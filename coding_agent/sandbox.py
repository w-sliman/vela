from __future__ import annotations
import shutil,subprocess
class DockerSandbox:
 def __init__(self,root,enabled=False):self.root=root;self.enabled=enabled
 def available(self):return self.enabled and shutil.which('docker') is not None
 def run(self,command,image='python:3.12-slim',timeout=60):
  if not self.available():raise RuntimeError('Docker sandbox unavailable or disabled')
  return subprocess.run(['docker','run','--rm','--network','none','--cpus','1','--memory','1g','-v',f'{self.root}:/workspace','-w','/workspace',image,'sh','-lc',command],text=True,capture_output=True,timeout=timeout)
