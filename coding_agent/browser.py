from __future__ import annotations
import httpx
class Browser:
 def __init__(self,enabled=False):self.enabled=enabled
 def fetch(self,url):
  if not self.enabled:raise PermissionError('browser support disabled; set CODER_ENABLE_BROWSER=1')
  r=httpx.get(url,follow_redirects=True,timeout=20);r.raise_for_status();return r.text[:50000]
 def open(self,url):
  if not self.enabled:raise PermissionError('browser support disabled; set CODER_ENABLE_BROWSER=1')
  try:
   from playwright.sync_api import sync_playwright
  except ImportError as e:raise RuntimeError("install browser support with: pip install -e '.[browser]' && playwright install chromium") from e
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True);page=browser.new_page();page.goto(url,wait_until='domcontentloaded',timeout=30000);text=page.locator('body').inner_text();title=page.title();browser.close();return {'title':title,'url':page.url,'text':text[:50000]}
