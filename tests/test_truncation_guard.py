"""A partially-read file must never be writable back as a whole-file rewrite.

read_file bounds content at VELA_MAX_FILE_CHARS but hashes the *whole* file, so
the stale-hash guard cannot catch a truncated round-trip: the hash matches while
the content is short. The write path fails closed on the truncation marker.
"""
import json

import pytest

from tests.conftest import make_config
from vela.browser import Browser
from vela.git import Git
from vela.github import GitHub
from vela.sandbox import DockerSandbox
from vela.shell import Shell
from vela.tools import ToolContext
from vela.workspace import TRUNCATION_MARKER, TruncatedContentError, Workspace

LIMIT = 100


def _ws(tmp_path, limit=LIMIT):
    return Workspace(tmp_path, max_file_chars=limit)


@pytest.fixture
def tool_ctx(tmp_path):
    cfg = make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto', max_file_chars=LIMIT)
    return ToolContext(cfg, _ws(tmp_path), Shell(cfg), lambda *_: True, Git(tmp_path),
                       Browser(), GitHub(), DockerSandbox(tmp_path))


def test_oversized_read_is_flagged_and_marked(tmp_path):
    ws = _ws(tmp_path)
    (tmp_path / "big.py").write_text("x" * 500)
    text, truncated = ws.read_file_bounded("big.py")
    assert truncated is True
    assert text.endswith(TRUNCATION_MARKER)


def test_small_read_is_not_flagged(tmp_path):
    ws = _ws(tmp_path)
    (tmp_path / "small.py").write_text("print(1)\n")
    text, truncated = ws.read_file_bounded("small.py")
    assert (text, truncated) == ("print(1)\n", False)


def test_writing_truncated_content_back_is_refused(tmp_path):
    ws = _ws(tmp_path)
    (tmp_path / "big.py").write_text("x" * 500)
    text = ws.read_file("big.py")
    digest = ws.hash_file("big.py")  # hash of the FULL file — matches, so no stale-hash error
    with pytest.raises(TruncatedContentError):
        ws.write_file("big.py", text, digest)
    assert len((tmp_path / "big.py").read_text()) == 500, "file must be untouched"


def test_read_file_tool_surfaces_truncation(tmp_path, tool_ctx):
    from vela.tools import dispatch

    (tmp_path / "big.py").write_text("y" * 500)
    payload = json.loads(dispatch(tool_ctx, "read_file", {"path": "big.py"}))
    assert payload["truncated"] is True
    assert "warning" in payload
    # the hash still covers the whole file, which is why the warning matters
    assert payload["sha256"] == tool_ctx.workspace.hash_file("big.py")


def test_write_file_tool_reports_the_refusal(tmp_path, tool_ctx):
    from vela.tools import dispatch

    (tmp_path / "big.py").write_text("y" * 500)
    read = json.loads(dispatch(tool_ctx, "read_file", {"path": "big.py"}))
    result = json.loads(
        dispatch(
            tool_ctx,
            "write_file",
            {"path": "big.py", "content": read["content"], "expected_hash": read["sha256"]},
        )
    )
    assert result["status"] == "error"
    assert result["error_type"] == "TruncatedContentError"
    assert len((tmp_path / "big.py").read_text()) == 500
