from tests.conftest import make_config
from coding_agent.shell import Shell


def config(tmp_path):
    return make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto')


def test_child_env_scrubs_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'supersecret')
    monkeypatch.setenv('GITHUB_TOKEN', 'ghtok')
    monkeypatch.setenv('HOME', '/home/keepme')
    result = Shell(config(tmp_path)).run(
        'sh -c \'echo "${OPENAI_API_KEY:-unset} ${GITHUB_TOKEN:-unset} ${HOME:+kept}"\'',
        approved=True)
    assert 'supersecret' not in result.stdout
    assert 'ghtok' not in result.stdout
    assert 'unset unset kept' in result.stdout
