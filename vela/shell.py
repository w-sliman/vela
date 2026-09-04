from __future__ import annotations
import atexit, os, re, shutil, signal, subprocess, sys, threading
import pathlib
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
# Children are started in their own session so a timeout can kill the whole
# process group. The cost is that they no longer die with us: nothing signals a
# detached session when Vela is terminated, so a command still running when we
# exit would keep running invisibly, holding its workspace and its resources
# after the agent that started it is gone. Every live child is tracked and
# reaped on the way out.
_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK=threading.Lock()
def _reap_live():
    """Kill every command still running. Registered for exit and termination."""
    with _LIVE_LOCK: procs=list(_LIVE); _LIVE.clear()
    for p in procs:
        if p.poll() is None: _kill_tree(p)
def _terminating_handler(signum,frame):
    """Reap children, then die of the original signal so our exit status is honest."""
    _reap_live()
    signal.signal(signum,signal.SIG_DFL)
    os.kill(os.getpid(),signum)
def _install_reaper():
    """Reap on normal exit and on the signals a supervisor actually sends.

    SIGKILL cannot be caught, so a `kill -9` still orphans; SIGTERM (what
    `timeout` and most supervisors send) and SIGHUP (a closed terminal) are the
    cases worth covering. An existing handler is left alone -- an embedding
    application owns its own signals.
    """
    atexit.register(_reap_live)
    for name in ('SIGTERM','SIGHUP'):
        sig=getattr(signal,name,None)
        if sig is None: continue
        try:
            if signal.getsignal(sig) is signal.SIG_DFL:
                signal.signal(sig,_terminating_handler)
        except (ValueError,OSError):pass   # not the main thread, or unsupported
_install_reaper()
_SECRET_KEY_RE=re.compile(r'(API_?KEY|TOKEN|SECRET|PASSWORD)',re.I)
# Vela runs from a venv, but children are spawned through a shell that only sees
# the ambient PATH. Without this, `python -m pytest` -- the command our own docs
# tell the agent to run -- can resolve to nothing at all, and the agent burns
# turns hunting for an interpreter. It is appended rather than prepended: a
# workspace with its own virtualenv on PATH must keep winning, because the
# project's environment, not ours, is the one its tests need.
_INTERPRETER_BIN=str(pathlib.Path(sys.executable).parent)
def child_env():
    """Environment for child processes with secret-shaped variables removed.

    The interpreter running Vela is appended to PATH as a fallback, so a bare
    `python` resolves even when nothing else provides one, without displacing an
    interpreter the workspace itself puts ahead of it.
    """
    env={k:v for k,v in os.environ.items() if not _SECRET_KEY_RE.search(k)}
    path=env.get('PATH','')
    if _INTERPRETER_BIN not in path.split(os.pathsep):
        env['PATH']=(path+os.pathsep if path else '')+_INTERPRETER_BIN
    return env
# A pipeline reports only its last stage, so `pytest | head` -- which is what a
# model writes to bound output -- exits 0 even when the suite failed, and the
# verify gate accepts it as a passing check. pipefail makes the pipeline carry
# the failure. dash has no pipefail, so this needs a real bash.
_BASH=shutil.which('bash')
_PIPEFAIL_PREFIX='set -o pipefail\n'
# With pipefail on, a downstream stage exiting early (`... | head -n 100`) makes
# the producer die of SIGPIPE and the whole pipeline report 141. That is orderly
# truncation, not a failed check, so it is reported as success -- 141 can only
# arise this way, since a real non-zero exit from any stage outranks it.
_SIGPIPE_RC=141
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
        script=(_PIPEFAIL_PREFIX+command) if _BASH else command
        p=subprocess.Popen(script,shell=True,executable=_BASH,cwd=self.config.workspace,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=child_env(),bufsize=1,start_new_session=True)
        chunks=[]
        def drain():
            # If the grace period expires the pipe is closed underneath this loop;
            # that is an orderly shutdown, not an error worth surfacing.
            try:
                assert p.stdout is not None    # stdout=PIPE above guarantees it
                for line in p.stdout:
                    chunks.append(line)
                    if on_output: on_output(line)
            except (ValueError,OSError):pass
        t=threading.Thread(target=drain,daemon=True);t.start()
        with _LIVE_LOCK: _LIVE.add(p)
        try:
            try: p.wait(timeout=timeout or self.config.command_timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(p); out=self._drain(p,t,chunks)
                return ShellResult(command,124,out[:self.config.max_tool_output]+'\ncommand timed out','',d)
            out=self._drain(p,t,chunks)
            rc=0 if p.returncode==_SIGPIPE_RC else p.returncode
            return ShellResult(command,rc,out[:self.config.max_tool_output],'',d)
        finally:
            with _LIVE_LOCK: _LIVE.discard(p)
    @staticmethod
    def _drain(p,thread,chunks):
        """Collect whatever the reader thread has left once the process has exited."""
        thread.join(_DRAIN_GRACE)
        try:p.stdout.close()
        except Exception:pass
        return ''.join(chunks)
