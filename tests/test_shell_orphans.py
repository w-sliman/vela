"""A command must not outlive the Vela process that started it.

Children run in their own session so a timeout can kill the whole group; the
same isolation means nothing signals them when Vela itself is terminated.
"""
import os
import signal
import subprocess
import sys
import time

import pytest

from tests.conftest import make_config
from vela.shell import _LIVE, Shell, _reap_live


def config(tmp_path):
    return make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto')


def _alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def test_finished_commands_are_not_tracked(tmp_path):
    shell = Shell(config(tmp_path))
    assert shell.run('true', approved=True).returncode == 0
    assert not _LIVE


def test_timed_out_commands_are_not_tracked(tmp_path):
    shell = Shell(config(tmp_path))
    assert shell.run('sleep 30', approved=True, timeout=1).returncode == 124
    assert not _LIVE


CHILD = """
import sys, time
sys.path.insert(0, {root!r})
from vela.config import Config
from vela.shell import Shell
cfg = Config.from_env({ws!r})
shell = Shell(cfg)
shell.run('sh -c "echo $$ > {pidfile}; sleep 120"', approved=True, timeout=90)
"""


@pytest.mark.parametrize('sig', [signal.SIGTERM, signal.SIGHUP])
def test_child_dies_with_vela(tmp_path, sig):
    """SIGTERM is what `timeout` sends; SIGHUP is a closed terminal."""
    pidfile = tmp_path / 'child.pid'
    script = CHILD.format(root=str(os.getcwd()), ws=str(tmp_path), pidfile=pidfile)
    parent = subprocess.Popen([sys.executable, '-c', script])
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not pidfile.exists():
            time.sleep(0.05)
        assert pidfile.exists(), 'child never started'
        pid = int(pidfile.read_text().strip())
        assert _alive(pid)

        parent.send_signal(sig)
        parent.wait(timeout=30)

        deadline = time.time() + 10
        while time.time() < deadline and _alive(pid):
            time.sleep(0.05)
        assert not _alive(pid), 'command outlived the Vela process that started it'
    finally:
        if parent.poll() is None:
            parent.kill()


def test_reap_is_safe_with_nothing_running():
    _reap_live()
    assert not _LIVE
