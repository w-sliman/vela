from coding_agent.config import Config
from coding_agent.git import Git
from coding_agent.workspace import Workspace


def test_snapshot_and_undo_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    git = Git(tmp_path)
    assert git.ensure_repo() is True
    ws.write_file('a.txt', 'v1\n')
    assert git.snapshot('baseline') == 'committed'
    ws.write_file('a.txt', 'v2\n')
    ws.write_file('b.txt', 'new\n')
    assert git.snapshot('second') == 'committed'
    r = git.undo_last()
    assert r.returncode == 0
    assert (tmp_path / 'a.txt').read_text() == 'v1\n'
    assert not (tmp_path / 'b.txt').exists()


def test_undo_past_baseline_fails_cleanly(tmp_path):
    Git(tmp_path).ensure_repo()
    git = Git(tmp_path)
    Workspace(tmp_path).write_file('only.txt', 'x\n')
    assert git.snapshot('first') == 'committed'
    assert git.undo_last().returncode != 0


def test_clean_tree_reports_clean(tmp_path):
    git = Git(tmp_path)
    git.ensure_repo()
    Workspace(tmp_path).write_file('same.txt', 'x\n')
    assert git.snapshot('first') == 'committed'
    assert git.snapshot('nothing changed') == 'clean'


def test_edit_tool_creates_checkpoint(tmp_path):
    from coding_agent.tools import ToolContext, dispatch
    from coding_agent.shell import Shell
    c = Config('k', None, 'm', 'auto', tmp_path, 'prompt', 5000, 30000, 10, 10,
               20, 100, 10000, False, False, False, True, False,
               0.0, 0.0, 128000, 2, True, 80, True, True)
    ctx = ToolContext(c, Workspace(tmp_path), Shell(c), lambda *_: True,
                      Git(tmp_path), None, None, None)
    result = dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'print(1)\n'})
    assert '"checkpoint": "committed"' in result
    assert (tmp_path / '.git').exists()


def test_list_files_hides_git_dir(tmp_path):
    from coding_agent.tools import ToolContext
    c = Config('k', None, 'm', 'auto', tmp_path, 'prompt', 5000, 30000, 10, 10,
               20, 100, 10000, False, False, False, True, False)
    ctx = ToolContext(c, Workspace(tmp_path), None, lambda *_: True,
                      Git(tmp_path), None, None, None)
    ctx.git.ensure_repo()
    files = ctx.workspace.list_files()
    assert not any(f.startswith('.git') for f in files)
