import json
from types import SimpleNamespace as NS

from vela.config import Config
from tests.conftest import make_config
from vela.llm import CodingAgent
from vela.session import Session
from vela.tools import ToolContext, diff_todos, dispatch, normalize_todos
from vela.ui import DebugUI
from vela.workspace import Workspace
from vela.shell import Shell
from vela.git import Git
from vela.browser import Browser
from vela.github import GitHub
from vela.sandbox import DockerSandbox


def cfg(tmp_path, **over):
    over.setdefault('stream_chat', False)
    return make_config(tmp_path, **over)


def tool_ctx(tmp_path, todos=None, **over):
    c = make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto', **over)
    ctx = ToolContext(c, Workspace(tmp_path), Shell(c), lambda *_: True, Git(tmp_path),
                      Browser(), GitHub(), DockerSandbox(tmp_path))
    ctx.todos = todos
    return ctx


class FakeProvider:
    def __init__(self):
        self.calls = []

    def chat(self, **kw):
        self.calls.append(kw)
        msg = NS(content='ok', tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=10, completion_tokens=5, total_tokens=15))


def make_agent(tmp_path, provider, context=None, **over):
    agent = CodingAgent(cfg(tmp_path, **over), context, Session(tmp_path))
    agent.provider = provider
    return agent


# --- validation ---

def test_normalize_caps_strips_and_defaults():
    raw = [{'text': f'  step {i} ', 'status': s} for i, s in
           enumerate(['done', 'IN_PROGRESS', 'weird', 'pending'] * 5)]
    raw.append('plain string item'); raw.append({'no_text': 1}); raw.append({'text': 'step 0'})
    out = normalize_todos(raw)
    assert len(out) == 12                                   # hard cap
    assert out[1] == {'text': 'step 1', 'status': 'in_progress'}
    assert out[2]['status'] == 'pending'                    # unknown -> pending
    assert all(t['text'] == t['text'].strip() for t in out)
    assert not any(t['text'] == 'step 0' and out.count(t) > 1 for t in out)

def test_normalize_rejects_garbage_and_dedupes():
    assert normalize_todos('nope') == []
    out = normalize_todos([{'text': 'same'}, {'text': 'SAME'}, {'text': ''}, None, 42])
    assert [t['text'] for t in out] == ['same']


# --- diffing ---

def test_diff_tracks_lifecycle_transitions():
    old = [{'text': 'a', 'status': 'pending'}, {'text': 'b', 'status': 'done'},
           {'text': 'c', 'status': 'in_progress'}]
    new = [{'text': 'a', 'status': 'done'}, {'text': 'b', 'status': 'pending'},
           {'text': 'd', 'status': 'in_progress'}]
    d = diff_todos(old, new)
    assert d['completed'] == ['a']
    assert d['reopened'] == ['b']                           # was done, now not
    assert d['added'] == ['d']
    assert d['removed'] == ['c']
    assert d['in_progress'] == ['d']

def test_diff_identical_lists_is_all_empty():
    items = [{'text': 'x', 'status': 'pending'}]
    assert all(v == [] for v in diff_todos(items, items).values())


# --- tool roundtrip ---

def test_write_todos_stores_state_and_returns_diff(tmp_path):
    c = tool_ctx(tmp_path)
    r1 = json.loads(dispatch(c, 'write_todos', {'todos': [
        {'text': 'inspect', 'status': 'done'}, {'text': 'fix', 'status': 'in_progress'}]}))
    assert r1['status'] == 'completed' and c.todos[0]['text'] == 'inspect'
    assert r1['diff']['added'] == ['inspect', 'fix']
    r2 = json.loads(dispatch(c, 'write_todos', {'todos': [
        {'text': 'inspect', 'status': 'done'}, {'text': 'fix', 'status': 'done'},
        {'text': 'test', 'status': 'pending'}]}))
    assert r2['diff']['completed'] == ['fix'] and r2['diff']['added'] == ['test']

def test_write_todos_missing_argument_errors_cleanly(tmp_path):
    c = tool_ctx(tmp_path)
    out = json.loads(dispatch(c, 'write_todos', {}))
    assert out['status'] == 'error' and 'todos' in out['message']

def test_tool_schema_declares_write_todos():
    from vela.tools import _REQ
    assert 'write_todos' in _REQ and _REQ['write_todos'] == ('todos',)


# --- injection into model payloads ---

def test_todo_queue_injected_after_memory_block(tmp_path):
    from tests.test_memory import _seed
    _seed(tmp_path)
    ctx = tool_ctx(tmp_path, todos=[{'text': 'fix bug', 'status': 'in_progress'},
                                    {'text': 'run tests', 'status': 'pending'}])
    p = FakeProvider()
    agent = make_agent(tmp_path, p, context=ctx)
    agent.run('fix the failing tests please')
    msgs = p.calls[0]['messages']
    contents = [str(m.get('content')) for m in msgs]
    todo_i = next(i for i, c in enumerate(contents) if '[current todos]' in c)
    assert todo_i == len(msgs) - 1                          # todos block sits last
    assert '[>] fix bug' in contents[todo_i] and '[ ] run tests' in contents[todo_i]

def test_todo_injection_disabled_via_config(tmp_path):
    ctx = tool_ctx(tmp_path, todos=[{'text': 'only step', 'status': 'pending'}])
    p = FakeProvider()
    make_agent(tmp_path, p, context=ctx, show_todos=False).run('do it')
    assert not any('[current todos]' in str(m.get('content')) for m in p.calls[0]['messages'])

def test_empty_or_absent_todos_inject_nothing(tmp_path):
    ctx = tool_ctx(tmp_path)
    p = FakeProvider()
    make_agent(tmp_path, p, context=ctx).run('quick question')
    assert not any('[current todos]' in str(m.get('content')) for m in p.calls[0]['messages'])


# --- journaling + properties ---

def test_dispatch_journals_todos_updated_with_diff(tmp_path):
    c = tool_ctx(tmp_path)
    agent = make_agent(tmp_path, FakeProvider(), context=c)
    agent._dispatch('write_todos', {'todos': [{'text': 'step one', 'status': 'pending'}]})
    lines = [json.loads(l) for l in agent.session.path.read_text().splitlines()]
    evt = [e for e in lines if e['kind'] == 'todos_updated']
    assert evt and evt[-1]['payload']['diff']['added'] == ['step one']

def test_agent_todos_property_mirrors_context(tmp_path):
    ctx = tool_ctx(tmp_path, todos=[{'text': 't', 'status': 'pending'}])
    agent = make_agent(tmp_path, FakeProvider(), context=ctx)
    assert agent.todos == [{'text': 't', 'status': 'pending'}]
    bare = make_agent(tmp_path, FakeProvider())             # no ToolContext
    assert bare.todos == []


# --- UI rendering ---

def test_debugui_renders_todos_always_visible():
    from rich.console import Console
    import io
    ui = DebugUI(console=Console(file=io.StringIO(), force_terminal=False), enabled=False)
    from vela.events import AgentEvent
    ui.event(AgentEvent('todos', 'todo list updated',
                        {'items': [{'text': 'a', 'status': 'done'},
                                   {'text': 'b', 'status': 'in_progress'}]}))
    out = ui.console.file.getvalue()
    assert 'todos' in out and 'a' in out and 'b' in out
    ui.event(AgentEvent('todos', 'todo list updated', {'items': []}))
    assert 'cleared' in ui.console.file.getvalue()


# --- resume digest integration ---

def test_resume_digest_carries_open_todos_only(tmp_path):
    from vela.resume import build_digest
    from datetime import datetime, timezone as tz
    d = tmp_path / '.vela' / 'sessions'; d.mkdir(parents=True)
    events = [
        {'timestamp': datetime.now(tz.utc).isoformat(), 'kind': 'user', 'payload': {'text': 'big task'}},
        {'timestamp': datetime.now(tz.utc).isoformat(), 'kind': 'todos_updated',
         'payload': {'todos': [{'text': 'done part', 'status': 'done'},
                               {'text': 'half way', 'status': 'in_progress'},
                               {'text': 'not started', 'status': 'pending'}], 'diff': {}}},
    ]
    (d / 'trace.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    text = build_digest(d / 'trace.jsonl')['text']
    assert 'Open todos left by that session' in text
    assert 'half way' in text and 'not started' in text
    assert 'done part' not in text.split('Open todos')[1]


# --- prompt + config ---

def test_system_prompt_documents_todo_behavior():
    from vela.prompts import SYSTEM_PROMPT
    assert 'write_todos' in SYSTEM_PROMPT and 'in_progress' in SYSTEM_PROMPT

def test_show_todos_config_default_and_override(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'k'); monkeypatch.setenv('OPENAI_MODEL', 'm')
    assert Config.from_env(str(tmp_path)).show_todos is True
    monkeypatch.setenv('VELA_TODOS', '0')
    assert Config.from_env(str(tmp_path)).show_todos is False
