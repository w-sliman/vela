import json
from types import SimpleNamespace as NS

import pytest

from coding_agent.config import Config
from coding_agent.llm import CodingAgent, PauseInterrupt
from coding_agent.session import Session

from tests.test_verify_gate import Scripted, tool_call


def cfg(tmp_path):
    return Config('test-key', 'http://localhost:9/v1', 'model-x', 'chat', tmp_path,
                  'prompt', 5000, 30000, 10, 10, 20, 100, 10000, False, False, False,
                  True, False, 0.0, 0.0, 128000, stream_chat=False)


def make(tmp_path, responses, context=None):
    p = Scripted(responses)
    a = CodingAgent(cfg(tmp_path), context, Session(tmp_path))
    a.provider = p
    return a, p


def fresh(tmp_path):
    return CodingAgent(cfg(tmp_path), None, Session(tmp_path))


def kinds(session):
    return [json.loads(l)['kind'] for l in session.path.read_text().splitlines()]


def test_repair_appends_outputs_for_dangling_chat_pairs(tmp_path):
    agent = fresh(tmp_path)
    agent.history = [
        {'role': 'user', 'content': 'go'},
        {'role': 'assistant', 'content': '',
         'tool_calls': [{'id': 'a1', 'type': 'function', 'function': {'name': 'x', 'arguments': '{}'}},
                        {'id': 'a2', 'type': 'function', 'function': {'name': 'y', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'a2', 'content': '{"ok":true}'},
    ]
    added = agent._repair_partial_turn()
    assert added == 1
    assert agent.history[-1] == {'role': 'tool', 'tool_call_id': 'a1', 'content': '[interrupted by user]'}


def test_repair_handles_responses_function_call_objects(tmp_path):
    fc = NS(type='function_call', call_id='k9', name='read_file')
    agent = fresh(tmp_path)
    agent.history = [{'role': 'user', 'content': 'go'}, fc]
    assert agent._repair_partial_turn() == 1
    assert agent.history[-1] == {'type': 'function_call_output', 'call_id': 'k9', 'output': '[interrupted by user]'}


def test_repair_noop_on_complete_history(tmp_path):
    agent = fresh(tmp_path)
    agent.history = [
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'z', 'type': 'function', 'function': {}}]},
        {'role': 'tool', 'tool_call_id': 'z', 'content': 'done'},
        {'role': 'assistant', 'content': 'all good'},
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
    roles = [h['role'] for h in a.history if isinstance(h, dict)]
    assert roles[-1] == 'tool'                       # dangling pair closed
    assert a.history[-1]['content'] == '[interrupted by user]'
    assert 'paused' in kinds(a.session)
    assert len(p.calls) == 1                         # died inside first batch


def test_resume_reenters_loop_with_nudge(tmp_path):
    a, p = make(tmp_path, ['first answer'])
    a.run('hello')
    n_before = len(a.history)
    p.queued.append('continued answer')              # provider for resume call
    r = a.resume()
    assert r.text == 'continued answer'
    assert any(h.get('role') == 'user' and str(h.get('content')).startswith('[paused]')
               for h in a.history[n_before:])


def test_resume_empty_history_raises(tmp_path):
    a, _ = make(tmp_path, [])
    with pytest.raises(RuntimeError, match='nothing to continue'):
        a.resume()
