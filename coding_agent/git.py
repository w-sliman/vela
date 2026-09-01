import subprocess
from pathlib import Path

# The agent's own state inside the workspace: session traces, the learned-window
# cache, persistent memory. Checkpoints must never contain it. A checkpoint is a
# snapshot of *the user's work*, and `/undo` is `git reset --hard` — so anything
# swept in here gets rewritten when a checkpoint is undone, silently truncating the
# very traces `/resume` reads back and destroying the audit trail of the session
# doing the undoing.
AGENT_STATE_DIR='.coder-agent'
class Git:
    def __init__(self,root):self.root=root;self._ready=None
    def run(self,*args):return subprocess.run(['git',*args],cwd=self.root,text=True,capture_output=True)
    def ensure_repo(self):
        """Initialize a local repo (with fallback identity) once; True when usable."""
        if self._ready is None:
            if not (Path(self.root)/'.git').exists():self.run('init','--quiet')
            if self.run('config','user.email').returncode!=0:
                self.run('config','user.email','agent@local');self.run('config','user.name','coding-agent')
            self._ready=self.run('rev-parse','--git-dir').returncode==0
            if self._ready:self._exclude_agent_state()
        return self._ready
    def _exclude_agent_state(self):
        """Keep agent state out of the repo, and evict it if an older run committed it.

        The exclude lives in `.git/info/exclude` rather than `.gitignore` so the
        agent never writes to a tracked file in the user's project. Untracking is a
        one-time migration for workspaces checkpointed before this existed; it only
        removes the index entry, never the files themselves.
        """
        try:
            git_dir=self.run('rev-parse','--git-dir').stdout.strip()
            if git_dir:
                info=(Path(self.root)/git_dir/'info');info.mkdir(parents=True,exist_ok=True)
                exclude=info/'exclude';current=exclude.read_text() if exclude.exists() else ''
                if f'/{AGENT_STATE_DIR}/' not in current:
                    exclude.write_text(f'{current}{"" if current.endswith(chr(10)) or not current else chr(10)}'
                                       f'# coding agent state; never part of a checkpoint\n/{AGENT_STATE_DIR}/\n')
            if self.run('ls-files','--error-unmatch',AGENT_STATE_DIR).returncode==0:
                self.run('rm','-r','--cached','--quiet',AGENT_STATE_DIR)
        except OSError:
            pass
    def snapshot(self,msg):
        """Commit the full workspace state; returns 'committed'|'clean'|None on failure."""
        if not msg or not self.ensure_repo():return None
        if self.run('add','-A').returncode:return None
        r=self.run('commit','-m',msg)
        return 'committed' if r.returncode==0 else 'clean'
    def undo_last_checkpoint(self):
        """Revert the workspace to the state before the newest agent auto-checkpoint.

        Only commits whose message starts with 'auto: ' (created by post-edit
        snapshots) are undoable, and only when that checkpoint is the latest
        commit — user commits are never reverted. Returns (ok, message).
        """
        head=self.run('rev-parse','HEAD')
        if head.returncode!=0:return False,'no commits in repository yet'
        r=self.run('log','--format=%H %s','-n','100')
        if r.returncode!=0:return False,f'git log failed: {r.stderr.strip()}'
        for line in r.stdout.splitlines():
            h,sep,msg=line.partition(' ')
            if not sep or not msg.startswith('auto: '):continue
            if h!=head.stdout.strip():
                return False,'newest agent checkpoint is not the latest commit; refusing to revert commits on top of it'
            target=f'{h}~1'
            if self.run('rev-parse','--verify','-q',target).returncode!=0:
                return False,f'checkpoint "{msg}" is the first commit in the repository and cannot be undone'
            rr=self.run('reset','--hard',target)
            if rr.returncode==0:return True,f'undone: workspace restored to state before "{msg}"'
            return False,f'undo failed: {rr.stderr.strip()}'
        return False,'no agent checkpoint to undo (checkpoints are commits starting with "auto: "); user commits are never reverted'
    def status(self):return self.run('status','--short','--branch')
    def diff(self,staged=False):return self.run('diff',*(('--staged',) if staged else ()))
    def checkpoint(self,msg):
        a=self.run('add','-A');
        if a.returncode:return a
        return self.run('commit','-m',msg)
