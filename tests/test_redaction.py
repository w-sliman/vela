"""Secrets must not reach anything that outlives the run.

An agent investigating its own environment (`env`, /proc/self/environ) prints
the API key Vela was given. Scrubbing child environments does not help: the
value is already in the command's *output*, and from there it reached a session
trace that was committed to git.
"""
import json

from vela.redact import PLACEHOLDER, redact, redact_obj
from vela.session import Session

# Synthetic, and it must stay synthetic: a test fixture is committed, so pasting
# a real credential here would recreate the very leak these tests exist to stop.
KEY = 'sk-' + 'FAKE0testkeyNOTREAL' * 3


def test_provider_shaped_tokens_are_removed():
    for secret in (KEY, 'ghp_' + 'a' * 30, 'xoxb-1234567890-abcdef', 'AKIA' + 'A' * 16):
        assert secret not in redact(f'OPENAI_API_KEY={secret} PATH=/usr/bin')


def test_the_processes_own_secrets_are_removed_by_value(monkeypatch):
    """Whatever the patterns miss, our own credentials are known exactly."""
    monkeypatch.setenv('SOME_PROVIDER_TOKEN', 'not-key-shaped-but-secret-12345')
    assert 'not-key-shaped-but-secret-12345' not in redact('TOKEN=not-key-shaped-but-secret-12345')


def test_ordinary_output_is_untouched():
    text = 'FAILED tests/test_utils.py::test_prepend_scheme_if_needed - AssertionError'
    assert redact(text) == text
    assert redact('PATH=/usr/local/bin HOME=/root') == 'PATH=/usr/local/bin HOME=/root'


def test_short_env_values_do_not_trigger_redaction(monkeypatch):
    """A one-character token would otherwise redact every occurrence of that letter."""
    monkeypatch.setenv('TOKEN', 'x')
    assert redact('executing xargs') == 'executing xargs'


def test_redaction_reaches_into_nested_payloads():
    out = redact_obj({'result': {'stdout': [f'OPENAI_API_KEY={KEY}']}})
    assert KEY not in json.dumps(out)
    assert PLACEHOLDER in out['result']['stdout'][0]


def test_session_trace_never_records_the_key(tmp_path):
    """The regression: this exact payload shape was committed to git."""
    session = Session(tmp_path)
    session.record('tool_result', {'name': 'run_command',
                                   'result': json.dumps({'stdout': f'OPENAI_API_KEY={KEY}'})})
    written = session.path.read_text(encoding='utf-8')
    assert KEY not in written
    assert PLACEHOLDER in written


def test_recorded_events_stay_valid_json(tmp_path):
    session = Session(tmp_path)
    session.record('tool_result', {'result': f'key={KEY}', 'returncode': 0})
    row = json.loads(session.path.read_text(encoding='utf-8').splitlines()[-1])
    assert row['payload']['returncode'] == 0
    assert KEY not in row['payload']['result']
