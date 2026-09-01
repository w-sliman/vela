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
    ELIDED_STATUS,
    ContextBudget,
    blocks,
    elidable,
    orphaned,
    payload_chars,
)
from coding_agent.config import Config
from coding_agent.conversation import AssistantMsg, ToolCall, ToolResult, UserMsg
from coding_agent.llm import CodingAgent
from coding_agent.session import Session
from coding_agent.transports import ChatTransport
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


def test_summarizer_failure_still_reduces_without_losing_turns(tmp_path):
    """Losing the summarizer degrades the conversation; it must not fail the request.

    The next rung down is elision, not dropping: the bodies of tool results are given
    up before whole turns are, so a dead summarizer costs re-readable content rather
    than the shape of the conversation.
    """
    p = FakeProvider(error=RuntimeError('summarizer down'))
    agent = make_agent(tmp_path, p, context_window_tokens=3000)
    agent.history = fat_hist(6)
    before_items, before_chars = len(agent.history), payload_chars(agent._fit_to_budget())
    assert before_chars < payload_chars(ChatTransport(None, 'm', '').encode(fat_hist(6)))
    assert len(agent.history) == before_items, 'elision reduces in place; no turn is lost'
    assert not orphaned(agent.history)
    methods = [json.loads(x)['payload'].get('method')
               for x in agent.session.path.read_text().splitlines()
               if json.loads(x)['kind'] == 'budget_reduced']
    assert 'elide_result' in methods and 'drop_oldest' not in methods


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


# ── retries only repeat what a retry could fix ──────────────────────────────

class _Status(Exception):
    def __init__(self, status): super().__init__(f'status {status}'); self.status_code = status


def test_deterministic_rejections_are_not_retried(tmp_path):
    """A 400 is a verdict on the request: resending it unchanged only delays the
    transport fallback that can actually fix it."""
    agent = make_agent(tmp_path, FakeProvider(1))
    calls = []

    def bad():
        calls.append(1); raise _Status(400)

    with pytest.raises(_Status):
        agent._with_retries(bad, delays=[0.0, 0.0, 0.0])
    assert len(calls) == 1


@pytest.mark.parametrize('status', [429, 500, 503, 408])
def test_transient_statuses_are_retried(tmp_path, status):
    agent = make_agent(tmp_path, FakeProvider(1))
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3: raise _Status(status)
        return 'ok'

    assert agent._with_retries(flaky, delays=[0.0, 0.0, 0.0]) == 'ok'
    assert len(calls) == 3


def test_errors_without_a_status_are_treated_as_transient(tmp_path):
    agent = make_agent(tmp_path, FakeProvider(1))
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2: raise ConnectionError('reset')
        return 'ok'

    assert agent._with_retries(flaky, delays=[0.0, 0.0]) == 'ok'


# ── elision: the rung that makes a single oversized turn reducible ───────────

def _one_turn(results=4, size=8000):
    """One user request that fans out into a long tool chain — the agentic shape."""
    out = [UserMsg(text='read every file and summarise each one')]
    for i in range(results):
        out.append(AssistantMsg(tool_calls=[ToolCall(id=f'c{i}', name='read_file', arguments='{}')]))
        out.append(ToolResult(call_id=f'c{i}', output='x' * size, name='read_file'))
    return out


def test_elision_takes_the_largest_result_and_keeps_its_pair():
    history = _one_turn(results=3, size=1000)
    history[4].output = 'y' * 9000                     # make one clearly the biggest
    out, freed = ContextBudget(1000).elide_largest_result(history)
    assert freed > 8000
    assert len(out) == len(history) and not orphaned(out)
    assert ELIDED_STATUS in out[4].output and out[4].call_id == history[4].call_id


def test_elision_is_idempotent_so_reduction_cannot_spin():
    b, history = ContextBudget(1000), _one_turn(results=1, size=9000)
    for _ in range(5):
        history, _ = b.elide_largest_result(history)
    assert not elidable(history)
    assert b.elide_largest_result(history)[1] == 0


def test_small_results_are_not_worth_eliding():
    """The stub would cost about what the body does, so this must report no progress."""
    assert ContextBudget(1000).elide_largest_result(_one_turn(results=2, size=80))[1] == 0


def test_a_single_block_with_a_fat_result_is_still_reducible():
    b = ContextBudget(1000)
    fat = [AssistantMsg(tool_calls=[ToolCall(id='c', name='read_file', arguments='{}')]),
           ToolResult(call_id='c', output='x' * 9000, name='read_file')]
    assert len(blocks(fat)) == 1
    assert b.reducible(fat) is True, 'one block, but plenty of bulk to shed'


def test_single_turn_overflow_converges_by_eliding_not_dropping(tmp_path):
    """The real-world failure: one request, one turn, a window far too small.

    Compaction has no older turns to summarize, so this used to fall straight to
    dropping the oldest block — which cannot help when the bulk is in the newest one.
    """
    p = FakeProvider(json.dumps({'summary': 's'}))
    agent = make_agent(tmp_path, p, context_window_tokens=8000)
    agent.history = _one_turn(results=4, size=8000)
    assert not agent.budget.fits(agent.transport.encode(agent.history)), 'precondition'

    payload = agent._fit_to_budget()

    assert agent.budget.fits(payload), 'reduction must actually converge'
    assert not orphaned(agent.history)
    assert [i.text for i in agent.history if isinstance(i, UserMsg)], 'the request survived'
    methods = [json.loads(x)['payload'].get('method')
               for x in agent.session.path.read_text().splitlines()
               if json.loads(x)['kind'] == 'budget_reduced']
    assert 'elide_result' in methods


def test_forced_reduction_after_a_rejection_walks_the_whole_ladder(tmp_path):
    """A rejection is a reason to reduce, not a reason to reduce badly: this used to
    bypass compaction and elision and drop the oldest block outright."""
    p = FakeProvider(json.dumps({'summary': 's'}))
    agent = make_agent(tmp_path, p, context_window_tokens=200000)   # believes it all fits
    agent.history = _one_turn(results=3, size=9000)

    assert agent._force_reduction() is True

    methods = [json.loads(x)['payload'].get('method')
               for x in agent.session.path.read_text().splitlines()
               if json.loads(x)['kind'] == 'budget_reduced']
    assert methods and methods[0] == 'elide_result' and 'drop_oldest' not in methods
    assert not orphaned(agent.history)
