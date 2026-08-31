import io
import sys

from coding_agent.cli import main


def test_cli_slash_commands_smoke(monkeypatch, capsys, tmp_path):
    """Drive the REPL non-interactively: banner, /help, /pwd, /tree, /usage, /quit."""
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
    monkeypatch.setenv('OPENAI_MODEL', 'dummy-model')
    monkeypatch.setenv('CODER_CONTEXT_WINDOW', '128000')   # explicit -> no startup probe
    monkeypatch.setattr(sys, 'stdin', io.StringIO('/help\n/pwd\n/tree\n/usage\n/continue\n/quit\n'))
    monkeypatch.setattr(sys, 'argv', ['coding_agent', '--workspace', str(tmp_path)])
    main()
    out = capsys.readouterr().out
    assert 'Workspace Coding Agent' in out          # banner
    assert str(tmp_path) in out                     # /pwd
    assert '/sessions' in out and '/resume' in out  # /help table lists new commands
    assert '/continue' in out                       # pause/continue documented in help
    assert 'nothing to continue' in out             # empty-context guard
    assert 'tokens in/out/total' in out             # /usage line
    assert 'set via CODER_CONTEXT_WINDOW' in out    # /usage context line (regression: NameError)


def test_cli_sessions_and_resume(monkeypatch, capsys, tmp_path):
    """/sessions lists a prior trace; /resume rebuilds context from it."""
    import json
    from datetime import datetime, timezone
    d = tmp_path / '.coder-agent' / 'sessions'; d.mkdir(parents=True)
    ev = {'timestamp': datetime.now(timezone.utc).isoformat(), 'kind': 'user',
          'payload': {'text': 'earlier task about hello.py'}}
    (d / '20260101-000000-000000.jsonl').write_text(json.dumps(ev) + '\n')
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
    monkeypatch.setenv('OPENAI_MODEL', 'dummy-model')
    monkeypatch.setenv('CODER_CONTEXT_WINDOW', '128000')   # explicit -> no startup probe
    monkeypatch.setattr(sys, 'stdin', io.StringIO('/sessions\n/resume\n/resume #9\n/quit\n'))
    monkeypatch.setattr(sys, 'argv', ['coding_agent', '--workspace', str(tmp_path)])
    main()
    out = capsys.readouterr().out
    assert 'Recent sessions' in out and '20260101-000000-000000' in out
    assert 'resumed' in out                          # digest resume confirmation
    assert 'out of range' in out                     # bad index reported
