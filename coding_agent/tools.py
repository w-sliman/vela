from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Callable
from .config import Config
from .shell import Shell
from .workspace import Workspace,ConcurrentEditError
from .editor import patch_or_replace,replace_lines
from .search import search_text,search_symbols
from .git import Git
from .browser import Browser
from .github import GitHub
from .sandbox import DockerSandbox
from .agents import Delegator
@dataclass
class ToolContext:
 config:Config;workspace:Workspace;shell:Shell;approval_callback:Callable[[str,str],bool]
 git:Git;browser:Browser;github:GitHub;sandbox:DockerSandbox;stream_callback:Callable[[str],None]|None=None;delegator:Delegator|None=None;events:object|None=None;on_tool_result:Callable[[str,str],None]|None=None;todos:list|None=None

_TODO_STATUSES=('pending','in_progress','done')
def normalize_todos(raw):
    """Validate a full todo-list replacement: <=12 items, one imperative line each,
    known statuses (unknown -> pending), exact duplicates dropped, junk skipped."""
    out=[];seen=set()
    if not isinstance(raw,list):return out
    for item in raw[:24]:
        if isinstance(item,str):item={'text':item}
        if not isinstance(item,dict):continue
        text=str(item.get('text') or '').strip()
        if not text:continue
        status=str(item.get('status') or 'pending').strip().lower()
        if status not in _TODO_STATUSES:status='pending'
        text=text[:120]
        if text.lower() in seen:continue
        seen.add(text.lower());out.append({'text':text,'status':status})
        if len(out)>=12:break
    return out
def diff_todos(old,new):
    """Deterministic change summary between two todo lists, matched by text."""
    def idx(items):return {str(t.get('text')).lower():str(t.get('status','pending')) for t in items}
    o,n=idx(old),idx(new)
    return {'completed':[t for t,s in n.items() if s=='done' and o.get(t)!='done'],
            'reopened':[t for t,s in n.items() if s!='done' and o.get(t)=='done'],
            'added':[t for t in n if t not in o],
            'removed':[t for t in o if t not in n],
            'in_progress':[t for t,s in n.items() if s=='in_progress']}

def fn(name,desc,props=None,req=None):return {'type':'function','name':name,'description':desc,'parameters':{'type':'object','properties':props or {},'required':req or []}}
def tool_schemas():
 return [
 fn('list_files','List workspace files/directories.',{'path':{'type':'string'},'max_depth':{'type':'integer'}},[]),
 fn('read_file','Read a file. Returns content plus a SHA-256 hash for safe editing.',{'path':{'type':'string'}},['path']),
 fn('write_file','Create/replace a file. Prefer apply_patch or replace_text for edits.',{'path':{'type':'string'},'content':{'type':'string','maxLength':12000},'expected_hash':{'type':'string'}},['path','content']),
 fn('replace_text','Replace text in a file. Two modes: (a) exact old->new text replacement, (b) line range start_line..end_line (1-based, inclusive) replaced verbatim by new. If it fails, the tool returns structured recovery data, often including closest-match lines; re-read and retry.',{'path':{'type':'string'},'old':{'type':'string','maxLength':8000},'new':{'type':'string','maxLength':8000},'occurrence':{'type':'integer'},'expected_hash':{'type':'string'},'fuzzy':{'type':'boolean'},'start_line':{'type':'integer','description':'1-based first line to replace (alternative to old)'},'end_line':{'type':'integer','description':'1-based last line to replace (defaults to start_line)'}},['path','new']),
 fn('apply_patch','Apply a unified diff with context validation and return the resulting diff.',{'path':{'type':'string'},'patch':{'type':'string','maxLength':16000},'expected_hash':{'type':'string'}},['path','patch']),
 fn('make_directory','Create a directory.',{'path':{'type':'string'}},['path']),
 fn('search_text','Regex search across workspace text.',{'query':{'type':'string'},'max_results':{'type':'integer'}},['query']),
 fn('search_symbols','Find Python functions/classes via AST: qualified names (Class.method), kind, line span, signature.',{'query':{'type':'string'}},[]),
 fn('run_command','Run a shell command subject to policy/approval.',{'command':{'type':'string'},'timeout':{'type':'integer'}},['command']),
 fn('run_tests','Select/run tests relevant to changed paths.',{'paths':{'type':'array','items':{'type':'string'}},'command':{'type':'string'}},[]),
 fn('git_status','Show Git status.',{},[]),fn('git_diff','Show Git diff.',{'staged':{'type':'boolean'}},[]),
 fn('git_checkpoint','Create a Git checkpoint; always requires approval.',{'message':{'type':'string'}},['message']),
 fn('remember','Persist a durable project fact/decision/preference to long-term memory.',{'kind':{'type':'string','description':'fact, decision, or preference'},'text':{'type':'string'},'tags':{'type':'array','items':{'type':'string'},'description':'short topical tags, e.g. testing/style'},'paths':{'type':'array','items':{'type':'string'},'description':'workspace paths this memory relates to'}},['kind','text']),
 fn('recall_memory','Read persistent project memory (all records).',{},[]),
 fn('forget_memory','Delete persistent memory records by id prefix (ids shown by recall_memory).',{'id':{'type':'string'}},['id']),
 fn('browser_fetch','Fetch a web page when browser support is enabled.',{'url':{'type':'string'}},['url']),
 fn('browser_open','Open a page in a real headless Chromium browser when enabled.',{'url':{'type':'string'}},['url']),
 fn('github_get','Read a GitHub API resource when enabled.',{'path':{'type':'string'}},['path']),
 fn('sandbox_run','Run a command in an optional no-network Docker sandbox.',{'command':{'type':'string'}},['command']),
 fn('delegate_role','Ask an isolated planner/reviewer sub-agent for analysis. It cannot edit files.',{'role':{'type':'string','enum':['planner','reviewer']},'task':{'type':'string'}},['role','task']),
 fn('write_todos','Write the full working todo list, replacing the previous one. Use for non-trivial multi-step tasks: lay out concrete steps before starting, keep exactly one in_progress, mark done immediately with evidence, add discovered work, drop obsolete items.',{'todos':{'type':'array','items':{'type':'object','properties':{'text':{'type':'string','description':'one imperative step'},'status':{'type':'string','enum':['pending','in_progress','done']}},'required':['text','status']}}},['todos']),
 ]

_REQ={}
def _build_req():
 global _REQ
 _REQ={s['name']:tuple(s['parameters'].get('required',())) for s in tool_schemas()}
_build_req()

def _checkpoint(ctx,label):
    """Best-effort post-edit snapshot; git problems never break editing."""
    if not getattr(ctx.config,'auto_checkpoint',False) or not ctx.git:return None
    try:return ctx.git.snapshot(label)
    except Exception:return 'failed'

def _dispatch_impl(ctx,name,a):
 try:
  missing=[k for k in _REQ.get(name,()) if k not in a]
  if missing:raise ValueError(f'missing required argument(s): {", ".join(missing)}')
  if name=='list_files':return json.dumps(ctx.workspace.list_files(a.get('path','.'),int(a.get('max_depth',3))),indent=2)
  if name=='read_file':
   return json.dumps({'path':a['path'],'content':ctx.workspace.read_file(a['path']),'sha256':ctx.workspace.hash_file(a['path'])},indent=2)
  if name=='write_file':
   old='';
   try:old=ctx.workspace.read_raw(a['path'])
   except FileNotFoundError:pass
   result=ctx.workspace.write_file(a['path'],a['content'],a.get('expected_hash'))
   cp=_checkpoint(ctx,f'auto: write_file {a["path"]}')
   return json.dumps({'status':'completed','message':result,'diff':ctx.workspace.diff(old,a['content'],a['path']),'checkpoint':cp},indent=2)
  if name in {'replace_text','apply_patch'}:
   path=a['path']; original=ctx.workspace.read_raw(path); expected=a.get('expected_hash')
   if name=='replace_text' and bool(a.get('fuzzy')) and not expected:raise ConcurrentEditError('fuzzy replacement requires expected_hash; re-read the file and pass its sha256')
   if expected and ctx.workspace.hash_file(path)!=expected:raise ConcurrentEditError('file changed since last read; re-read the file and retry')
   if name=='replace_text' and a.get('start_line') is not None:
    updated=replace_lines(original,int(a['start_line']),int(a.get('end_line') or a['start_line']),a['new'])
   else:
    updated=patch_or_replace(original,path,a.get('patch'),a.get('old'),a.get('new'),int(a.get('occurrence',1)),bool(a.get('fuzzy',False)))
   ctx.workspace.write_file(path,updated,expected)
   cp=_checkpoint(ctx,f'auto: {name} {path}')
   return json.dumps({'status':'completed','diff':ctx.workspace.diff(original,updated,path),'sha256':ctx.workspace.hash_file(path),'checkpoint':cp},indent=2)
  if name=='make_directory':return ctx.workspace.make_directory(a['path'])
  if name=='search_text':return json.dumps(search_text(ctx.workspace.root,a['query'],int(a.get('max_results',100))),indent=2)
  if name=='search_symbols':return json.dumps(search_symbols(ctx.workspace.root,a.get('query','')),indent=2)
  if name=='run_command':return _run(ctx,a['command'],int(a.get('timeout',ctx.config.command_timeout)))
  if name=='run_tests':
   cmd=a.get('command') or _select_tests(ctx.workspace.root,a.get('paths',[]));return _run(ctx,cmd,ctx.config.command_timeout)
  if name=='git_status':
   r=ctx.git.status();return json.dumps({'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2)
  if name=='git_diff':
   r=ctx.git.diff(bool(a.get('staged')));return json.dumps({'returncode':r.returncode,'stdout':r.stdout[:ctx.config.max_tool_output],'stderr':r.stderr},indent=2)
  if name=='git_checkpoint':
   if not ctx.approval_callback('git add -A && git commit','creating a Git checkpoint changes repository history'):return json.dumps({'status':'denied'})
   r=ctx.git.checkpoint(a['message']);return json.dumps({'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2)
  if name=='remember':
   from .memory import ProjectMemory; p=ProjectMemory(ctx.workspace.root); rid=p.add(a['kind'],a['text'],a.get('tags'),a.get('paths'));p.prune(getattr(ctx.config,'memory_max_records',None),getattr(ctx.config,'memory_ttl_days',0));return f'memory saved ({rid})'
  if name=='recall_memory':
   from .memory import ProjectMemory;return ProjectMemory(ctx.workspace.root).text()
  if name=='forget_memory':
   from .memory import ProjectMemory; n=ProjectMemory(ctx.workspace.root).forget(a['id']);return json.dumps({'status':'completed','removed':n} if n else {'status':'completed','removed':0,'message':f'no memory id matching {a["id"]}'} ,indent=2)
  if name=='browser_fetch':return ctx.browser.fetch(a['url'])
  if name=='browser_open':return json.dumps(ctx.browser.open(a['url']),indent=2)
  if name=='github_get':return json.dumps(ctx.github.request('GET',a['path']),indent=2)[:ctx.config.max_tool_output]
  if name=='sandbox_run':
   r=ctx.sandbox.run(a['command']);return json.dumps({'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2)
  if name=='delegate_role':
   if not ctx.delegator: return json.dumps({'status':'error','message':'delegation unavailable'})
   return ctx.delegator.run(a['role'],a['task'])
  if name=='write_todos':
   old=list(ctx.todos or []);new=normalize_todos(a.get('todos'));ctx.todos=new
   return json.dumps({'status':'completed','todos':new,'diff':diff_todos(old,new)},indent=2)
  raise ValueError(f'unknown tool: {name}')
 except Exception as e:
  payload={'status':'error','error_type':type(e).__name__,'message':str(e)}
  if name in {'replace_text','apply_patch','write_file'}:payload['recovery']='Re-read the file, inspect the current content/hash, then retry with a fresh patch. Do not guess the target text.'
  return json.dumps(payload,indent=2)

def dispatch(ctx,name,a):
    if getattr(ctx, 'events', None):
        ctx.events.emit('start', f'tool: {name}', tool=name)
    result = _dispatch_impl(ctx, name, a)
    if getattr(ctx, 'events', None):
        try:
            status = json.loads(result).get('status', 'completed') if isinstance(result, str) else 'completed'
        except Exception:
            status = 'completed'
        ctx.events.emit('error' if status == 'error' else 'done', f'tool: {name}', status=status)
    cb = getattr(ctx, 'on_tool_result', None)
    if cb:
        cb(name, result)
    return result

def _run(ctx,command,timeout):
 d=ctx.shell.classify(command)
 if d.action=='deny':return json.dumps({'status':'denied','reason':d.reason})
 if d.action=='approve' and not ctx.approval_callback(command,d.reason):return json.dumps({'status':'denied','reason':'user declined approval'})
 r=ctx.shell.run(command,approved=True,timeout=timeout,on_output=ctx.stream_callback);return json.dumps({'status':'completed','returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'policy':r.decision.action},indent=2)
def _select_tests(root,paths):
 py=[p for p in paths if str(p).endswith('.py')]
 if not py:return 'pytest -q'
 tests=[]
 for p in py:
  stem=str(p).rsplit('/',1)[-1].replace('.py','')
  cand=list(root.rglob(f'test_{stem}.py'))+list(root.rglob(f'{stem}_test.py'))
  tests += [str(x.relative_to(root)) for x in cand]
 return 'pytest -q '+' '.join(dict.fromkeys(tests)) if tests else 'pytest -q'
