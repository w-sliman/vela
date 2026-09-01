import json

import pytest

from tests.conftest import make_config
from coding_agent.editor import exact_replace, replace_lines, unified_apply
from coding_agent.shell import Shell
from coding_agent.tools import ToolContext, dispatch
from coding_agent.workspace import Workspace


def context(tmp_path):
    cfg = make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto')
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


def _hash(ctx, path):
    return json.loads(dispatch(ctx, 'read_file', {'path': path}))['sha256']


def test_line_mode_end_to_end(tmp_path):
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'value = 1\nsecond\n'})
    result = dispatch(ctx, 'replace_text',
                      {'path': 'app.py', 'start_line': 1, 'end_line': 1, 'new': 'value = 9\n',
                       'expected_hash': _hash(ctx, 'app.py')})
    assert '"status": "completed"' in result
    assert (tmp_path / 'app.py').read_text() == 'value = 9\nsecond\n'


def test_line_mode_requires_a_hash(tmp_path):
    """A line range is positional — nothing in it is checked against the text being
    replaced, so the hash is all that stands between a mis-counted range and a
    silently mangled file."""
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'value = 1\nsecond\n'})
    result = json.loads(dispatch(ctx, 'replace_text',
                                 {'path': 'app.py', 'start_line': 1, 'new': 'value = 9\n'}))
    assert result['error_type'] == 'ConcurrentEditError'
    assert (tmp_path / 'app.py').read_text() == 'value = 1\nsecond\n', 'unchanged'


def test_overwriting_an_existing_file_requires_a_hash(tmp_path):
    """write_file without a hash silently destroyed edits made since the read."""
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'original = 1\n'})
    result = json.loads(dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'clobbered = 1\n'}))
    assert result['error_type'] == 'ConcurrentEditError'
    assert (tmp_path / 'app.py').read_text() == 'original = 1\n'


def test_creating_a_new_file_needs_no_hash(tmp_path):
    """There is nothing to race against, so the guard must not obstruct creation."""
    ctx = context(tmp_path)
    assert '"status": "completed"' in dispatch(ctx, 'write_file',
                                               {'path': 'fresh.py', 'content': 'x = 1\n'})


# ── an edit may not newly break a file the tools can parse ──────────────────

def test_edit_that_would_break_python_syntax_is_refused(tmp_path):
    """Every corruption seen in real use — a mis-counted range, an anchor missing its
    trailing newline — produced a file that simply would not parse."""
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'm.py', 'content': 'def f():\n    return 1\n'})
    result = json.loads(dispatch(ctx, 'replace_text',
                                 {'path': 'm.py', 'old': '    return 1\n', 'new': '    return 1\ndef g(:\n',
                                  'expected_hash': _hash(ctx, 'm.py')}))
    assert result['error_type'] == 'SyntaxRegressionError'
    assert 'recovery' in result
    assert (tmp_path / 'm.py').read_text() == 'def f():\n    return 1\n', 'nothing written'


def test_an_already_broken_file_can_still_be_repaired(tmp_path):
    """The rule is no *regression*: refusing here would block the fix."""
    ctx = context(tmp_path)
    (tmp_path / 'broken.py').write_text('def f(:\n')
    result = dispatch(ctx, 'replace_text',
                      {'path': 'broken.py', 'old': 'def f(:\n', 'new': 'def f():\n    pass\n',
                       'expected_hash': _hash(ctx, 'broken.py')})
    assert '"status": "completed"' in result


def test_non_python_files_are_not_syntax_checked(tmp_path):
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'notes.md', 'content': '# hi\n'})
    result = dispatch(ctx, 'write_file', {'path': 'notes.md', 'content': 'def f(:\n',
                                          'expected_hash': _hash(ctx, 'notes.md')})
    assert '"status": "completed"' in result


def test_text_mode_requires_old_when_no_line_range(tmp_path):
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'value = 1\n'})
    result = dispatch(ctx, 'replace_text', {'path': 'app.py', 'new': 'value = 2\n'})
    assert '"status": "error"' in result
    assert 'provide patch or old/new' in result or 'old' in result


def test_line_mode_requires_end_line(tmp_path):
    """Observed live: a model passed start_line=1 meaning "rewrite the file", end_line
    defaulted to start_line, and only line 1 was replaced — inserting the new version
    above the old one and duplicating the body. The result reported success and parses
    as valid Python, so no syntax check can catch it; the range must be stated."""
    ctx = context(tmp_path)
    body = 'def f():\n    total = 1\n    return total\n'
    dispatch(ctx, 'write_file', {'path': 'm.py', 'content': body})
    result = json.loads(dispatch(ctx, 'replace_text',
                                 {'path': 'm.py', 'start_line': 1,
                                  'new': 'def f():\n    if True:\n        return 0\n    return 1\n',
                                  'expected_hash': _hash(ctx, 'm.py')}))
    assert result['status'] == 'error' and 'needs end_line' in result['message']
    assert (tmp_path / 'm.py').read_text() == body, 'nothing written'


def test_line_mode_accepts_an_explicit_single_line_range(tmp_path):
    """Stating start_line == end_line is unambiguous and must still work."""
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'm.py', 'content': 'a = 1\nb = 2\n'})
    result = dispatch(ctx, 'replace_text',
                      {'path': 'm.py', 'start_line': 1, 'end_line': 1, 'new': 'a = 9\n',
                       'expected_hash': _hash(ctx, 'm.py')})
    assert '"status": "completed"' in result
    assert (tmp_path / 'm.py').read_text() == 'a = 9\nb = 2\n'
