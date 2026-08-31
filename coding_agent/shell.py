from __future__ import annotations
import os, re, signal, subprocess, threading
from dataclasses import dataclass
from .policy import Decision, classify_command
@dataclass(frozen=True)
class ShellResult:
    command:str; returncode:int; stdout:str; stderr:str; decision:Decision
def _kill_tree(p):
    """Kill the child's whole process group so grandchildren cannot outlive a timeout."""
    try:os.killpg(os.getpgid(p.pid),signal.SIGKILL)
    except (ProcessLookupError,PermissionError,AttributeError):p.kill()
# The process is already dead when we join, so the drain thread only has buffered
# output left to read. The bound exists solely for the case where a grandchild
# inherited stdout and holds the pipe open; 0.2s was short enough to truncate the
# tail of a normal chatty command, which the model then reasoned from.
_DRAIN_GRACE=5.0
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
    def classify(self,command): return classify_command(command)
    def run(self,command,approved=False,timeout=None,on_output=None):
        d=self.classify(command)
        if d.action=='deny': raise PermissionError(d.reason)
        if d.action=='approve' and not approved: raise PermissionError(f'approval required: {d.reason}')
        p=subprocess.Popen(command,shell=True,cwd=self.config.workspace,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=child_env(),bufsize=1,start_new_session=True)
        chunks=[]
        def drain():
            # If the grace period expires the pipe is closed underneath this loop;
            # that is an orderly shutdown, not an error worth surfacing.
            try:
                for line in p.stdout:
                    chunks.append(line)
                    if on_output: on_output(line)
            except (ValueError,OSError):pass
        t=threading.Thread(target=drain,daemon=True);t.start()
        try: p.wait(timeout=timeout or self.config.command_timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p); out=self._drain(p,t,chunks)
            return ShellResult(command,124,out[:self.config.max_tool_output]+'\ncommand timed out','',d)
        out=self._drain(p,t,chunks)
        return ShellResult(command,p.returncode,out[:self.config.max_tool_output],'',d)
    @staticmethod
    def _drain(p,thread,chunks):
        """Collect whatever the reader thread has left once the process has exited."""
        thread.join(_DRAIN_GRACE)
        try:p.stdout.close()
        except Exception:pass
        return ''.join(chunks)
