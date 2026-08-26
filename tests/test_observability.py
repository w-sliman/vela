from datetime import datetime, timezone

from coding_agent.config import Config
from coding_agent.session import Session
from coding_agent.shell import Shell


def test_session_filename_is_utc(tmp_path):
    before = datetime.now(timezone.utc)
    session = Session(tmp_path)
    after = datetime.now(timezone.utc)
    stem = session.path.stem  # YYYYMMDD-HHMMSS-ffffff
    parsed = datetime.strptime(stem, '%Y%m%d-%H%M%S-%f').replace(tzinfo=timezone.utc)
    assert before <= parsed <= after


def test_error_events_are_journaled(tmp_path):
    session = Session(tmp_path)
    session.record('user', {'text': 'hi'})
    # llm.py journals transport failures with this shape; verify the recorder round-trips it
    session.record('error', {'stage': 'model_request', 'mode': 'chat', 'message': 'boom'})
    kinds = [e['kind'] for e in session.recent()]
    assert kinds == ['user', 'error']
    assert session.recent()[-1]['payload']['stage'] == 'model_request'


def test_shell_reports_single_honest_output(tmp_path):
    cfg = Config(None, None, None, 'auto', tmp_path, 'prompt', 5000, 30000,
                 10, 10, 20, 100, 10000, False, False, False, True, False)
    result = Shell(cfg).run('sh -c "echo boom; exit 3"', approved=True)
    assert result.returncode == 3
    assert 'boom' in result.stdout
    assert result.stderr == ''
