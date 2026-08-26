import subprocess
from pathlib import Path
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
        return self._ready
    def snapshot(self,msg):
        """Commit the full workspace state; returns 'committed'|'clean'|None on failure."""
        if not msg or not self.ensure_repo():return None
        if self.run('add','-A').returncode:return None
        r=self.run('commit','-m',msg)
        return 'committed' if r.returncode==0 else 'clean'
    def undo_last(self):
        """Revert workspace to the state before the last snapshot."""
        r=self.run('reset','--hard','HEAD~1')
        return r
    def status(self):return self.run('status','--short','--branch')
    def diff(self,staged=False):return self.run('diff',*(('--staged',) if staged else ()))
    def checkpoint(self,msg):
        a=self.run('add','-A');
        if a.returncode:return a
        return self.run('commit','-m',msg)
