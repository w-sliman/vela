from coding_agent.config import Config
from coding_agent.shell import Shell


def config(tmp_path):
    return Config(None, None, None, 'auto', tmp_path, 'prompt', 5000, 30000,
                  10, 10, 20, 100, 10000, False, False, False, True, False)


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
