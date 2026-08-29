from __future__ import annotations
import re
from dataclasses import dataclass
@dataclass(frozen=True)
class Decision: action:str; reason:str
SAFE_PREFIXES=('python ','python3 ','pytest','pip ','ruff ','mypy ','git status','git diff','git log','git show','git branch','ls','find ','pwd','cat ','head ','tail ','grep ','rg ','echo ','which ','true','false')
RISK_PATTERNS=[(re.compile(r'(^|\s)sudo(\s|$)',re.I),'privilege escalation'),(re.compile(r'\brm\s+-rf\b',re.I),'recursive force deletion'),(re.compile(r'\bmkfs(?:\s|$)',re.I),'filesystem formatting'),(re.compile(r'\bdd\s+if=',re.I),'raw disk write'),(re.compile(r'\bshutdown(?:\s|$)',re.I),'system shutdown'),(re.compile(r'\breboot(?:\s|$)',re.I),'system reboot'),(re.compile(r'\b(curl|wget)\b.*\|\s*(sh|bash)\b',re.I),'remote script execution')]
COMPOUND_RE=re.compile(r'[|;&<>`]|\$\(|\n')
# Path references that can resolve outside the workspace even though the
# command starts with a safe prefix: relative '..' segments, '~' expansions,
# and $HOME-style environment variables.
DOTDOT_RE=re.compile(r'(^|[\s"\'=/(])\.\.(?=/|$|[\s"\'")])')
TILDE_RE=re.compile(r'(^|[\s"\'=/(])~(?=/|$|[\s"\'")])')
ENV_PATH_RE=re.compile(r'\$(?:HOME|USER|PWD|OLDPWD|SHELL)\b')
INLINE_EXEC_RE=re.compile(r'\b(?:python3?|sh|bash|zsh)\s+(?:-\w+\s+)*-c\b')
FIND_EXEC_RE=re.compile(r'\bfind\s[^\n]*\s(?:-delete|-exec|-execdir|-ok)\b')
PIP_INSTALL_RE=re.compile(r'\s*pip3?\s+install\b')
def classify_command(command,workspace):
    s=command.strip()
    if not s:return Decision('deny','empty command')
    for p,r in RISK_PATTERNS:
        if p.search(s):return Decision('approve',r)
    if COMPOUND_RE.search(s):return Decision('approve','compound/redirecting command requires approval')
    if INLINE_EXEC_RE.search(s):return Decision('approve','inline script execution requires approval')
    if FIND_EXEC_RE.search(s):return Decision('approve','destructive/exec find operation requires approval')
    if PIP_INSTALL_RE.match(s.lower()):return Decision('approve','package installation executes third-party code')
    if re.search(r'(?:^|\s)/(?:home|root|etc|var|usr|opt)(?:/|\s|$)',s.lower()):return Decision('approve','host-sensitive absolute path')
    if DOTDOT_RE.search(s):return Decision('approve','relative path may escape the workspace')
    if TILDE_RE.search(s):return Decision('approve','tilde path may escape the workspace')
    if ENV_PATH_RE.search(s):return Decision('approve','environment variable may reference a path outside the workspace')
    if any(x in s for x in ('&& rm ','|| rm ','; rm ','&& sudo ','; sudo ')):return Decision('approve','compound privileged/destructive operation')
    return Decision('allow','recognized development/read-only command') if s.lower().startswith(SAFE_PREFIXES) else Decision('approve','unrecognized command; explicit approval required')
def ensure_within(root,candidate):
    root=root.resolve(); target=(root/candidate).resolve()
    try: target.relative_to(root)
    except ValueError as e: raise ValueError(f'path escapes workspace: {candidate}') from e
    return target
