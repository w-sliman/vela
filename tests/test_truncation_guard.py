"""A partially-read file must never be writable back as a whole-file rewrite.

read_file bounds content at CODER_MAX_FILE_CHARS but hashes the *whole* file, so
the stale-hash guard cannot catch a truncated round-trip: the hash matches while
the content is short. The write path fails closed on the truncation marker.
"""
import json

import pytest

from coding_agent.browser import Browser
from coding_agent.config import Config
from coding_agent.git import Git
from coding_agent.github import GitHub
from coding_agent.sandbox import DockerSandbox
from coding_agent.shell import Shell
from coding_agent.tools import ToolContext
from coding_agent.workspace import TRUNCATION_MARKER, TruncatedContentError, Workspace

LIMIT = 100


def _ws(tmp_path, limit=LIMIT):
    return Workspace(tmp_path, max_file_chars=limit)


@pytest.fixture
def tool_ctx(tmp_path):
    cfg = Config(None, None, None, 'auto', tmp_path, 'prompt', 5000, LIMIT, 10, 10, 20, 100,
                 10000, False, False, False, True, False)
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
    from coding_agent.tools import dispatch

    (tmp_path / "big.py").write_text("y" * 500)
    payload = json.loads(dispatch(tool_ctx, "read_file", {"path": "big.py"}))
    assert payload["truncated"] is True
    assert "warning" in payload
    # the hash still covers the whole file, which is why the warning matters
    assert payload["sha256"] == tool_ctx.workspace.hash_file("big.py")


def test_write_file_tool_reports_the_refusal(tmp_path, tool_ctx):
    from coding_agent.tools import dispatch

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
