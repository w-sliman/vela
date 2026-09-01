from datetime import datetime, timezone

from tests.conftest import make_config
from vela.session import Session
from vela.shell import Shell


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
    cfg = make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto')
    result = Shell(cfg).run('sh -c "echo boom; exit 3"', approved=True)
    assert result.returncode == 3
    assert 'boom' in result.stdout
    assert result.stderr == ''


def test_usage_events_report_the_window_in_force_not_the_configured_one(tmp_path):
    """A window learned from the server overrules configuration, so every context
    percentage must be measured against it — reporting the config default understated
    real context pressure by about half whenever the two disagreed."""
    import json as _json
    from tests.test_compact import FakeProvider
    from vela.budget import ContextBudget
    from vela.events import EventBus
    from vela.llm import CodingAgent
    from vela.session import Session
    from tests.conftest import make_config

    seen = []
    bus = EventBus(seen.append)
    agent = CodingAgent(make_config(tmp_path, context_window_tokens=128000), None,
                        Session(tmp_path), bus)
    agent.provider = FakeProvider(_json.dumps({'summary': 's'}))
    agent.budget = ContextBudget(65536)          # what the server actually said

    agent._emit_usage({'input': 1000, 'output': 10, 'total': 1010})

    assert [e.data['window'] for e in seen if e.kind == 'usage'] == [65536]
