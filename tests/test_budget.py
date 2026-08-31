"""Context budget: measure the outgoing payload, reduce until it fits.

The old design guessed three ways — a char-budget trim every turn, a
once-per-request compaction reading the *previous* call's usage, and a manual
`/compact`. These tests pin the replacement: one owner, measured on the payload
actually being sent, with summarizing preferred over dropping.
"""
import json

import pytest

from coding_agent.budget import (
    DEFAULT_CHARS_PER_TOKEN,
    ContextBudget,
    blocks,
    orphaned,
    payload_chars,
)
from coding_agent.config import Config
from coding_agent.conversation import AssistantMsg, ToolCall, ToolResult, UserMsg
from coding_agent.llm import CodingAgent
from coding_agent.session import Session
from tests.conftest import make_config
from tests.test_compact import FakeProvider, hist


def fat_hist(turns=6, size=2000):
    """A history whose tool results are big enough to need real reduction."""
    out = hist(turns)
    for item in out:
        if isinstance(item, ToolResult):
            item.output = 'x' * size
    return out


def make_agent(tmp_path, provider, **overrides):
    agent = CodingAgent(make_config(tmp_path, **overrides), None, Session(tmp_path))
    agent.provider = provider
    return agent


# ── measurement ─────────────────────────────────────────────────────────────

def test_payload_chars_measures_the_encoded_request():
    """The measurement primitive: whatever the transport built, serialized."""
    small = payload_chars([{'role': 'user', 'content': 'hi'}])
    large = payload_chars([{'role': 'user', 'content': 'x' * 1000}])
    assert small < large and large > 1000


def test_payload_chars_handles_canonical_items_too():
    """Dataclasses must not fall back to repr() and mis-size the payload."""
    assert payload_chars([UserMsg(text='y' * 500)]) > 500


def test_limit_reserves_headroom_for_the_reply():
    """The limit is the window minus room for the answer — not a tuned percentage."""
    assert ContextBudget(1000, reserve_tokens=200).limit == 800


def test_reserve_defaults_to_an_eighth_of_the_window():
    assert ContextBudget(128000).reserve == 16000


def test_reserve_has_a_floor_for_small_windows():
    assert ContextBudget(4000).reserve == 1024


def test_reserve_can_never_swallow_the_whole_window():
    """A limit of 0 means "unknown window"; a tiny window must not fake that and
    disable reduction exactly where it is needed most."""
    b = ContextBudget(500)
    assert b.reserve == 250 and b.limit == 250
    assert ContextBudget(500, reserve_tokens=100_000).limit == 250


def test_unknown_window_means_no_limit_and_everything_fits():
    """A window we cannot know must not silently reduce the conversation."""
    b = ContextBudget(0)
    assert b.limit == 0
    assert b.fits([{'role': 'user', 'content': 'x' * 100_000}]) is True


def test_fits_compares_the_estimate_against_the_limit():
    b = ContextBudget(1000, reserve_tokens=0, chars_per_token=1.0)
    assert b.fits([{'c': 'x' * 500}]) is True
    assert b.fits([{'c': 'x' * 5000}]) is False


# ── calibration turns the estimate into a measurement ───────────────────────

def test_calibration_learns_the_real_ratio_from_reported_usage():
    b = ContextBudget(128000)
    assert b.chars_per_token == DEFAULT_CHARS_PER_TOKEN
    b.calibrate(payload_chars_sent=8000, reported_input_tokens=2000)
    assert b.chars_per_token == 4.0 and b.calibrations == 1


def test_calibration_ignores_absurd_ratios_and_missing_usage():
    """A nonsense ratio must not poison every later measurement."""
    b = ContextBudget(128000)
    for chars, tokens in [(1_000_000, 1), (10, 1000), (0, 100), (1000, None)]:
        b.calibrate(chars, tokens)
    assert b.chars_per_token == DEFAULT_CHARS_PER_TOKEN and b.calibrations == 0


# ── reduction: summarize first, drop only as fallback ───────────────────────

def test_drop_oldest_removes_a_whole_block_never_half_a_pair():
    history = hist(3)
    out, dropped = ContextBudget(1000).drop_oldest(history)
    assert dropped == 1 and not orphaned(out)
    assert len(blocks(out)) == len(blocks(history)) - 1


def test_drop_oldest_refuses_to_empty_the_conversation():
    history = [UserMsg(text='only thing left')]
    out, dropped = ContextBudget(1000).drop_oldest(history)
    assert dropped == 0 and out == history


def test_reducible_is_false_once_one_block_remains():
    b = ContextBudget(1000)
    assert b.reducible(hist(2)) is True
    assert b.reducible([UserMsg(text='x')]) is False


def test_multi_result_call_moves_as_one_block():
    history = [
        AssistantMsg(tool_calls=[ToolCall(id='c1', name='n', arguments='{}')]),
        ToolResult(call_id='c1', output='a'),
        ToolResult(call_id='c1', output='b'),
        UserMsg(text='next'),
    ]
    assert [len(b) for b in blocks(history)] == [3, 1]


# ── pair integrity: trimming may never orphan a tool result ─────────────────

def _call(i, results=1):
    out = [AssistantMsg(tool_calls=[ToolCall(id=f'c{i}', name='read_file', arguments='{}')])]
    out += [ToolResult(call_id=f'c{i}', output='r' * 200) for _ in range(results)]
    return out


def test_blocks_never_split_pairs():
    history = [UserMsg(text='u'),
               AssistantMsg(tool_calls=[ToolCall(id='a', name='n', arguments='{}')]),
               ToolResult(call_id='a', output='r')]
    assert [len(b) for b in blocks(history)] == [1, 2]


def test_blocks_leaves_plain_turns_alone():
    history = [UserMsg(text='u'), AssistantMsg(text='answer'), UserMsg(text='u2')]
    assert [len(b) for b in blocks(history)] == [1, 1, 1]


def test_repeated_dropping_never_orphans():
    history = [UserMsg(text='u')]
    for i in range(20):
        history += _call(i, results=2)
    b = ContextBudget(128000)
    while b.reducible(history):
        history, dropped = b.drop_oldest(history)
        assert dropped and not orphaned(history)


def test_orphan_detector_catches_a_broken_history():
    """Guards the guard: orphaned must actually report an orphan."""
    assert orphaned([ToolResult(call_id='missing', output='r')]) is True


# ── the loop fits the payload before sending ────────────────────────────────

def test_oversized_history_is_compacted_before_the_request(tmp_path):
    p = FakeProvider(json.dumps({'summary': 'earlier work', 'keep_last_turns': 1}))
    agent = make_agent(tmp_path, p, context_window_tokens=3000)
    agent.history = fat_hist(6)
    assert not agent.budget.fits(agent.transport.encode(agent.history)), 'precondition'
    payload = agent._fit_to_budget()
    assert agent.budget.fits(payload)
    assert agent.history[0].text.startswith('[Conversation summary]')
    assert not orphaned(agent.history)


def test_a_fitting_history_is_left_alone(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's'}))
    agent = make_agent(tmp_path, p, context_window_tokens=128000)
    agent.history = hist(2)
    before = list(agent.history)
    agent._fit_to_budget()
    assert agent.history == before and p.calls == []


def test_reduction_repeats_until_it_fits(tmp_path):
    """One pass is not assumed to be enough — this is a loop, not a single shot.

    keep_last_turns=5 means each compaction frees very little, so fitting requires
    several rounds and eventually the dropping fallback.
    """
    p = FakeProvider(json.dumps({'summary': 'x', 'keep_last_turns': 5}))
    agent = make_agent(tmp_path, p, context_window_tokens=3000)
    agent.history = fat_hist(8)
    payload = agent._fit_to_budget()
    assert agent.budget.fits(payload)
    assert not orphaned(agent.history)


def test_reduction_stops_when_nothing_more_can_be_freed(tmp_path):
    """An unfittable payload terminates rather than spinning; the send then fails
    loudly at the provider, which is the honest outcome."""
    p = FakeProvider(json.dumps({'summary': 's', 'keep_last_turns': 1}))
    agent = make_agent(tmp_path, p, context_window_tokens=100)   # smaller than the system prompt
    agent.history = fat_hist(4)
    payload = agent._fit_to_budget()                              # must return, not hang
    assert not agent.budget.reducible(agent.history)
    assert payload is not None


def test_summarizer_failure_falls_back_to_dropping(tmp_path):
    """Losing the summarizer degrades the conversation; it must not fail the request."""
    p = FakeProvider(error=RuntimeError('summarizer down'))
    agent = make_agent(tmp_path, p, context_window_tokens=3000)
    agent.history = fat_hist(6)
    before = len(agent.history)
    agent._fit_to_budget()
    assert len(agent.history) < before
    assert not orphaned(agent.history)
    kinds = [json.loads(x)['kind'] for x in agent.session.path.read_text().splitlines()]
    assert 'budget_reduced' in kinds


def test_auto_compact_disabled_skips_reduction_entirely(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's'}))
    agent = make_agent(tmp_path, p, context_window_tokens=3000, auto_compact=False)
    agent.history = fat_hist(6)
    before = list(agent.history)
    agent._fit_to_budget()
    assert agent.history == before and p.calls == []


def test_reduction_is_journaled(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's', 'keep_last_turns': 1}))
    agent = make_agent(tmp_path, p, context_window_tokens=3000)
    agent.history = fat_hist(6)
    agent._fit_to_budget()
    events = [json.loads(x) for x in agent.session.path.read_text().splitlines()]
    reduced = [e for e in events if e['kind'] == 'budget_reduced']
    assert reduced and reduced[0]['payload']['method'] == 'compact'
    assert reduced[0]['payload']['limit'] == agent.budget.limit


# ── retries (unchanged behaviour, kept alongside the budget) ────────────────

def test_with_retries_succeeds_after_transient_failures(tmp_path):
    agent = make_agent(tmp_path, FakeProvider(''))
    attempts = {'n': 0}

    def flaky():
        attempts['n'] += 1
        if attempts['n'] < 3:
            raise RuntimeError('boom')
        return 'ok'

    assert agent._with_retries(flaky, delays=[0.0, 0.0, 0.0]) == 'ok'
    assert attempts['n'] == 3


def test_with_retries_raises_after_exhaustion(tmp_path):
    agent = make_agent(tmp_path, FakeProvider(''))
    attempts = {'n': 0}

    def always():
        attempts['n'] += 1
        raise RuntimeError('down')

    with pytest.raises(RuntimeError):
        agent._with_retries(always, delays=[0.0, 0.0])
    assert attempts['n'] == 2


# ── config ──────────────────────────────────────────────────────────────────

def test_config_defaults_and_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'k')
    monkeypatch.setenv('OPENAI_MODEL', 'm')
    c = Config.from_env(str(tmp_path))
    assert c.request_retries == 2 and c.auto_compact is True and c.reply_reserve_tokens == 0
    monkeypatch.setenv('CODER_REQUEST_RETRIES', '5')
    monkeypatch.setenv('CODER_AUTO_COMPACT', '0')
    monkeypatch.setenv('CODER_REPLY_RESERVE_TOKENS', '4096')
    c2 = Config.from_env(str(tmp_path))
    assert (c2.request_retries, c2.auto_compact, c2.reply_reserve_tokens) == (5, False, 4096)


def test_agent_budget_uses_the_configured_reserve(tmp_path):
    agent = make_agent(tmp_path, FakeProvider(''), context_window_tokens=10000,
                       reply_reserve_tokens=3000)
    assert agent.budget.limit == 7000
