import json
from types import SimpleNamespace as NS


from coding_agent.config import Config
from tests.conftest import make_config
from coding_agent.llm import VERIFY_GATE_MSG, CodingAgent
from coding_agent.session import Session

from tests.test_todos import tool_ctx


def cfg(tmp_path, **over):
    over.setdefault('stream_chat', False)
    over.setdefault('verify_gate', True)
    return make_config(tmp_path, **over)


class Scripted:
    """Provider returning queued contents in order; Exceptions propagate raw.
    NS items with tool_calls are returned as tool-call messages."""
    def __init__(self, responses):
        self.queued = list(responses)
        self.calls = []

    def chat(self, **kw):
        self.calls.append(kw)
        item = self.queued.pop(0)
        if isinstance(item, BaseException):            # covers KeyboardInterrupt too
            raise item
        if getattr(item, 'tool_calls', None):           # scripted tool-call message
            return NS(choices=[NS(message=item)], usage=NS(prompt_tokens=5, completion_tokens=1, total_tokens=6))
        msg = NS(content=item, tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=5, completion_tokens=1, total_tokens=6))


def tool_call(name, args):
    """Realistic chat-completions assistant message carrying one tool call."""
    return NS(content=None, tool_calls=[NS(id='c1', function=NS(name=name, arguments=json.dumps(args)))])


def make(tmp_path, provider, context=None, **over):
    agent = CodingAgent(cfg(tmp_path, **over), context, Session(tmp_path))
    agent.provider = provider
    return agent


def test_edit_tracking_flags_set_and_clear(tmp_path):
    c = tool_ctx(tmp_path)
    agent = make(tmp_path, Scripted([]), context=c)
    agent._dispatch('write_file', {'path': 'f.txt', 'content': 'x'})
    assert agent._edited_since_check is True
    agent._dispatch('write_todos', {'todos': [{'text': 't', 'status': 'done'}]})
    assert agent._edited_since_check is True          # todo writes are not edits
    agent._dispatch('run_tests', {})                  # no tests -> rc!=0 -> not a pass
    assert agent._edited_since_check is True
    agent._dispatch('run_command', {'command': 'echo no-check-here'})
    assert agent._edited_since_check is True          # non-check commands don't clear
    agent._dispatch('run_command', {'command': 'python3 -m unittest --help'})
    assert agent._edited_since_check is False         # passing check command clears


def test_gate_intercepts_finish_with_open_todos(tmp_path):
    c = tool_ctx(tmp_path, todos=[{'text': 'unfinished business', 'status': 'pending'}])
    p = Scripted(['all done!', 'ok, reconciled the list instead.'])
    agent = make(tmp_path, p, context=c)
    res = agent.run('wrap it up')
    assert len(p.calls) == 2
    gate_msgs = [m for m in p.calls[1]['messages'] if VERIFY_GATE_MSG in str(m.get('content'))]
    assert gate_msgs and gate_msgs[0]['role'] == 'user'
    assert res.text == 'ok, reconciled the list instead.'
    lines = [json.loads(l)['kind'] for l in agent.session.path.read_text().splitlines()]
    assert 'verify_gate' in lines


def test_gate_intercepts_unverified_edits_without_todos(tmp_path):
    c = tool_ctx(tmp_path)
    p = Scripted([tool_call('write_file', {'path': 'f.txt', 'content': 'change'}),
                  'edit done and verified, trust me', 'now actually checked'])
    agent = make(tmp_path, p, context=c)
    agent.run('make a change and finish')
    assert len(p.calls) == 3
    assert any(str(m.get('content')).startswith('[verify gate]') for m in p.calls[2]['messages'])
    assert res_text(agent) == 'now actually checked'

def res_text(agent):
    from coding_agent.conversation import AssistantMsg
    return isinstance(agent.history[-1], AssistantMsg) and agent.history[-1].text


def test_clean_state_never_triggers_gate(tmp_path):
    c = tool_ctx(tmp_path, todos=[{'text': 'all good', 'status': 'done'}])
    p = Scripted(['finished cleanly'])
    make(tmp_path, p, context=c).run('done task')
    assert len(p.calls) == 1


def test_gate_nudges_at_most_once_per_request(tmp_path):
    c = tool_ctx(tmp_path, todos=[{'text': 'still open', 'status': 'pending'}])
    p = Scripted(['first claim', 'second claim'])
    res = make(tmp_path, p, context=c).run('whatever')
    assert len(p.calls) == 2 and res.text == 'second claim'   # second finish passes


def test_gate_disabled_by_config(tmp_path):
    c = tool_ctx(tmp_path, todos=[{'text': 'open', 'status': 'pending'}])
    p = Scripted(['straight through'])
    make(tmp_path, p, context=c, verify_gate=False).run('go')
    assert len(p.calls) == 1


def test_verify_gate_config_default(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'k'); monkeypatch.setenv('OPENAI_MODEL', 'm')
    assert Config.from_env(str(tmp_path)).verify_gate is False
