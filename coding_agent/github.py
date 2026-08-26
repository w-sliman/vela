from __future__ import annotations
import os,httpx
class GitHub:
 def __init__(self,enabled=False):self.enabled=enabled;self.token=os.getenv('GITHUB_TOKEN')
 def request(self,method,path,**kw):
  if not self.enabled:raise PermissionError('GitHub support disabled; set CODER_ENABLE_GITHUB=1')
  h={'Accept':'application/vnd.github+json'}
  if self.token:h['Authorization']=f'Bearer {self.token}'
  r=httpx.request(method,'https://api.github.com'+path,headers=h,timeout=20,**kw);r.raise_for_status();return r.json()
