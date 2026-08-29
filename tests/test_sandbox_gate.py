import json
import subprocess

from coding_agent.config import Config
from coding_agent.tools import ToolContext, dispatch
from coding_agent.workspace import Workspace
from coding_agent.shell import Shell
from coding_agent.git import Git
from coding_agent.browser import Browser
from coding_agent.github import GitHub
from coding_agent.sandbox import DockerSandbox


def make_ctx(tmp_path, callback):
    c = Config('test-key', 'http://localhost:9/v1', 'model-x', 'chat', tmp_path,
               'prompt', 5000, 30000, 10, 10, 20, 100, 10000, False, False, False,
               True, False, 0.0, 0.0, 128000)
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
