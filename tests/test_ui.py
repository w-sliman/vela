import io

from rich.console import Console

from vela.events import AgentEvent
from vela.ui import DebugUI, _fmt_tokens


def render(enabled=False):
    buf = io.StringIO()
    return DebugUI(console=Console(file=buf, width=200), enabled=enabled), buf


def test_usage_event_renders_even_when_debug_disabled():
    ui, buf = render(enabled=False)
    ui.event(AgentEvent('usage', 'model usage', {
        'available': True, 'input': 12300, 'output': 900, 'total': 13200,
        'last_input': 12300, 'window': 128000}))
    out = buf.getvalue()
    assert '12.3k in' in out and '900 out' in out and '13.2k total' in out
    assert 'context 12.3k/128.0k (10%)' in out


def test_usage_small_numbers_not_kformatted():
    ui, buf = render(enabled=False)
    ui.event(AgentEvent('usage', 'model usage', {
        'available': True, 'input': 120, 'output': 30, 'total': 150,
        'last_input': 120, 'window': 8000}))
    assert '120 in / 30 out / 150 total' in buf.getvalue()
    assert '(2%)' in buf.getvalue()


def test_unavailable_usage_shows_advice():
    ui, buf = render(enabled=False)
    ui.event(AgentEvent('usage', 'model usage', {'available': False, 'advice': 'enable usage reporting'}))
    assert 'usage reporting' in buf.getvalue()


def test_non_usage_events_still_gated():
    ui, buf = render(enabled=False)
    ui.event(AgentEvent('start', 'tool: read_file', {}))
    assert buf.getvalue() == ''


def test_debug_enabled_still_renders_other_kinds():
    ui, buf = render(enabled=True)
    ui.event(AgentEvent('start', 'tool: read_file', {}))
    assert 'tool: read_file' in buf.getvalue()


def test_fmt_tokens():
    assert _fmt_tokens(999) == '999'
    assert _fmt_tokens(1000) == '1.0k'
    assert _fmt_tokens(128000) == '128.0k'
