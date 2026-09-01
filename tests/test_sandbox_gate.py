import json
import subprocess

from tests.conftest import make_config
from vela.tools import ToolContext, dispatch
from vela.workspace import Workspace
from vela.shell import Shell
from vela.git import Git
from vela.browser import Browser
from vela.github import GitHub
from vela.sandbox import DockerSandbox


def make_ctx(tmp_path, callback):
    c = make_config(tmp_path, price_input_per_million=0.0, price_output_per_million=0.0)
    return ToolContext(c, Workspace(tmp_path), Shell(c), callback, Git(tmp_path),
                       Browser(), GitHub(), DockerSandbox(tmp_path))


def _fake_run(ran):
    def run(command, image='python:3.12-slim', timeout=60):
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, 'ok', '')
    return run


def test_sandbox_run_risky_command_denied_without_approval(tmp_path):
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return False
    ctx = make_ctx(tmp_path, cb)
    res = json.loads(dispatch(ctx, 'sandbox_run', {'command': 'rm -rf /'}))
    assert res['status'] == 'denied'
    assert len(called) == 1  # approval was requested


def test_sandbox_run_risky_command_approved_reaches_sandbox(tmp_path, monkeypatch):
    ran = []
    ctx = make_ctx(tmp_path, lambda *_: True)
    monkeypatch.setattr(ctx.sandbox, 'run', _fake_run(ran))
    res = json.loads(dispatch(ctx, 'sandbox_run', {'command': 'rm -rf /'}))
    assert res['status'] == 'completed'
    assert ran == ['rm -rf /']


def test_sandbox_run_safe_command_skips_approval(tmp_path, monkeypatch):
    ran = []
    called = []
    def cb(cmd, reason):
        called.append((cmd, reason))
        return False
    ctx = make_ctx(tmp_path, cb)
    monkeypatch.setattr(ctx.sandbox, 'run', _fake_run(ran))
    res = json.loads(dispatch(ctx, 'sandbox_run', {'command': 'ls'}))
    assert res['status'] == 'completed'
    assert not called  # safe commands need no approval
    assert ran == ['ls']
