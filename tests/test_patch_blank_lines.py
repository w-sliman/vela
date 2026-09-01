"""Blank context lines in unified diffs.

A blank context line is canonically ' \\n' (space + newline), but most producers —
including many models and every editor that strips trailing whitespace — emit a
bare '\\n'. Rejecting those makes apply_patch fail on the majority of real diffs
that touch a file containing blank lines.
"""
import pytest

from vela.editor import unified_apply

ORIGINAL = "def a():\n    pass\n\ndef b():\n    pass\n"


def test_blank_context_line_without_trailing_space():
    patch = "@@ -1,4 +1,5 @@\n def a():\n     pass\n\n+# added\n def b():\n"
    assert unified_apply(ORIGINAL, patch) == "def a():\n    pass\n\n# added\ndef b():\n    pass\n"


def test_canonical_blank_context_line_still_works():
    patch = "@@ -1,4 +1,5 @@\n def a():\n     pass\n \n+# added\n def b():\n"
    assert unified_apply(ORIGINAL, patch) == "def a():\n    pass\n\n# added\ndef b():\n    pass\n"


def test_crlf_blank_context_line():
    original = "a\r\n\r\nb\r\n"
    patch = "@@ -1,3 +1,4 @@\n a\r\n\r\n+c\r\n b\r\n"
    assert unified_apply(original, patch) == "a\r\n\r\nc\r\nb\r\n"


def test_deleting_a_blank_line():
    patch = "@@ -1,4 +1,3 @@\n def a():\n     pass\n-\n def b():\n"
    assert unified_apply(ORIGINAL, patch) == "def a():\n    pass\ndef b():\n    pass\n"


def test_genuinely_unsupported_line_still_rejected():
    with pytest.raises(ValueError, match="unsupported patch line"):
        unified_apply(ORIGINAL, "@@ -1,2 +1,2 @@\n def a():\n?     pass\n")


def test_context_mismatch_still_detected():
    patch = "@@ -1,4 +1,5 @@\n def z():\n     pass\n\n+# added\n def b():\n"
    with pytest.raises(ValueError, match="context does not match"):
        unified_apply(ORIGINAL, patch)
