from __future__ import annotations
import os, re, subprocess, threading
from dataclasses import dataclass
from .policy import Decision, classify_command
@dataclass(frozen=True)
class ShellResult:
    command:str; returncode:int; stdout:str; stderr:str; decision:Decision
_SECRET_KEY_RE=re.compile(r'(API_?KEY|TOKEN|SECRET|PASSWORD)',re.I)
def child_env():
    """Environment for child processes with secret-shaped variables removed."""
    return {k:v for k,v in os.environ.items() if not _SECRET_KEY_RE.search(k)}
class Shell:
    """Run commands with stderr merged into stdout (single stream).

    ShellResult.stderr is kept for interface compatibility but is always
    empty; all command output lives in stdout.
    """
    def __init__(self,config): self.config=config
    def classify(self,command): return classify_command(command,self.config.workspace)
    def run(self,command,approved=False,timeout=None,on_output=None):
        d=self.classify(command)
        if d.action=='deny': raise PermissionError(d.reason)
        if d.action=='approve' and not approved: raise PermissionError(f'approval required: {d.reason}')
        p=subprocess.Popen(command,shell=True,cwd=self.config.workspace,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=child_env(),bufsize=1)
        chunks=[]
        def drain():
            for line in p.stdout:
                chunks.append(line)
                if on_output: on_output(line)
        t=threading.Thread(target=drain,daemon=True);t.start()
        try: p.wait(timeout=timeout or self.config.command_timeout)
        except subprocess.TimeoutExpired:
            p.kill(); t.join(.2); out=''.join(chunks)
            return ShellResult(command,124,out[:self.config.max_tool_output]+'\ncommand timed out','',d)
        t.join(.2);out=''.join(chunks)
        return ShellResult(command,p.returncode,out[:self.config.max_tool_output],'',d)
