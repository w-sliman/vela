"""Bounds that must announce themselves, and preconditions that must run in order.

Three of these guard the same failure shape: a bounded result that looks complete.
The other two guard ordering (validate before prompting) and clamping (the model
does not get to pick its own ceiling).
"""
import json

import pytest

from tests.conftest import make_config
from vela.browser import Browser
from vela.git import Git
from vela.github import GitHub
from vela.sandbox import DockerSandbox
from vela.shell import Shell
from vela.tools import ToolContext, _timeout, dispatch
from vela.workspace import MAX_LISTING_ENTRIES, ConcurrentEditError, Workspace


def _cfg(tmp_path, **kw):
    return make_config(tmp_path, **kw)


def _ctx(tmp_path, approve=True, **kw):
    c = _cfg(tmp_path, **kw)
    return ToolContext(c, Workspace(tmp_path), Shell(c), lambda *_: approve, Git(tmp_path),
                       Browser(), GitHub(), DockerSandbox(tmp_path))


# ── shell output is not cut off at the tail ──────────────────────────────────

def test_output_survives_a_consumer_slower_than_the_process(tmp_path):
    """The drain thread must be allowed to finish after the process exits.

    The real consumer is a Rich `console.print` per line, which is slow enough to
    still be working when a short-lived command has already exited. A 0.2s join
    cut it off there and handed the model a truncated view of its own test output;
    the sleep here makes that race deterministic rather than machine-dependent.
    """
    import sys
    import time

    c = _cfg(tmp_path, max_tool_output=10_000_000, command_timeout=30)
    (tmp_path / 'noisy.py').write_text('for i in range(40): print(i)\n')
    seen = []

    def slow_consumer(line):
        time.sleep(0.01)          # ~0.4s of draining, well past the old 0.2s bound
        seen.append(line)

    result = Shell(c).run(f'{sys.executable} noisy.py', approved=True,
                          on_output=slow_consumer)
    assert result.returncode == 0
    assert len(seen) == 40, f'consumer saw only {len(seen)} of 40 lines'
    assert result.stdout.splitlines()[-1] == '39'


# ── listing cap announces itself ─────────────────────────────────────────────

def test_listing_under_the_cap_is_not_flagged(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / 'a.py').write_text('x')
    entries, truncated = ws.list_files_bounded()
    assert truncated is False and 'a.py' in entries


def test_listing_over_the_cap_is_flagged(tmp_path):
    ws = Workspace(tmp_path)
    for i in range(MAX_LISTING_ENTRIES + 25):
        (tmp_path / f'f{i:05d}.txt').write_text('x')
    entries, truncated = ws.list_files_bounded()
    assert truncated is True
    assert len(entries) == MAX_LISTING_ENTRIES


def test_list_files_tool_surfaces_the_cap(tmp_path):
    for i in range(MAX_LISTING_ENTRIES + 5):
        (tmp_path / f'f{i:05d}.txt').write_text('x')
    payload = json.loads(dispatch(_ctx(tmp_path), 'list_files', {}))
    assert payload['truncated'] is True
    assert 'warning' in payload
    assert len(payload['entries']) == MAX_LISTING_ENTRIES


# ── write preconditions run before the approval prompt ───────────────────────

def test_stale_hash_is_rejected_without_prompting(tmp_path):
    """The user must not be asked to approve a diff that cannot be applied."""
    asked = []

    def approval(label, preview):
        asked.append(label)
        return True

    c = _cfg(tmp_path, approval_edits=True)
    ctx = ToolContext(c, Workspace(tmp_path), Shell(c), approval, Git(tmp_path),
                      Browser(), GitHub(), DockerSandbox(tmp_path))
    (tmp_path / 'app.py').write_text('original\n')
    result = json.loads(dispatch(ctx, 'write_file', {
        'path': 'app.py', 'content': 'replacement\n', 'expected_hash': 'deadbeef'}))
    assert result['status'] == 'error'
    assert result['error_type'] == 'ConcurrentEditError'
    assert asked == [], 'preconditions must be checked before prompting'
    assert (tmp_path / 'app.py').read_text() == 'original\n'


def test_valid_edit_still_prompts_and_applies(tmp_path):
    asked = []
    c = _cfg(tmp_path, approval_edits=True)
    ws = Workspace(tmp_path)
    ctx = ToolContext(c, ws, Shell(c), lambda label, _: (asked.append(label), True)[1],
                      Git(tmp_path), Browser(), GitHub(), DockerSandbox(tmp_path))
    (tmp_path / 'app.py').write_text('original\n')
    result = json.loads(dispatch(ctx, 'write_file', {
        'path': 'app.py', 'content': 'replacement\n', 'expected_hash': ws.hash_file('app.py')}))
    assert result['status'] == 'completed'
    assert asked == ['edit app.py']
    assert (tmp_path / 'app.py').read_text() == 'replacement\n'


def test_preflight_does_not_touch_the_file(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / 'app.py').write_text('original\n')
    with pytest.raises(ConcurrentEditError):
        ws.preflight_write('app.py', 'new\n', 'deadbeef')
    assert (tmp_path / 'app.py').read_text() == 'original\n'


# ── the model cannot exceed the configured timeout ceiling ───────────────────

@pytest.mark.parametrize('requested,expected', [
    (None, 10),      # unspecified -> ceiling
    (5, 5),          # under the ceiling -> honored
    (10, 10),        # at the ceiling -> honored
    (99999, 10),     # over the ceiling -> clamped
    (0, 1),          # nonsense low -> floor
    (-30, 1),
    ('abc', 10),     # junk -> ceiling, never an exception
    (None, 10),
])
def test_timeout_is_clamped_to_the_configured_ceiling(tmp_path, requested, expected):
    assert _timeout(_ctx(tmp_path), requested) == expected
