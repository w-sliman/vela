"""Telling the model the network is gone, and enforcing it in the shell.

On a SWE-bench run with egress blocked the agent spent 58 of its 90 shell
commands trying to reach the network and made no edits at all: each attempt
failed on its own terms, so a different technique always looked worth trying.
"""
from tests.conftest import make_config
from vela.policy import classify_command
from vela.prompts import NO_NETWORK_NOTE
from vela.shell import Shell


NETWORKY = [
    'curl https://example.com',
    'wget http://example.com/f',
    'python -c "import urllib.request; urllib.request.urlopen(1)"',
    'python3 - <<PY\nimport requests; requests.get("http://x")\nPY',
    'pip install requests',
    'git clone https://github.com/psf/requests',
    'nc example.com 80',
]
LOCAL = [
    'pytest -q',
    'ls -la',
    'python -m pytest tests/test_utils.py',
    'grep -rn "def parse" requests/',
]


def test_network_commands_denied_when_there_is_no_network():
    for cmd in NETWORKY:
        d = classify_command(cmd, network=False)
        assert d.action == 'deny', cmd
        assert 'no network access' in d.reason


def test_the_denial_says_what_to_do_instead():
    reason = classify_command('curl https://example.com', network=False).reason
    assert 'cannot succeed' in reason and 'repository contents' in reason


def test_local_commands_are_unaffected():
    for cmd in LOCAL:
        assert classify_command(cmd, network=False).action != 'deny', cmd


def test_default_is_permissive():
    """pip install and git fetch are ordinary; denying them by default protects nothing."""
    for cmd in NETWORKY:
        assert classify_command(cmd).action != 'deny', cmd


def test_shell_refuses_the_command_rather_than_running_it(tmp_path):
    cfg = make_config(tmp_path, api_key=None, base_url=None, model=None,
                      api_mode='auto', shell_network=False)
    shell = Shell(cfg)
    try:
        shell.run('curl https://example.com', approved=True)
        raise AssertionError('expected the shell to refuse')
    except PermissionError as exc:
        assert 'no network access' in str(exc)


def test_the_note_names_the_techniques_the_model_will_try():
    for tool in ('curl', 'wget', 'urllib', 'requests', 'pip install', 'git clone'):
        assert tool in NO_NETWORK_NOTE
    assert 'will not change that' in NO_NETWORK_NOTE
