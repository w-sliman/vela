from __future__ import annotations
import hashlib, difflib
from .policy import ensure_within
TRUNCATION_MARKER='\n...[truncated: file exceeds VELA_MAX_FILE_CHARS]...'
MAX_LISTING_ENTRIES=1000
class Workspace:
    def __init__(self,root,max_file_chars=30000): self.root=root.resolve(); self.max_file_chars=max_file_chars; self.root.mkdir(parents=True,exist_ok=True)
    def resolve(self,path): return ensure_within(self.root,path)
    def rel(self,p): return str(p.relative_to(self.root))
    def list_files(self,path='.',max_depth=3):
        """Workspace listing, capped at MAX_LISTING_ENTRIES."""
        return self.list_files_bounded(path,max_depth)[0]
    def list_files_bounded(self,path='.',max_depth=3):
        """Return (entries, truncated). A silently-capped listing reads as a complete
        one, so callers that can act on it are told when entries were dropped."""
        base=self.resolve(path)
        if not base.exists():raise FileNotFoundError(path)
        if not base.is_dir():raise NotADirectoryError(path)
        bd=len(base.relative_to(self.root).parts); out=[]
        for x in sorted(base.rglob('*')):
            rel=x.relative_to(self.root)
            if rel.parts[0]=='.git':continue
            d=len(rel.parts)-bd
            if d<=max_depth: out.append(f'{rel}'+('/' if x.is_dir() else ''))
        return out[:MAX_LISTING_ENTRIES],len(out)>MAX_LISTING_ENTRIES
    def read_file(self,path):
        """Bounded read; oversized files come back with TRUNCATION_MARKER appended."""
        return self.read_file_bounded(path)[0]
    def read_file_bounded(self,path):
        """Return (text, truncated). Truncated text carries TRUNCATION_MARKER so a
        caller cannot mistake a partial view for the whole file."""
        p=self.resolve(path)
        if not p.is_file():raise FileNotFoundError(path)
        text=p.read_text(encoding='utf-8')
        if len(text)<=self.max_file_chars:return text,False
        return text[:self.max_file_chars]+TRUNCATION_MARKER,True
    def read_raw(self,path):
        p=self.resolve(path); return p.read_text(encoding='utf-8')
    def hash_file(self,path): return hashlib.sha256(self.read_raw(path).encode()).hexdigest()
    def preflight_write(self,path,content,expected_hash=None):
        """Raise if this write must not happen; touch nothing. Separated from
        write_file so callers can validate *before* prompting a user to approve a
        diff that was never going to be applied."""
        if len(content)>self.max_file_chars*4:raise ValueError('content exceeds maximum write size')
        # A truncated read echoed back as a full-file write would silently destroy
        # the tail of the file; the hash guard cannot catch it (the hash is of the
        # whole file, the content is not). Fail closed instead.
        if TRUNCATION_MARKER in content:
            raise TruncatedContentError('refusing to write content containing the read truncation marker: '
                'this file was only partially read. Edit it with replace_text (start_line/end_line) or apply_patch instead of rewriting it whole.')
        if expected_hash:
            p=self.resolve(path)
            if not p.exists() or self.hash_file(path)!=expected_hash: raise ConcurrentEditError('file changed or removed since it was read')
    def write_file(self,path,content,expected_hash=None):
        self.preflight_write(path,content,expected_hash)
        p=self.resolve(path)
        p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content,encoding='utf-8'); return f'wrote {self.rel(p)}'
    def make_directory(self,path):p=self.resolve(path);p.mkdir(parents=True,exist_ok=True);return f'created directory {self.rel(p)}'
    def diff(self,old,new,path): return ''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=f'a/{path}',tofile=f'b/{path}'))
class ConcurrentEditError(RuntimeError):pass
class TruncatedContentError(ValueError):pass
