from __future__ import annotations
import os,httpx
from .net import safe_api_path
API_BASE='https://api.github.com'
class GitHub:
 """Opt-in GitHub reads. The path is validated before it is appended to the fixed
 base: this request carries a bearer token, so a re-targeted host leaks a credential."""
 def __init__(self,enabled=False):self.enabled=enabled;self.token=os.getenv('GITHUB_TOKEN')
 def request(self,method,path,**kw):
  if not self.enabled:raise PermissionError('GitHub support disabled; set VELA_ENABLE_GITHUB=1')
  h={'Accept':'application/vnd.github+json'}
  if self.token:h['Authorization']=f'Bearer {self.token}'
  r=httpx.request(method,API_BASE+safe_api_path(path),headers=h,timeout=20,follow_redirects=False,**kw)
  r.raise_for_status();return r.json()
