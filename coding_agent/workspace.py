from __future__ import annotations
import hashlib, difflib
from .policy import ensure_within
class Workspace:
    def __init__(self,root,max_file_chars=30000): self.root=root.resolve(); self.max_file_chars=max_file_chars; self.root.mkdir(parents=True,exist_ok=True)
    def resolve(self,path): return ensure_within(self.root,path)
    def rel(self,p): return str(p.relative_to(self.root))
    def list_files(self,path='.',max_depth=3):
        base=self.resolve(path)
        if not base.exists():raise FileNotFoundError(path)
        if not base.is_dir():raise NotADirectoryError(path)
        bd=len(base.relative_to(self.root).parts); out=[]
        for x in sorted(base.rglob('*')):
            rel=x.relative_to(self.root)
            if rel.parts[0]=='.git':continue
            d=len(rel.parts)-bd
            if d<=max_depth: out.append(f'{rel}'+('/' if x.is_dir() else ''))
        return out[:1000]
    def read_file(self,path):
        p=self.resolve(path)
        if not p.is_file():raise FileNotFoundError(path)
        text=p.read_text(encoding='utf-8'); return text if len(text)<=self.max_file_chars else text[:self.max_file_chars]+'\n...[truncated]...'
    def read_raw(self,path):
        p=self.resolve(path); return p.read_text(encoding='utf-8')
    def hash_file(self,path): return hashlib.sha256(self.read_raw(path).encode()).hexdigest()
    def write_file(self,path,content,expected_hash=None):
        if len(content)>self.max_file_chars*4:raise ValueError('content exceeds maximum write size')
        p=self.resolve(path)
        if expected_hash:
            if not p.exists() or self.hash_file(path)!=expected_hash: raise ConcurrentEditError('file changed or removed since it was read')
        p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content,encoding='utf-8'); return f'wrote {self.rel(p)}'
    def make_directory(self,path):p=self.resolve(path);p.mkdir(parents=True,exist_ok=True);return f'created directory {self.rel(p)}'
    def diff(self,old,new,path): return ''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=f'a/{path}',tofile=f'b/{path}'))
class ConcurrentEditError(RuntimeError):pass
