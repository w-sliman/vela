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


# ── checkpoints hold the user's work, never the agent's own state ────────────

def _trace(root, text='{"kind":"user"}\n'):
    d = root / '.coder-agent' / 'sessions'
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'session.jsonl'
    f.write_text(text)
    return f


def test_checkpoints_never_track_agent_state(tmp_path):
    git = Git(tmp_path)
    git.ensure_repo()
    Workspace(tmp_path).write_file('app.py', 'print(1)\n')
    _trace(tmp_path)
    git.snapshot('auto: write_file app.py')
    assert git.run('ls-files').stdout.split() == ['app.py']


def test_undo_reverts_the_code_but_not_the_session_trace(tmp_path):
    """`/undo` is `git reset --hard`. While traces were tracked it rewound them too,
    truncating the history `/resume` reads back — including the running session's."""
    git = Git(tmp_path)
    git.ensure_repo()
    ws = Workspace(tmp_path)
    ws.write_file('README.md', 'project\n')
    git.snapshot('initial')

    trace = _trace(tmp_path)
    ws.write_file('app.py', 'print(1)\n')
    git.snapshot('auto: write_file app.py')
    trace.write_text(trace.read_text() + '{"kind":"assistant"}\n')   # written after the checkpoint

    ok, _ = git.undo_last_checkpoint()
    assert ok
    assert not (tmp_path / 'app.py').exists(), 'the edit is reverted'
    assert trace.read_text().count('\n') == 2, 'the trace is not'


def test_agent_state_committed_by_an_older_version_is_untracked_once(tmp_path):
    """One-time migration for workspaces checkpointed before the exclude existed."""
    git = Git(tmp_path)
    git.ensure_repo()
    trace = _trace(tmp_path)
    git.run('add', '-f', '.coder-agent')          # simulate the old `add -A` behaviour
    git.run('commit', '-m', 'legacy checkpoint')
    assert '.coder-agent/sessions/session.jsonl' in git.run('ls-files').stdout

    Git(tmp_path).ensure_repo()

    assert git.run('ls-files').stdout.split() == []
    assert trace.exists(), 'untracking must never delete the file'
