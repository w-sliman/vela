from tests.conftest import make_config
from coding_agent.shell import Shell
from coding_agent.workspace import Workspace
from coding_agent.git import Git
from coding_agent.browser import Browser
from coding_agent.github import GitHub
from coding_agent.sandbox import DockerSandbox
from coding_agent.tools import ToolContext,dispatch

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
