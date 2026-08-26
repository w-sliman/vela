import pytest

from coding_agent.config import Config
from coding_agent.editor import exact_replace, replace_lines, unified_apply
from coding_agent.shell import Shell
from coding_agent.tools import ToolContext, dispatch
from coding_agent.workspace import Workspace


def context(tmp_path):
    cfg = Config(None, None, None, 'auto', tmp_path, 'prompt', 5000, 30000,
                 10, 10, 20, 100, 10000, False, False, False, True, False)
    return ToolContext(cfg, Workspace(tmp_path), Shell(cfg), lambda *_: True,
                       None, None, None, None)


ORIGINAL = 'def foo():\n    return 42\n'


def test_missing_required_arg_is_named(tmp_path):
    result = dispatch(context(tmp_path), 'apply_patch', {'patch': '@@ -1,1 +1,1 @@\n-x\n'})
    assert '"status": "error"' in result
    assert 'missing required argument(s): path' in result


def test_not_found_error_lists_closest_matches():
    with pytest.raises(ValueError) as exc:
        exact_replace(ORIGINAL, 'return 43', 'return 0')
    msg = str(exc.value)
    assert 'closest matches' in msg
    assert 'line 2' in msg and 'return 42' in msg


def test_not_found_without_hint_stays_clean():
    with pytest.raises(ValueError) as exc:
        exact_replace('aaa\nbbb\n', 'zzz', 'q')
    assert 'closest matches' not in str(exc.value)


def test_fuzzy_error_reports_line_and_alternatives():
    from coding_agent.editor import fuzzy_replace
    with pytest.raises(ValueError) as exc:
        fuzzy_replace(ORIGINAL, 'totally different text here', 'x')
    assert 'near line' in str(exc.value)


def test_unified_apply_header_error_teaches_format():
    with pytest.raises(ValueError) as exc:
        unified_apply(ORIGINAL, '@@\n-old\n+new\n')
    assert "expected format" in str(exc.value)
    assert "@@ -1,3 +1,4 @@" in str(exc.value)


def test_replace_lines_basic():
    out = replace_lines(ORIGINAL, 2, 2, '    return 7\n')
    assert out == 'def foo():\n    return 7\n'


def test_replace_lines_rejects_bad_range():
    with pytest.raises(ValueError):
        replace_lines(ORIGINAL, 5, 6, 'x')


def test_line_mode_end_to_end(tmp_path):
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'value = 1\nsecond\n'})
    result = dispatch(ctx, 'replace_text',
                      {'path': 'app.py', 'start_line': 1, 'end_line': 1, 'new': 'value = 9\n'})
    assert '"status": "completed"' in result
    assert (tmp_path / 'app.py').read_text() == 'value = 9\nsecond\n'


def test_text_mode_requires_old_when_no_line_range(tmp_path):
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'value = 1\n'})
    result = dispatch(ctx, 'replace_text', {'path': 'app.py', 'new': 'value = 2\n'})
    assert '"status": "error"' in result
    assert 'provide patch or old/new' in result or 'old' in result
