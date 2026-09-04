"""A failing check must not be hidden by the pipe the model wrote to bound output."""
import sys

import pytest

from tests.conftest import make_config
from vela.llm import _is_check_command
from vela.shell import _BASH, Shell


def config(tmp_path):
    return make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto')


needs_bash = pytest.mark.skipif(_BASH is None, reason='pipefail requires bash')


@needs_bash
def test_failing_stage_survives_a_pipe(tmp_path):
    """`pytest | head` reported head's status, so a failed suite cleared the gate."""
    result = Shell(config(tmp_path)).run('false | cat', approved=True)
    assert result.returncode != 0


@needs_bash
def test_failing_check_command_survives_truncation(tmp_path):
    result = Shell(config(tmp_path)).run(
        'python -c "raise SystemExit(1)" 2>&1 | head -n 100', approved=True)
    assert result.returncode != 0
    # The gate only inspects commands it recognises as checks; this is one.
    assert _is_check_command('python -m pytest -q 2>&1 | head -n 100')


@needs_bash
def test_early_pipe_close_is_not_a_failure(tmp_path):
    """pipefail turns orderly truncation into SIGPIPE; that is not a failed check."""
    result = Shell(config(tmp_path)).run('seq 1 200000 | head -n 2', approved=True)
    assert result.returncode == 0
    assert result.stdout.startswith('1')


def test_exit_status_is_still_faithful(tmp_path):
    shell = Shell(config(tmp_path))
    assert shell.run('exit 7', approved=True).returncode == 7
    assert shell.run('true', approved=True).returncode == 0


def test_child_python_is_the_interpreter_vela_runs(tmp_path):
    """A bare `python` in a command must mean the venv Vela was launched from."""
    result = Shell(config(tmp_path)).run(
        'python -c "import sys; print(sys.executable)"', approved=True)
    assert result.returncode == 0
    assert result.stdout.strip() == sys.executable
