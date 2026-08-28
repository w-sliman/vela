import json
import pytest
from coding_agent.config import Config
from coding_agent.tools import ToolContext, dispatch
from coding_agent.workspace import Workspace
from coding_agent.shell import Shell
from coding_agent.git import Git
from coding_agent.browser import Browser
from coding_agent.github import GitHub
from coding_agent.sandbox import DockerSandbox

def make_ctx(tmp_path, callback, approval_edits=True):
    c = Config('test-key', 'http://localhost:9/v1', 'model-x', 'chat', tmp_path,
               'prompt', 5000, 30000, 10, 10, 20, 100, 10000, False, False, False,
               True, False, 0.0, 0.0, 128000, approval_edits=approval_edits)
    ctx = ToolContext(c, Workspace(tmp_path), Shell(c), callback, Git(tmp_path),
                      Browser(), GitHub(), DockerSandbox(tmp_path))
    ctx.todos = None
    return ctx

def test_edit_approval_disabled_by_default(tmp_path):
    # Enabled=False (default config from env) -> does not call approval callback
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return True
    ctx = make_ctx(tmp_path, cb, approval_edits=False)
    
    res = json.loads(dispatch(ctx, 'write_file', {'path': 'f.txt', 'content': 'hello'}))
    assert res['status'] == 'completed'
    assert not called
    assert (tmp_path / 'f.txt').read_text() == 'hello'

def test_edit_approval_granted(tmp_path):
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return True
    ctx = make_ctx(tmp_path, cb, approval_edits=True)
    
    res = json.loads(dispatch(ctx, 'write_file', {'path': 'f.txt', 'content': 'hello'}))
    assert res['status'] == 'completed'
    assert len(called) == 1
    assert called[0][0] == 'edit f.txt'
    assert '+hello' in called[0][1] # shows diff content

def test_edit_approval_denied_write_file(tmp_path):
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return False # Denied!
    ctx = make_ctx(tmp_path, cb, approval_edits=True)
    
    res = json.loads(dispatch(ctx, 'write_file', {'path': 'f.txt', 'content': 'hello'}))
    assert res['status'] == 'denied'
    assert res['reason'] == 'user declined this edit'
    assert not (tmp_path / 'f.txt').exists()

def test_edit_approval_denied_replace_text(tmp_path):
    (tmp_path / 'f.txt').write_text('original text')
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return False # Denied!
    ctx = make_ctx(tmp_path, cb, approval_edits=True)
    
    res = json.loads(dispatch(ctx, 'replace_text', {'path': 'f.txt', 'old': 'original text', 'new': 'replaced text'}))
    assert res['status'] == 'denied'
    assert res['reason'] == 'user declined this edit'
    assert (tmp_path / 'f.txt').read_text() == 'original text' # Unchanged
