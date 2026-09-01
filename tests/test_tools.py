from tests.conftest import make_config
from vela.shell import Shell
from vela.workspace import Workspace
from vela.git import Git
from vela.browser import Browser
from vela.github import GitHub
from vela.sandbox import DockerSandbox
from vela.tools import ToolContext,dispatch

def context(tmp_path):
    cfg=make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto')
    return ToolContext(cfg,Workspace(tmp_path),Shell(cfg),lambda *_:True,Git(tmp_path),Browser(),GitHub(),DockerSandbox(tmp_path))
def test_write_tool(tmp_path):
    result=dispatch(context(tmp_path),'write_file',{'path':'app.py','content':'print(1)\n'});assert 'wrote' in result
    assert (tmp_path/'app.py').read_text()=='print(1)\n'
def test_command_tool(tmp_path):
    import sys
    result=dispatch(context(tmp_path),'run_command',{'command':f'{sys.executable} -c "print(123)"'});assert '123' in result and 'returncode' in result

def test_fuzzy_replace_requires_expected_hash(tmp_path):
    c=context(tmp_path)
    dispatch(c,'write_file',{'path':'app.py','content':'value = 1\n'})
    result=dispatch(c,'replace_text',{'path':'app.py','old':'value = 1','new':'value = 2','fuzzy':True})
    assert '"status": "error"' in result and 'expected_hash' in result

def test_fuzzy_replace_with_expected_hash_succeeds(tmp_path):
    c=context(tmp_path)
    dispatch(c,'write_file',{'path':'app.py','content':'value = 1\n'})
    h=c.workspace.hash_file('app.py')
    result=dispatch(c,'replace_text',{'path':'app.py','old':'value = 1','new':'value = 2','fuzzy':True,'expected_hash':h})
    assert '"status": "completed"' in result
    assert (tmp_path/'app.py').read_text()=='value = 2\n'

def test_tool_result_callback_invoked(tmp_path):
    seen=[]
    c=ToolContext(make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto'),
                  Workspace(tmp_path),Shell(make_config(tmp_path, api_key=None, base_url=None, model=None, api_mode='auto')),
                  lambda *_:True,Git(tmp_path),Browser(),GitHub(),DockerSandbox(tmp_path),
                  on_tool_result=lambda name,result:seen.append(name))
    dispatch(c,'make_directory',{'path':'d'})
    assert 'make_directory' in seen


# ── the schema's declared limits are a contract, not a hint ──────────────────

def test_oversized_argument_is_refused_with_the_declared_limit(tmp_path):
    """A model sent 18k characters into a field documented at 8k; the replacement was
    silently short and mangled the file. The schema now binds."""
    import json
    from tests.test_editor import context
    ctx = context(tmp_path)
    dispatch(ctx, 'write_file', {'path': 'app.py', 'content': 'x = 1\n'})
    sha = json.loads(dispatch(ctx, 'read_file', {'path': 'app.py'}))['sha256']

    result = json.loads(dispatch(ctx, 'replace_text',
                                 {'path': 'app.py', 'old': 'x = 1\n', 'new': 'y' * 9000,
                                  'expected_hash': sha}))

    assert result['status'] == 'error'
    assert 'the limit is 8,000' in result['message']
    assert (tmp_path / 'app.py').read_text() == 'x = 1\n', 'nothing written'


def test_arguments_within_the_declared_limit_pass(tmp_path):
    from tests.test_editor import context
    ctx = context(tmp_path)
    assert '"status": "completed"' in dispatch(
        ctx, 'write_file', {'path': 'ok.py', 'content': '# ' + 'a' * 500 + '\n'})
