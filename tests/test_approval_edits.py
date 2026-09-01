import json
from tests.conftest import make_config
from vela.tools import ToolContext, dispatch
from vela.workspace import Workspace
from vela.shell import Shell
from vela.git import Git
from vela.browser import Browser
from vela.github import GitHub
from vela.sandbox import DockerSandbox

def make_ctx(tmp_path, callback, approval_edits=True):
    c = make_config(tmp_path, price_input_per_million=0.0, price_output_per_million=0.0, approval_edits=approval_edits)
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

def test_edit_approval_denied_apply_patch(tmp_path):
    (tmp_path / 'f.txt').write_text('line1\nline2\nline3\n')
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return False
    ctx = make_ctx(tmp_path, cb, approval_edits=True)
    patch = '@@ -1,3 +1,3 @@\n line1\n-line2\n+LINE2\n line3\n'
    res = json.loads(dispatch(ctx, 'apply_patch', {'path': 'f.txt', 'patch': patch}))
    assert res['status'] == 'denied'
    assert res['reason'] == 'user declined this edit'
    assert (tmp_path / 'f.txt').read_text() == 'line1\nline2\nline3\n' # Unchanged

def test_edit_approval_diff_truncated(tmp_path):
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return True
    ctx = make_ctx(tmp_path, cb, approval_edits=True)
    res = json.loads(dispatch(ctx, 'write_file', {'path': 'big.txt', 'content': 'x' * 3000}))
    assert res['status'] == 'completed'
    assert len(called) == 1
    assert '… [truncated]' in called[0][1]  # large diffs are truncated in the preview
