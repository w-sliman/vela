"""Shared test fixtures.

`make_config` exists because Config is a long positional dataclass: constructing it
inline made every field change a 25-file edit, and hid which values a test actually
cared about. Tests name only what matters and inherit safe defaults for the rest —
notably a base_url pointing at a dead port, so a test that accidentally reaches the
network fails fast instead of hanging.
"""
import pytest

from vela.config import Config

DEFAULTS = dict(
    api_key='test-key', base_url='http://localhost:9/v1', model='model-x', api_mode='chat',
    approval_mode='prompt', max_tool_output=5000, max_file_chars=30000, command_timeout=10,
    enable_browser=False, enable_github=False, enable_sandbox=False, telemetry=True, debug=False,
    context_window_tokens=128000, request_retries=0, stream_chat=False, auto_checkpoint=False,
    # Tests state their window, so startup never probes a server that isn't there.
    context_window_explicit=True,
)


def make_config(tmp_path, **overrides):
    """A Config for tests: sane defaults, named overrides, no positional guessing."""
    return Config(workspace=tmp_path, **{**DEFAULTS, **overrides})


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)
