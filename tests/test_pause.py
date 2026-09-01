import json

import pytest

from tests.conftest import make_config
from vela.llm import CodingAgent, PauseInterrupt
from vela.conversation import INTERRUPTED, AssistantMsg, ToolCall, ToolResult, UserMsg
from vela.session import Session

from tests.test_verify_gate import Scripted, tool_call


def cfg(tmp_path):
    return make_config(tmp_path, price_input_per_million=0.0, price_output_per_million=0.0)


def make(tmp_path, responses, context=None):
    p = Scripted(responses)
    a = CodingAgent(cfg(tmp_path), context, Session(tmp_path))
    a.provider = p
    return a, p


def fresh(tmp_path):
    return CodingAgent(cfg(tmp_path), None, Session(tmp_path))


def kinds(session):
    return [json.loads(l)['kind'] for l in session.path.read_text().splitlines()]


def test_repair_closes_only_the_unanswered_calls(tmp_path):
    """One assistant turn, two calls, one already answered: only the other is closed.

    Before history became transport-neutral this needed a test per wire format.
    """
    agent = fresh(tmp_path)
    agent.history = [
        UserMsg(text='go'),
        AssistantMsg(tool_calls=[ToolCall(id='a1', name='x', arguments='{}'),
                                 ToolCall(id='a2', name='y', arguments='{}')]),
        ToolResult(call_id='a2', output='{"ok":true}'),
    ]
    assert agent._repair_partial_turn() == 1
    assert agent.history[-1] == ToolResult(call_id='a1', output=INTERRUPTED, name='x')


def test_repair_closes_every_call_when_none_were_answered(tmp_path):
    agent = fresh(tmp_path)
    agent.history = [
        UserMsg(text='go'),
        AssistantMsg(tool_calls=[ToolCall(id='k1', name='read_file', arguments='{}'),
                                 ToolCall(id='k2', name='read_file', arguments='{}')]),
    ]
    assert agent._repair_partial_turn() == 2
    assert [r.call_id for r in agent.history[-2:]] == ['k1', 'k2']


def test_repair_noop_on_complete_history(tmp_path):
    agent = fresh(tmp_path)
    agent.history = [
        AssistantMsg(tool_calls=[ToolCall(id='z', name='n', arguments='{}')]),
        ToolResult(call_id='z', output='done'),
        AssistantMsg(text='all good'),
    ]
    assert agent._repair_partial_turn() == 0
    assert len(agent.history) == 3


def test_keyboard_interrupt_becomes_pause_and_repairs(tmp_path):
    from test_todos import tool_ctx
    c = tool_ctx(tmp_path)
    # Interrupt lands mid-tool-batch: dispatch itself is killed after the
    # assistant message with its tool_calls was already appended.
    a, p = make(tmp_path, [tool_call('write_file', {'path': 'f.txt', 'content': 'x'}), 'unreached'], context=c)
    def boom(name, args):
        raise KeyboardInterrupt
    a._dispatch = boom
    with pytest.raises(PauseInterrupt):
        a.run('start something')
    assert isinstance(a.history[-1], ToolResult)     # dangling pair closed
    assert a.history[-1].output == INTERRUPTED
    assert 'paused' in kinds(a.session)
    assert len(p.calls) == 1                         # died inside first batch


def test_resume_reenters_loop_with_nudge(tmp_path):
    a, p = make(tmp_path, ['first answer'])
    a.run('hello')
    n_before = len(a.history)
    p.queued.append('continued answer')              # provider for resume call
    r = a.resume()
    assert r.text == 'continued answer'
    assert any(isinstance(h, UserMsg) and h.text.startswith('[paused]')
               for h in a.history[n_before:])


def test_resume_empty_history_raises(tmp_path):
    a, _ = make(tmp_path, [])
    with pytest.raises(RuntimeError, match='nothing to continue'):
        a.resume()
