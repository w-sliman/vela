from pathlib import Path
import ast,os,re
IGNORED={'.git','.venv','venv','__pycache__','.pytest_cache','node_modules','.vela'}
MAX_SYMBOLS=500
def _files(root):
 for cur,dirs,files in os.walk(root):
  dirs[:]=[d for d in dirs if d not in IGNORED]
  for f in files:yield Path(cur)/f
def search_text(root,query,max_results=100):
 pat=re.compile(query,re.I);out=[]
 for p in _files(root):
  try:lines=p.read_text(encoding='utf-8').splitlines()
  except (UnicodeDecodeError,OSError):continue
  for n,line in enumerate(lines,1):
   if pat.search(line):out.append({'path':str(p.relative_to(root)),'line':n,'text':line[:500]})
   if len(out)>=max_results:return out
 return out

def _signature(node):
 """Human-readable parameter list via the AST itself."""
 try:return f'({ast.unparse(node.args)})'
 except Exception:return '(...)'

def _class_signature(node):
 try:bases=', '.join(ast.unparse(b) for b in node.bases)
 except Exception:bases=''
 return f'({bases})'

def _regex_symbols(path,rel):
 """Fallback line scan for files that fail to parse."""
 out:list[dict]=[]
 try:lines=path.read_text(encoding='utf-8').splitlines()
 except (UnicodeDecodeError,OSError):return out
 for n,line in enumerate(lines,1):
  m=re.match(r'\s*(async\s+)?def\s+([A-Za-z_]\w*)\s*\(',line) or re.match(r'\s*class\s+([A-Za-z_]\w*)\s*[:(]',line)
  if m:
   name=m.group(2);kind='class' if m.group(0).lstrip().startswith('class') else ('async def' if m.group(1) else 'def')
   out.append({'path':rel,'line':n,'end_line':None,'symbol':name,'kind':kind,'signature':'(?)'})
 return out

def _scan_symbols(path,rel):
 """AST walk collecting defs/classes with qualified names, spans, signatures.

 Falls back to a line-regex scan for files that fail to parse, so odd-syntax
 files still contribute simple matches instead of disappearing.
 """
 try:tree=ast.parse(path.read_text(encoding='utf-8',errors='replace'))
 except (SyntaxError,ValueError):return _regex_symbols(path,rel)
 out=[]
 def visit(node,parents):
  for child in ast.iter_child_nodes(node):
   if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef)):
    kind='async def' if isinstance(child,ast.AsyncFunctionDef) else 'def'
    out.append({'path':rel,'line':child.lineno,'end_line':getattr(child,'end_lineno',None),
                'symbol':'.'.join(parents+[child.name]),'kind':kind,'signature':_signature(child)})
    visit(child,parents+[child.name])
   elif isinstance(child,ast.ClassDef):
    out.append({'path':rel,'line':child.lineno,'end_line':getattr(child,'end_lineno',None),
                'symbol':'.'.join(parents+[child.name]),'kind':'class','signature':_class_signature(child)})
    visit(child,parents+[child.name])
   else:visit(child,parents)
 visit(tree,[])
 return out

def search_symbols(root,query=''):
 """AST-based symbol index: qualified names (Class.method), kinds, spans, signatures."""
 out=[]
 for p in _files(root):
  if p.suffix!='.py':continue
  try:out.extend(_scan_symbols(p,str(p.relative_to(root))))
  except OSError:continue
  if len(out)>=MAX_SYMBOLS:break
 q=query.lower()
 if q:out=[s for s in out if q in s['symbol'].lower()]
 return sorted(out,key=lambda s:(s['path'],s['line']))[:MAX_SYMBOLS]
