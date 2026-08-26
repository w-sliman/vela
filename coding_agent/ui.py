from __future__ import annotations

from rich.console import Console
from rich.panel import Panel


def _fmt_tokens(n):
    """1234 -> '1.2k'; below 1000 stays plain."""
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


class DebugUI:
    """Render operational agent events when debug mode is enabled.

    This intentionally displays execution events, tool names, status and
    timing rather than model chain-of-thought. Per-turn token/context
    usage ('usage' events) is operational information and renders even
    when debug mode is disabled.
    """

    def __init__(self, console: Console | None = None, enabled: bool = False):
        self.console = console or Console()
        self.enabled = enabled

    def event(self, event):
        kind = getattr(event, "kind", "info")
        if kind == "usage":
            self._usage(getattr(event, "data", {}) or {})
            return
        if kind == "token":
            self._token(getattr(event, "data", {}) or {})
            return
        if not self.enabled:
            return
        self._debug(event, kind)

    def _token(self, data):
        """Streamed assistant text; rendered raw (no rich markup) and always visible."""
        if data.get("end"):
            self.console.print()
            return
        piece = str(data.get("text", ""))
        if piece:
            self.console.print(piece, end="", markup=False, highlight=False)

    def _usage(self, data):
        if not data.get("available"):
            advice = str(data.get("advice", "endpoint returned no usage object"))
            self.console.print(f"[yellow]⚠ {advice}[/]")
            return
        window = int(data.get("window") or 0)
        last = int(data.get("last_input") or 0)
        ctx = ""
        if window > 0 and last > 0:
            ctx = f" | context {_fmt_tokens(last)}/{_fmt_tokens(window)} ({last/window*100:.0f}%)"
        self.console.print(
            "[dim]⇄ tokens "
            f"{_fmt_tokens(int(data.get('input') or 0))} in / "
            f"{_fmt_tokens(int(data.get('output') or 0))} out / "
            f"{_fmt_tokens(int(data.get('total') or 0))} total{ctx}[/]"
        )

    def _debug(self, event, kind):
        styles = {"start": "cyan", "done": "green", "error": "red", "info": "yellow"}
        icons = {"start": "▶", "done": "✓", "error": "✗", "info": "•"}

        message = getattr(event, "message", str(event))
        data = getattr(event, "data", {}) or {}

        details = []
        if "duration_ms" in data:
            details.append(f"{data['duration_ms']}ms")
        if data.get("repaired"):
            details.append("JSON repaired")
        if data.get("status") and data["status"] not in {"completed"}:
            details.append(f"status={data['status']}")

        suffix = f" ({', '.join(details)})" if details else ""
        self.console.print(
            f"[{styles.get(kind, 'white')}]"
            f"{icons.get(kind, '•')} {message}{suffix}[/]"
        )

    def tool_result(self, name: str, result):
        if not self.enabled:
            return

        text = result if isinstance(result, str) else str(result)
        if len(text) > 700:
            text = text[:700] + "…"
        self.console.print(Panel(text, title=f"tool: {name}", border_style="dim"))
