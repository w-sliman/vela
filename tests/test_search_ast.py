import json

from coding_agent.search import search_symbols
from coding_agent.tools import ToolContext, dispatch


def build(tmp_path):
    pkg = tmp_path / 'pkg'
    pkg.mkdir()
    (pkg / 'mod.py').write_text(
        'import asyncio\n'
        'def greet(name):\n'
        '    return name\n'
        '\n'
        'async def fetch(url, *, timeout=5):\n'
        '    pass\n'
        '\n'
        'class Greeter:\n'
        '    def method_one(self):\n'
        '        def nested_helper(x):\n'
        '            return x\n'
        '        return nested_helper\n'
        '\n'
        '    async def method_two(self):\n'
        '        pass\n'
        '\n'
        'def conditional():\n'
        '    if True:\n'
        '        def hidden():\n'
        '            return 1\n'
        '        return hidden\n'
    )
    (tmp_path / 'broken.py').write_text('def ok_before_syntax_error(:\n')
    (tmp_path / 'notes.txt').write_text('def not_python():\n')


def test_finds_all_defs_with_qualified_names(tmp_path):
    build(tmp_path)
    syms = {s['symbol'] for s in search_symbols(tmp_path)}
    assert {'greet', 'fetch', 'Greeter', 'Greeter.method_one',
            'Greeter.method_one.nested_helper', 'Greeter.method_two',
            'conditional', 'conditional.hidden', 'ok_before_syntax_error'} <= syms


def test_kinds_and_signatures_are_accurate(tmp_path):
    build(tmp_path)
    by_name = {s['symbol']: s for s in search_symbols(tmp_path)}
    assert by_name['fetch']['kind'] == 'async def'
    assert by_name['greet']['kind'] == 'def'
    assert by_name['Greeter']['kind'] == 'class'
    assert 'name' in by_name['greet']['signature']
    assert 'timeout' in by_name['fetch']['signature']


def test_line_spans_cover_whole_definition(tmp_path):
    build(tmp_path)
    m = {s['symbol']: s for s in search_symbols(tmp_path)}
    greet = m['greet']
    assert greet['end_line'] > greet['line']       # multi-line def captured
    assert m['conditional.hidden']['line'] > m['conditional']['line']


def test_query_filters_case_insensitively_on_qualified_name(tmp_path):
    build(tmp_path)
    syms = {s['symbol'] for s in search_symbols(tmp_path, 'greeter.')}
    assert {'Greeter.method_one', 'Greeter.method_two'} <= syms
    assert all(s.startswith('Greeter.') for s in syms)


def test_broken_file_falls_back_to_regex(tmp_path):
    build(tmp_path)
    broken = [s for s in search_symbols(tmp_path) if s['path'] == 'broken.py']
    assert broken and broken[0]['signature'] == '(?)'


def test_non_python_files_ignored(tmp_path):
    build(tmp_path)
    assert not any(s['path'].endswith('.txt') for s in search_symbols(tmp_path))


def test_dispatch_returns_structured_results(tmp_path):
    build(tmp_path)
    from coding_agent.config import Config as C
    from coding_agent.shell import Shell
    from coding_agent.workspace import Workspace
    from coding_agent.git import Git
    cfg = C('k', None, 'm', 'auto', tmp_path, 'prompt', 5000, 30000, 10, 10,
            20, 100, 10000, False, False, False, True, False)
    ctx = ToolContext(cfg, Workspace(tmp_path), Shell(cfg), lambda *_: True,
                      Git(tmp_path), None, None, None)
    result = json.loads(dispatch(ctx, 'search_symbols', {'query': 'method_two'}))
    assert result[0]['symbol'] == 'Greeter.method_two'
    assert result[0]['kind'] == 'async def'
