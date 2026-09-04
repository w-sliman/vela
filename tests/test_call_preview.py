"""The live view must say what run_command ran, not just that it ran."""
from vela.llm import _call_preview


def test_command_is_shown():
    line = _call_preview('run_command', {'command': 'python -m pytest -q | head -n 100'})
    assert line == 'run_command python -m pytest -q | head -n 100'


def test_path_is_shown_for_file_tools():
    assert _call_preview('read_file', {'path': 'expr/parser.py'}) == 'read_file expr/parser.py'


def test_long_commands_are_truncated_on_one_line():
    line = _call_preview('run_command', {'command': 'echo ' + 'x' * 500})
    assert len(line) < 200
    assert '\n' not in line
    assert line.endswith('…')


def test_multiline_arguments_collapse():
    assert '\n' not in _call_preview('run_command', {'command': 'a\n  b'})


def test_unparsed_arguments_fall_back_to_the_name():
    assert _call_preview('write_todos', None) == 'write_todos'
    assert _call_preview('run_command', {}) == 'run_command'
