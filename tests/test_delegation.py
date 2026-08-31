"""Sub-agent delegation.

`delegate_role` was the one model call in the codebase that skipped the retry
policy every other call had, so a transient provider blip failed the delegation
outright. It also opened a client at REPL startup whether or not it was ever used.
"""
import json

import pytest

from coding_agent.agents import Delegator
from coding_agent.tools import dispatch
from tests.conftest import make_config


class Flaky:
    """Fails `failures` times, then answers."""
    def __init__(self, failures=0, answer='the plan'):
        self.failures = failures
        self.answer = answer
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError('transient upstream error')
        from types import SimpleNamespace as NS
        self.last = kw
        return NS(choices=[NS(message=NS(content=self.answer))])


def _delegator(tmp_path, provider, **over):
    d = Delegator(make_config(tmp_path, **over), 'Workspace: /w')
    d.provider = provider
    return d


def test_a_transient_failure_is_retried(tmp_path):
    p = Flaky(failures=2)
    assert _delegator(tmp_path, p, request_retries=2).run('planner', 'design it') == 'the plan'
    assert p.calls == 3


def test_a_persistent_failure_still_surfaces(tmp_path):
    p = Flaky(failures=99)
    with pytest.raises(RuntimeError, match='transient upstream error'):
        _delegator(tmp_path, p, request_retries=1).run('planner', 'design it')
    assert p.calls == 2


def test_retries_are_announced_when_an_event_bus_is_given(tmp_path):
    from coding_agent.events import EventBus
    seen = []
    d = Delegator(make_config(tmp_path, request_retries=2), '', EventBus(lambda e: seen.append(e.message)))
    d.provider = Flaky(failures=1)
    d.run('reviewer', 'review it')
    assert any('delegate(reviewer)' in m for m in seen)


def test_the_client_is_not_built_until_it_is_used(tmp_path):
    """A REPL that never delegates should never open a connection."""
    d = Delegator(make_config(tmp_path), '')
    assert d._provider is None


@pytest.mark.parametrize('role', ['planner', 'reviewer'])
def test_both_roles_carry_their_own_brief(tmp_path, role):
    p = Flaky()
    _delegator(tmp_path, p).run(role, 'do the thing')
    system = p.last['messages'][0]['content']
    assert 'Do not edit files' in system
    assert ('architect' in system) == (role == 'planner')


def test_an_unknown_role_is_rejected_before_any_call(tmp_path):
    p = Flaky()
    with pytest.raises(ValueError, match='planner, reviewer'):
        _delegator(tmp_path, p).run('saboteur', 'delete everything')
    assert p.calls == 0


def test_workspace_context_and_task_reach_the_model(tmp_path):
    p = Flaky()
    _delegator(tmp_path, p).run('planner', 'add retry handling')
    user = p.last['messages'][1]['content']
    assert 'Workspace: /w' in user and 'add retry handling' in user


def test_dispatch_reports_a_missing_delegator_rather_than_crashing(tmp_path):
    from coding_agent.browser import Browser
    from coding_agent.git import Git
    from coding_agent.github import GitHub
    from coding_agent.sandbox import DockerSandbox
    from coding_agent.shell import Shell
    from coding_agent.tools import ToolContext
    from coding_agent.workspace import Workspace

    c = make_config(tmp_path)
    ctx = ToolContext(c, Workspace(tmp_path), Shell(c), lambda *_: True, Git(tmp_path),
                      Browser(), GitHub(), DockerSandbox(tmp_path))
    out = json.loads(dispatch(ctx, 'delegate_role', {'role': 'planner', 'task': 'x'}))
    assert out['status'] == 'error' and 'unavailable' in out['message']
