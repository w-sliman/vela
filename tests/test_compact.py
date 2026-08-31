import json
from types import SimpleNamespace as NS

import pytest

from coding_agent.config import Config
from coding_agent.llm import CodingAgent
from coding_agent.context import _orphaned
from coding_agent.conversation import AssistantMsg, ToolCall, ToolResult, UserMsg
from coding_agent.session import Session


def cfg(tmp_path):
    return Config('test-key', 'http://localhost:9/v1', 'model-x', 'chat', tmp_path,
                  'prompt', 5000, 30000, 10, 10, 20, 100, 10000, False, False, False, True, False)


def hist(n=4):
    """n complete user-turns, each with an assistant tool call + its result."""
    h = []
    for i in range(n):
        h.append(UserMsg(text=f'problem {i}'))
        h.append(AssistantMsg(tool_calls=[ToolCall(id=f'c{i}', name='read_file', arguments='{}')]))
        h.append(ToolResult(call_id=f'c{i}', output=f'result {i}'))
    return h


class FakeProvider:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def chat(self, **kw):
        if self.error:
            raise self.error
        self.calls.append(kw)
        usage = NS(prompt_tokens=50, completion_tokens=20, total_tokens=70)
        msg = NS(content=self.content, tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=usage)


def agent_with(tmp_path, provider):
    agent = CodingAgent(cfg(tmp_path), None, Session(tmp_path))
    agent.provider = provider
    agent.history = hist(4)
    return agent


def test_compact_prepends_summary_and_keeps_last_turn_verbatim(tmp_path):
    p = FakeProvider(json.dumps({'summary': 'did stuff', 'keep_last_turns': 1}))
    agent = agent_with(tmp_path, p)
    info = agent.compact()
    assert info['compacted'] and info['turns_removed'] == 3 and info['turns_kept'] == 1
    h = agent.history
    assert isinstance(h[0], UserMsg) and h[0].text.startswith('[Conversation summary]')
    assert 'did stuff' in h[0].text
    # last turn intact: user + assistant(tool call) + result
    assert [type(m).__name__ for m in h[1:]] == ['UserMsg', 'AssistantMsg', 'ToolResult']
    assert h[1].text == 'problem 3'
    assert not _orphaned(h)


def test_focus_goes_to_prompt_and_summary_header(tmp_path):
    p = FakeProvider(json.dumps({'summary': 'stuff happened', 'keep_last_turns': 2}))
    agent = agent_with(tmp_path, p)
    agent.compact(focus='the migration plan')
    sent = p.calls[0]['messages'][1]['content']
    assert 'Focus for this compaction: the migration plan' in sent
    assert 'focus: the migration plan' in agent.history[0].text


def test_keep_is_clamped_to_sane_range(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's', 'keep_last_turns': 99}))
    agent = agent_with(tmp_path, p)
    info = agent.compact()
    assert info['turns_kept'] == 3          # clamped to KEEP_MAX(5) then to available turns
    assert info['turns_removed'] == 1


def test_non_json_prose_still_works_as_summary(tmp_path):
    p = FakeProvider('We fixed the parser and updated tests.')
    agent = agent_with(tmp_path, p)
    info = agent.compact()
    assert info['compacted'] and info['turns_kept'] == 2   # DEFAULT_KEEP_TURNS
    assert 'fixed the parser' in agent.history[0].text


def test_transport_failure_leaves_history_untouched(tmp_path):
    p = FakeProvider(error=RuntimeError('endpoint down'))
    agent = agent_with(tmp_path, p)
    before = list(agent.history)
    with pytest.raises(RuntimeError):
        agent.compact()
    assert agent.history == before


def test_short_history_refuses(tmp_path):
    agent = agent_with(tmp_path, FakeProvider('{}'))
    agent.history = hist(2)
    info = agent.compact()
    assert info['compacted'] is False
    assert agent.provider.calls == []       # no summarizer call made


def test_usage_and_compact_events_journaled(tmp_path):
    session = Session(tmp_path)
    agent = CodingAgent(cfg(tmp_path), None, session)
    agent.provider = FakeProvider(json.dumps({'summary': 's', 'keep_last_turns': 2}))
    agent.history = hist(4)
    agent.compact()
    kinds = [e['kind'] for e in session.recent(20)]
    assert 'usage' in kinds and 'compact' in kinds


