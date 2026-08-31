from tests.conftest import make_config
from coding_agent.git import Git
from coding_agent.workspace import Workspace


def test_clean_tree_reports_clean(tmp_path):
    git = Git(tmp_path)
    git.ensure_repo()
    Workspace(tmp_path).write_file('same.txt', 'x\n')
    assert git.snapshot('first') == 'committed'
    assert git.snapshot('nothing changed') == 'clean'


def test_edit_tool_creates_checkpoint(tmp_path):
    from coding_agent.tools import ToolContext, dispatch
    from coding_agent.shell import Shell
    c = make_config(tmp_path, api_key='k', base_url=None, model='m', api_mode='auto', price_input_per_million=0.0, price_output_per_million=0.0, request_retries=2, auto_compact=True, stream_chat=True, auto_checkpoint=True)
    ctx = ToolContext(c, Workspace(tmp_path), Shell(c), lambda *_: True,
                      Git(tmp_path), None, None, None)
    result = dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'print(1)\n'})
    assert '"checkpoint": "committed"' in result
    assert (tmp_path / '.git').exists()


def test_undo_last_checkpoint_reverts_agent_commit(tmp_path):
    git = Git(tmp_path)
    assert git.ensure_repo() is True
    ws = Workspace(tmp_path)
    ws.write_file('a.txt', 'v1\n')
    assert git.snapshot('auto: write_file a.txt') == 'committed'
    ws.write_file('a.txt', 'v2\n')
    ws.write_file('b.txt', 'new\n')
    assert git.snapshot('auto: write_file a.txt') == 'committed'
    ok, msg = git.undo_last_checkpoint()
    assert ok is True
    assert (tmp_path / 'a.txt').read_text() == 'v1\n'
    assert not (tmp_path / 'b.txt').exists()


def test_undo_last_checkpoint_refuses_over_user_commit(tmp_path):
    git = Git(tmp_path)
    assert git.ensure_repo() is True
    ws = Workspace(tmp_path)
    ws.write_file('a.txt', 'v1\n')
    assert git.snapshot('auto: write_file a.txt') == 'committed'
    ws.write_file('a.txt', 'v2\n')
    assert git.run('add', '-A').returncode == 0
    assert git.run('commit', '-m', 'user commit').returncode == 0
    ok, msg = git.undo_last_checkpoint()
    assert ok is False
    assert 'refusing' in msg
    assert (tmp_path / 'a.txt').read_text() == 'v2\n'  # user commit untouched


def test_undo_last_checkpoint_refuses_without_agent_commit(tmp_path):
    git = Git(tmp_path)
    assert git.ensure_repo() is True
    ws = Workspace(tmp_path)
    ws.write_file('a.txt', 'v1\n')
    assert git.run('add', '-A').returncode == 0
    assert git.run('commit', '-m', 'user commit').returncode == 0
    ok, msg = git.undo_last_checkpoint()
    assert ok is False
    assert 'no agent checkpoint' in msg


def test_undo_last_checkpoint_root_commit_refuses(tmp_path):
    git = Git(tmp_path)
    assert git.ensure_repo() is True
    ws = Workspace(tmp_path)
    ws.write_file('a.txt', 'v1\n')
    assert git.snapshot('auto: write_file a.txt') == 'committed'
    ok, msg = git.undo_last_checkpoint()
    assert ok is False
    assert 'first commit' in msg
    assert (tmp_path / 'a.txt').read_text() == 'v1\n'


def test_list_files_hides_git_dir(tmp_path):
    from coding_agent.tools import ToolContext
    c = make_config(tmp_path, api_key='k', base_url=None, model='m', api_mode='auto')
    ctx = ToolContext(c, Workspace(tmp_path), None, lambda *_: True,
                      Git(tmp_path), None, None, None)
    ctx.git.ensure_repo()
    files = ctx.workspace.list_files()
    assert not any(f.startswith('.git') for f in files)
