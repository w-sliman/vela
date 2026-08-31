import json

import pytest

from coding_agent.config import Config
from coding_agent.llm import CodingAgent
from coding_agent.context import _orphaned
from coding_agent.session import Session

from tests.test_compact import FakeProvider, hist


def make_agent(tmp_path, provider, **overrides):
    c = Config('test-key', 'http://localhost:9/v1', 'model-x', 'chat', tmp_path,
               'prompt', 5000, 30000, 10, 10, 20, 100, 10000,
               False, False, False, True, False, 0.0, 0.0, 128000, **overrides)
    agent = CodingAgent(c, None, Session(tmp_path))
    agent.provider = provider
    return agent


def test_auto_compact_triggers_at_threshold(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's', 'keep_last_turns': 2}))
    agent = make_agent(tmp_path, p)
    agent.history = hist(4)
    agent.metrics.last_input_tokens = 120000        # 94% of 128k
    agent._maybe_auto_compact()
    assert len(p.calls) == 1                        # summarizer was called
    assert not _orphaned(agent.history)
    assert agent.history[0].text.startswith('[Conversation summary]')


def test_no_trigger_below_threshold(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's'}))
    agent = make_agent(tmp_path, p)
    agent.history = hist(4)
    agent.metrics.last_input_tokens = 10000         # ~8%
    agent._maybe_auto_compact()
    assert p.calls == [] and len(agent.history) == 12


def test_disabled_or_unknown_context_never_triggers(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's'}))
    agent = make_agent(tmp_path, p, auto_compact=False)
    agent.history = hist(4)
    agent.metrics.last_input_tokens = 127999
    agent._maybe_auto_compact()
    assert p.calls == []

    p2 = FakeProvider(json.dumps({'summary': 's'}))
    agent2 = make_agent(tmp_path, p2)
    agent2.history = hist(4)                        # last_input_tokens stays 0
    agent2._maybe_auto_compact()
    assert p2.calls == []


def test_only_one_attempt_per_request(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's', 'keep_last_turns': 1}))
    agent = make_agent(tmp_path, p)
    agent.history = hist(4)
    agent.metrics.last_input_tokens = 120000
    agent._maybe_auto_compact()
    n = len(p.calls)
    agent.metrics.last_input_tokens = 127000        # still over threshold
    agent._maybe_auto_compact()
    assert len(p.calls) == n                        # cooldown flag blocks retry


def test_threshold_respected_exactly(tmp_path):
    p = FakeProvider(json.dumps({'summary': 's'}))
    c = Config('k', None, 'm', 'chat', tmp_path, 'prompt', 5000, 30000, 10, 10,
               20, 100, 10000, False, False, False, True, False,
               0.0, 0.0, 1000, 2, True, 80)
    agent = CodingAgent(c, None, Session(tmp_path)); agent.provider = p
    agent.history = hist(4)
    agent.metrics.last_input_tokens = 799           # just below 80%
    agent._maybe_auto_compact()
    assert p.calls == []
    agent.metrics.last_input_tokens = 800           # exactly 80%
    agent.compact_calls = 0
    agent._maybe_auto_compact()
    assert len(p.calls) == 1


def test_with_retries_succeeds_after_transient_failures(tmp_path):
    agent = make_agent(tmp_path, FakeProvider(''))
    attempts = {'n': 0}
    def flaky():
        attempts['n'] += 1
        if attempts['n'] < 3:
            raise RuntimeError('boom')
        return 'ok'
    result = agent._with_retries(flaky, delays=[0.0, 0.0, 0.0])
    assert result == 'ok' and attempts['n'] == 3


def test_with_retries_raises_after_exhaustion(tmp_path):
    agent = make_agent(tmp_path, FakeProvider(''))
    attempts = {'n': 0}
    def always():
        attempts['n'] += 1
        raise RuntimeError('down')
    with pytest.raises(RuntimeError):
        agent._with_retries(always, delays=[0.0, 0.0])
    assert attempts['n'] == 2                       # every delay slot gets one attempt


def test_config_defaults_and_overrides(monkeypatch, tmp_path):
    from coding_agent.config import Config as C
    monkeypatch.setenv('OPENAI_API_KEY', 'k'); monkeypatch.setenv('OPENAI_MODEL', 'm')
    c = C.from_env(str(tmp_path))
    assert c.request_retries == 2 and c.auto_compact is True and c.auto_compact_pct == 80
    monkeypatch.setenv('CODER_REQUEST_RETRIES', '5')
    monkeypatch.setenv('CODER_AUTO_COMPACT', '0')
    monkeypatch.setenv('CODER_AUTO_COMPACT_PCT', '90')
    c2 = C.from_env(str(tmp_path))
    assert (c2.request_retries, c2.auto_compact, c2.auto_compact_pct) == (5, False, 90)


