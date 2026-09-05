"""Non-interactive prompting.

The REPL takes one line per turn, so a piped multi-line request became one agent
turn per line -- the model began work having seen only the first line. These
flags take the whole text as a single request.
"""
import io
import sys

import pytest

from vela.cli import main


def _env(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
    monkeypatch.setenv('OPENAI_MODEL', 'dummy-model')
    monkeypatch.setenv('VELA_CONTEXT_WINDOW', '128000')


@pytest.fixture
def captured(monkeypatch):
    """Record every request the agent is asked to run."""
    seen = []

    class Reply:
        text = 'done'

    def fake_run(self, text, *args, **kwargs):
        seen.append(text)
        return Reply()

    monkeypatch.setattr('vela.llm.CodingAgent.run', fake_run)
    return seen


MULTILINE = 'Proxy authentication bug\n\nWhen using proxies I get a 407.\n\nFix it.\n'


def test_prompt_flag_is_one_request(monkeypatch, captured, tmp_path):
    _env(monkeypatch)
    monkeypatch.setattr(sys, 'argv',
                        ['vela', '--workspace', str(tmp_path), '--prompt', MULTILINE])
    main()
    assert len(captured) == 1
    assert captured[0].startswith('Proxy authentication bug')
    assert '407' in captured[0] and 'Fix it.' in captured[0]


def test_prompt_file_is_one_request(monkeypatch, captured, tmp_path):
    _env(monkeypatch)
    f = tmp_path / 'task.txt'
    f.write_text(MULTILINE)
    monkeypatch.setattr(sys, 'argv',
                        ['vela', '--workspace', str(tmp_path), '--prompt-file', str(f)])
    main()
    assert len(captured) == 1
    assert '407' in captured[0] and 'Fix it.' in captured[0]


def test_prompt_file_dash_reads_all_of_stdin(monkeypatch, captured, tmp_path):
    _env(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(MULTILINE))
    monkeypatch.setattr(sys, 'argv',
                        ['vela', '--workspace', str(tmp_path), '--prompt-file', '-'])
    main()
    assert len(captured) == 1
    assert '407' in captured[0] and 'Fix it.' in captured[0]


def test_piping_without_the_flag_still_splits_per_line(monkeypatch, captured, tmp_path):
    """The footgun this fixes: documented here so the difference stays visible."""
    _env(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(MULTILINE + '/quit\n'))
    monkeypatch.setattr(sys, 'argv', ['vela', '--workspace', str(tmp_path)])
    main()
    assert len(captured) > 1


def test_both_flags_is_an_error(monkeypatch, tmp_path):
    _env(monkeypatch)
    monkeypatch.setattr(sys, 'argv',
                        ['vela', '--workspace', str(tmp_path), '--prompt', 'x',
                         '--prompt-file', 'y'])
    with pytest.raises(SystemExit):
        main()


def test_empty_prompt_is_rejected(monkeypatch, captured, tmp_path):
    _env(monkeypatch)
    monkeypatch.setattr(sys, 'argv',
                        ['vela', '--workspace', str(tmp_path), '--prompt', '   \n  '])
    with pytest.raises(SystemExit):
        main()
    assert not captured
