from __future__ import annotations
from .net import check_url,get_checked
class Browser:
 """Opt-in web access. URLs come from the model, which reads untrusted repository
 content, so every target is checked against net.check_url before a request is made."""
 def __init__(self,enabled=False,allow_private=False):self.enabled=enabled;self.allow_private=allow_private
 def _guard(self,url):
  if not self.enabled:raise PermissionError('browser support disabled; set VELA_ENABLE_BROWSER=1')
  return check_url(url,self.allow_private)
 def fetch(self,url):
  self._guard(url)
  return get_checked(url,self.allow_private,timeout=20).text[:50000]
 def open(self,url):
  self._guard(url)
  try:
   from playwright.sync_api import sync_playwright
  except ImportError as e:raise RuntimeError("install browser support with: pip install -e '.[browser]' && playwright install chromium") from e
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True)
   try:
    page=browser.new_page();page.goto(url,wait_until='domcontentloaded',timeout=30000)
    # The page may have redirected after the pre-flight check; judge where it landed.
    final=page.url;check_url(final,self.allow_private)
    return {'title':page.title(),'url':final,'text':page.locator('body').inner_text()[:50000]}
   finally:
    browser.close()
