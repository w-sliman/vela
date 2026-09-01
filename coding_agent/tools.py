from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Callable
from .config import Config
from .shell import Shell
from .workspace import Workspace,ConcurrentEditError
from .editor import ensure_no_syntax_regression,patch_or_replace,replace_lines
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
    out:list[dict]=[];seen:set[str]=set()
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
 fn('read_file','Read a file. Returns content, a SHA-256 hash for safe editing, and a truncated flag when the file exceeded the read limit.',{'path':{'type':'string'}},['path']),
 fn('write_file','Create a file, or replace one whole. Prefer apply_patch or replace_text for edits. expected_hash is REQUIRED when the file already exists (read_file gives it); creating a new file needs none.',{'path':{'type':'string'},'content':{'type':'string','maxLength':12000},'expected_hash':{'type':'string'}},['path','content']),
 fn('replace_text','Replace text in a file. Two modes: (a) exact old->new text replacement, (b) line range start_line..end_line (1-based, inclusive) replaced verbatim by new; both start_line and end_line are REQUIRED together in this mode, as is expected_hash; the range is REPLACED verbatim, never inserted before. If it fails, the tool returns structured recovery data, often including closest-match lines; re-read and retry.',{'path':{'type':'string'},'old':{'type':'string','maxLength':8000},'new':{'type':'string','maxLength':8000},'occurrence':{'type':'integer'},'expected_hash':{'type':'string'},'fuzzy':{'type':'boolean'},'start_line':{'type':'integer','description':'1-based first line to replace (alternative to old)'},'end_line':{'type':'integer','description':'1-based last line to replace; required whenever start_line is given'}},['path','new']),
 fn('apply_patch','Apply a unified diff with context validation and return the resulting diff.',{'path':{'type':'string'},'patch':{'type':'string','maxLength':16000},'expected_hash':{'type':'string'}},['path','patch']),
 fn('make_directory','Create a directory.',{'path':{'type':'string'}},['path']),
 fn('search_text','Regex search across workspace text.',{'query':{'type':'string'},'max_results':{'type':'integer'}},['query']),
 fn('search_symbols','Find Python functions/classes via AST: qualified names (Class.method), kind, line span, signature.',{'query':{'type':'string'}},[]),
 fn('run_command','Run a shell command subject to policy/approval.',{'command':{'type':'string'},'timeout':{'type':'integer','description':'seconds; clamped to the configured ceiling (CODER_COMMAND_TIMEOUT)'}},['command']),
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

_REQ:dict[str,tuple[str,...]]={};_MAXLEN:dict[str,dict[str,int]]={}
def _build_req():
 """Index the schemas once: required arguments, and declared string limits.

 The schemas are the single source of truth for the tool contract, but a model
 treats `maxLength` as a hint — one silently sent 18k characters into a field
 documented at 8k, and the oversized replacement mangled the file. Indexing the
 limits here lets the dispatcher hold the model to what the schema advertises.
 """
 global _REQ,_MAXLEN
 schemas=tool_schemas()
 _REQ={s['name']:tuple(s['parameters'].get('required',())) for s in schemas}
 _MAXLEN={s['name']:{k:v['maxLength'] for k,v in s['parameters'].get('properties',{}).items()
                     if isinstance(v,dict) and isinstance(v.get('maxLength'),int)}
          for s in schemas}
_build_req()

def _enforce_limits(name,a):
 """Reject arguments longer than the schema advertises, before anything is written."""
 for key,cap in _MAXLEN.get(name,{}).items():
  value=a.get(key)
  if isinstance(value,str) and len(value)>cap:
   raise ValueError(f'{name}.{key} is {len(value):,} characters; the limit is {cap:,}. '
    'Split this into smaller edits targeting the specific regions you are changing '
    '— an oversized replacement is where content gets silently dropped.')

def _approve_edit(ctx,label,old,new,path):
 """Optional consent gate for file edits (CODER_APPROVAL_EDITS=1): show the
 computed diff through the approval layer before anything is written."""
 if not getattr(ctx.config,'approval_edits',False):return True
 d=ctx.workspace.diff(old,new,path)
 preview=d if len(d)<=1200 else d[:1170]+'\n… [truncated]'
 return bool(ctx.approval_callback(label,preview))
def _checkpoint(ctx,label):
    """Best-effort post-edit snapshot; git problems never break editing."""
    if not getattr(ctx.config,'auto_checkpoint',False) or not ctx.git:return None
    try:return ctx.git.snapshot(label)
    except Exception:return 'failed'

def _dispatch_impl(ctx,name,a):
 try:
  missing=[k for k in _REQ.get(name,()) if k not in a]
  if missing:raise ValueError(f'missing required argument(s): {", ".join(missing)}')
  _enforce_limits(name,a)
  if name=='list_files':
   entries,truncated=ctx.workspace.list_files_bounded(a.get('path','.'),int(a.get('max_depth',3)))
   out={'path':a.get('path','.'),'entries':entries,'truncated':truncated}
   if truncated:out['warning']='listing was capped; narrow it with a subdirectory path or a smaller max_depth.'
   return json.dumps(out,indent=2)
  if name=='read_file':
   text,truncated=ctx.workspace.read_file_bounded(a['path'])
   out={'path':a['path'],'content':text,'sha256':ctx.workspace.hash_file(a['path']),'truncated':truncated}
   if truncated:out['warning']=('only the first part of this file is shown; sha256 covers the WHOLE file. '
     'Do not rewrite this file with write_file — edit it with replace_text (start_line/end_line) or apply_patch.')
   return json.dumps(out,indent=2)
  if name=='write_file':
   old='';existed=True
   try:old=ctx.workspace.read_raw(a['path'])
   except FileNotFoundError:existed=False
   # Validate first: approving a diff that then fails on a stale hash wastes the
   # user's decision and reads as a bug.
   if existed and not a.get('expected_hash'):
    raise ConcurrentEditError('overwriting an existing file requires expected_hash; re-read '
     f'{a["path"]} and pass its sha256. Without it this write cannot tell whether the file '
     'changed since you read it, and would silently discard any edit made in the meantime.')
   ctx.workspace.preflight_write(a['path'],a['content'],a.get('expected_hash'))
   ensure_no_syntax_regression(a['path'],old,a['content'])
   if not _approve_edit(ctx,f'edit {a["path"]}',old,a['content'],a['path']):return json.dumps({'status':'denied','reason':'user declined this edit'},indent=2)
   result=ctx.workspace.write_file(a['path'],a['content'],a.get('expected_hash'))
   cp=_checkpoint(ctx,f'auto: write_file {a["path"]}')
   return json.dumps({'status':'completed','message':result,'diff':ctx.workspace.diff(old,a['content'],a['path']),'checkpoint':cp},indent=2)
  if name in {'replace_text','apply_patch'}:
   path=a['path']; original=ctx.workspace.read_raw(path); expected=a.get('expected_hash')
   if name=='replace_text' and bool(a.get('fuzzy')) and not expected:raise ConcurrentEditError('fuzzy replacement requires expected_hash; re-read the file and pass its sha256')
   if expected and ctx.workspace.hash_file(path)!=expected:raise ConcurrentEditError('file changed since last read; re-read the file and retry')
   if name=='replace_text' and a.get('start_line') is not None:
    # A line range is positional: nothing in it is checked against the text being
    # replaced, so the hash is the only thing standing between a mis-counted range
    # and a silently mangled file.
    if not expected:raise ConcurrentEditError('replacing a line range requires expected_hash; '
     f're-read {path} and pass its sha256 along with the line numbers you saw.')
    # end_line is required rather than defaulting to start_line. Defaulting reads as
    # "insert here" to a model rewriting a region, and silently replaced one line
    # instead — duplicating the body it meant to supersede. The result is valid
    # Python, so no syntax check catches it; only stating the range can.
    if a.get('end_line') is None:raise ValueError(
     f'replace_text on {path} needs end_line: start_line..end_line is REPLACED verbatim, '
     'and omitting end_line would overwrite only that one line. Pass end_line equal to '
     'start_line to replace a single line, or the last line of the region you are rewriting.')
    updated=replace_lines(original,int(a['start_line']),int(a['end_line']),a['new'])
   else:
    updated=patch_or_replace(original,path,a.get('patch'),a.get('old'),a.get('new'),int(a.get('occurrence',1)),bool(a.get('fuzzy',False)))
   ensure_no_syntax_regression(path,original,updated)
   if not _approve_edit(ctx,f'edit {path}',original,updated,path):return json.dumps({'status':'denied','reason':'user declined this edit'},indent=2)
   ctx.workspace.write_file(path,updated,expected)
   cp=_checkpoint(ctx,f'auto: {name} {path}')
   return json.dumps({'status':'completed','diff':ctx.workspace.diff(original,updated,path),'sha256':ctx.workspace.hash_file(path),'checkpoint':cp},indent=2)
  if name=='make_directory':return ctx.workspace.make_directory(a['path'])
  if name=='search_text':return json.dumps(search_text(ctx.workspace.root,a['query'],int(a.get('max_results',100))),indent=2)
  if name=='search_symbols':return json.dumps(search_symbols(ctx.workspace.root,a.get('query','')),indent=2)
  if name=='run_command':return _run(ctx,a['command'],_timeout(ctx,a.get('timeout')))
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
    d=ctx.shell.classify(a['command'])
    if d.action=='deny':return json.dumps({'status':'denied','reason':d.reason},indent=2)
    if d.action=='approve' and not ctx.approval_callback(a['command'],d.reason):return json.dumps({'status':'denied','reason':'user declined approval'},indent=2)
    r=ctx.sandbox.run(a['command']);return json.dumps({'status':'completed','returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2)
  if name=='delegate_role':
   if not ctx.delegator: return json.dumps({'status':'error','message':'delegation unavailable'})
   return ctx.delegator.run(a['role'],a['task'])
  if name=='write_todos':
   previous=list(ctx.todos or []);new=normalize_todos(a.get('todos'));ctx.todos=new
   return json.dumps({'status':'completed','todos':new,'diff':diff_todos(previous,new)},indent=2)
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

def _timeout(ctx,requested):
 """Clamp a model-supplied timeout to 1..config.command_timeout seconds.

 The model may ask for less than the configured ceiling but never more, and
 junk values fall back to the ceiling rather than raising or hanging the REPL.
 """
 ceiling=int(ctx.config.command_timeout)
 if requested is None:return ceiling
 try:want=int(requested)
 except (TypeError,ValueError):return ceiling
 return max(1,min(want,ceiling))
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
