"""Cross-session resume: index past traces and rebuild task state as a digest.

Resume is digest-based, not faithful replay: tool pairs are never reconstructed,
so history/pair integrity holds by construction regardless of which transport
(originally) produced the trace.
"""
from __future__ import annotations
import json

def _sessions_dir(workspace):return workspace/'.vela'/'sessions'
def _trunc(s,n):s=str(s).strip();return s[:n]+'…' if len(s)>n else s

def list_sessions(workspace,exclude=None,limit=20):
    """Newest-first index of session traces (id, mtime epoch, user turns, first request)."""
    d=_sessions_dir(workspace);out:list[dict]=[]
    if not d.exists():return out
    for f in sorted(d.glob('*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True):
        if exclude is not None and f==exclude:continue
        first='';turns=0
        try:
            with f.open(encoding='utf-8') as fh:
                for line in fh:
                    try:e=json.loads(line)
                    except Exception:continue
                    if e.get('kind')=='user':
                        turns+=1
                        if not first:first=_trunc((e.get('payload') or {}).get('text',''),120)
        except OSError:continue
        out.append({'id':f.stem,'path':f,'mtime':int(f.stat().st_mtime),'turns':turns,'first_user':first})
        if len(out)>=limit:break
    return out

def resolve_session(workspace,ref=None,exclude=None):
    """Resolve /resume ref -> (session dict|None, error|None).

    Grammar: 'last'/None = newest *resumable* session; '#N' or a 1-2 digit
    number = Nth newest resumable (1-based); anything else = session-id prefix
    match (ids are UTC timestamps, so longer digit strings always mean
    prefixes, never indexes). Zero-request traces are never resumable.
    """
    sess=[s for s in list_sessions(workspace,exclude=exclude,limit=200) if s['turns']>0]
    if not sess:return None,'no resumable sessions (traces with recorded requests) found'
    if ref is None or str(ref).strip() in ('','last'):return sess[0],None
    ref=str(ref).strip()
    def by_index(i):
        if 1<=i<=len(sess):return sess[i-1],None
        return None,f'session #{i} out of range (1..{len(sess)})'
    if ref.startswith('#'):
        return by_index(int(ref[1:])) if ref[1:].isdigit() else (None,f'bad index {ref!r}')
    if ref.isdigit() and len(ref)<=2:return by_index(int(ref))
    hits=[s for s in sess if s['id'].startswith(ref)]
    if len(hits)==1:return hits[0],None
    if not hits:return None,f'no session id starting with {ref!r}'
    return None,f'ambiguous session prefix {ref!r} ({len(hits)} matches)'

def build_digest(path,max_chars=6000):
    """Mechanical task-state summary of one trace: requests, touched files, last answer."""
    requests=[];files=[];last_answer='';compactions=0;errors=0;started='';last_todos=None
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            try:e=json.loads(line)
            except Exception:continue
            k=e.get('kind');p=e.get('payload') or {}
            if not started and e.get('timestamp'):started=str(e['timestamp'])
            if k=='user':requests.append(str(p.get('text','')))
            elif k=='tool_call':
                raw=p.get('arguments_raw')
                try:a=json.loads(raw) if isinstance(raw,str) else (raw or {})
                except Exception:a={}
                for key in ('path','paths'):
                    v=a.get(key)
                    if isinstance(v,str):files.append(v)
                    elif isinstance(v,list):files.extend(str(x) for x in v)
            elif k=='assistant':
                t=str(p.get('text','')).strip()
                if t:last_answer=t
            elif k=='compact':
                if p.get('compacted'):compactions+=1
            elif k=='error':errors+=1
            elif k=='todos_updated':last_todos=p.get('todos') or []
    files=list(dict.fromkeys(files))
    def compose(reqs):
        out=[f'[Resumed session {path.stem}] Task state reconstructed from the trace — verify current file contents before editing.',
             f'Started: {started[:19]}',
             'User requests in that session:' if reqs else 'No user requests were recorded.']
        if len(requests)>len(reqs):out.append(f'(…{len(requests)-len(reqs)} earlier request(s) omitted)')
        base=len(requests)-len(reqs)
        for i,r in enumerate(reqs,base+1):out.append(f'{i}. {_trunc(r,200) or "(empty)"}')
        if files:out.append('Files touched: '+', '.join(files[:15])+(f' (+{len(files)-15} more)' if len(files)>15 else ''))
        if compactions:out.append(f'Compactions already applied: {compactions}')
        if errors:out.append(f'Recorded transport errors: {errors}')
        open_items=[t for t in (last_todos or []) if str(t.get('status'))!='done']
        if open_items:
            out.append('Open todos left by that session (pick up where it stopped):')
            out+= [f"- [{t.get('status')}] {t.get('text')}" for t in open_items[:12]]
        if last_answer:out.append('Last assistant message (tail): '+_trunc(last_answer,400))
        return '\n'.join(out)
    shown=requests[-20:]
    text=compose(shown)
    while len(text)>max_chars and shown:
        shown.pop(0);text=compose(shown)
    return {'text':text,'requests':len(requests),'files':files,'turns':max(1,len(requests)),'chars':len(text)}
