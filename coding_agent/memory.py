from __future__ import annotations
import json,math,re,datetime

_STOP={'the','a','an','and','or','of','to','in','for','on','is','are','be','was','were','it','its','this','that','these','those','with','as','at','by','from','use','uses','used','using','when','if','not','do','does','did','can','will','should'}
_TOK=re.compile(r'[a-z0-9_][a-z0-9_.\-/]*')
_SUB=re.compile(r'[./_\-]+')

def tokenize(text):
    """Lowercase tokens; path/dotted tokens are kept whole AND split into sub-tokens."""
    out=[]
    for t in _TOK.findall(str(text).lower()):
        if len(t)<2 or t in _STOP:continue
        out.append(t)
        if '/' in t or '.' in t:
            out.extend(s for s in _SUB.split(t) if len(s)>=2 and s not in _STOP)
    return out

def _pathish(values):
    """Path-shaped tokens (contain / or .) plus their sub-tokens, from any mix of paths/free text."""
    out=set()
    for v in values:
        for t in _TOK.findall(str(v).lower()):
            if '/' in t or '.' in t:
                out.add(t);out.update(s for s in _SUB.split(t) if len(s)>=2 and s not in _STOP)
    return out

def _idf(records):
    df={};n=max(1,len(records))
    for r in records:
        for t in set(tokenize(r.get('text','')))|set(tokenize(' '.join(r.get('tags',[])))):df[t]=df.get(t,0)+1
    return {t:math.log(1+n/(1+d)) for t,d in df.items()}

def _rtime(r):
    for k in ('last_seen','created'):
        v=r.get(k)
        if v:
            try:return datetime.datetime.fromisoformat(str(v))
            except ValueError:pass
    return datetime.datetime(1970,1,1,tzinfo=datetime.timezone.utc)

def score_record(r,q_tokens,q_paths,idf,now):
    """Field-weighted lexical relevance: text 1x, tags 2x, path tokens 3x, plus a flat
    bonus when the record touches any actively-edited path; recency+usage decay."""
    rt=set(tokenize(r.get('text','')));tt=set(tokenize(' '.join(r.get('tags',[]))));pt=_pathish(list(r.get('paths',[]))+[r.get('text','')])
    s=0.0
    for t in q_tokens:
        w=idf.get(t,0.5)
        if t in rt:s+=w
        if t in tt:s+=2*w
        if t in pt:s+=3*w
    if pt&q_paths:s+=1.5
    if s<=0:return 0.0
    days=max(0.0,(now-_rtime(r)).total_seconds()/86400)
    return round(s*math.exp(-days/90)*(1+math.log1p(int(r.get('hits',0)))),4)

def render_record(r):
    tags=f" (tags: {', '.join(r['tags'])})" if r.get('tags') else ''
    return f"[{r.get('id','?')}/{r.get('kind','fact')}] {r.get('text','')}{tags}"

def select_records(records,query,active_paths=(),top_k=4,min_score=0.5,max_chars=1500,exclude=(),now=None):
    """Rank records lexically against the query + active workspace paths.

    Deterministic: ties break by id. Applies min-score threshold, exclusion set,
    top-k cap, then a rendered-char budget. Returns [(score, record), ...].
    """
    now=now or datetime.datetime.now(datetime.timezone.utc);idf=_idf(records)
    qt=set(tokenize(query));qp=_pathish(active_paths);skip={str(x) for x in exclude}
    scored=[(score_record(r,qt,qp,idf,now),r.get('id',''),r) for r in records if str(r.get('id','')) not in skip]
    scored=[x for x in scored if x[0]>=min_score]
    scored.sort(key=lambda x:(-x[0],x[1]))
    out=[];used=0
    for sc,rid,r in scored[:max(0,int(top_k))]:
        line=render_record(r)
        if used+len(line)>max_chars:break
        out.append((sc,r));used+=len(line)+1
    return out

def _now_iso():return datetime.datetime.now(datetime.timezone.utc).isoformat()

class ProjectMemory:
    def __init__(self,root):self.path=root/'.coder-agent'/'memory.json';self.path.parent.mkdir(parents=True,exist_ok=True)
    def _read(self):
        if not self.path.exists():return {'version':2,'records':[]}
        try:
            d=json.loads(self.path.read_text())
            return d if isinstance(d,dict) else {'version':2,'records':[]}
        except Exception:return {'version':2,'records':[]}
    def load(self):
        d=self._read()
        return self._migrate(d) if 'records' not in d else d
    def _migrate(self,old):
        """Legacy buckets {'facts':[{'text','timestamp'},...]} -> versioned record list."""
        recs=[]
        for kind,items in old.items():
            if not isinstance(items,list):continue
            for it in items:
                if isinstance(it,str):it={'text':it}
                ts=str(it.get('timestamp') or it.get('created') or _now_iso())
                recs.append({'id':f'r{len(recs)+1}','kind':kind,'text':str(it.get('text','')),'tags':[],'paths':[],'created':ts,'last_seen':ts,'hits':0})
        d={'version':2,'records':recs};self._write(d);return d
    def _write(self,d):self.path.write_text(json.dumps(d,indent=2))
    def records(self):return self.load()['records']
    def _next_id(self,recs):
        mx=0
        for r in recs:
            m=re.fullmatch(r'r(\d+)',str(r.get('id','')))
            if m:mx=max(mx,int(m.group(1)))
        return f'r{mx+1}'
    def add(self,kind,text,tags=None,paths=None):
        """Append a record; exact kind+text duplicates update last_seen instead of piling up."""
        d=self.load();recs=d['records'];norm=' '.join(str(text).split())
        for r in recs:
            if r.get('kind')==kind and ' '.join(str(r.get('text','')).split())==norm:
                r['last_seen']=_now_iso();self._write(d);return r['id']
        now=_now_iso();rid=self._next_id(recs)
        recs.append({'id':rid,'kind':kind,'text':str(text),'tags':[str(t) for t in (tags or [])],'paths':[str(p) for p in (paths or [])],'created':now,'last_seen':now,'hits':0})
        self._write(d);return rid
    def forget(self,rid):
        """Remove records whose id starts with the given prefix; returns count removed."""
        d=self.load();want=str(rid)
        kept=[r for r in d['records'] if not str(r.get('id','')).startswith(want)]
        removed=len(d['records'])-len(kept)
        if removed:d['records']=kept;self._write(d)
        return removed
    def touch(self,ids):
        """Bump hits/last_seen for injected records so future ranking self-tunes."""
        if not ids:return
        d=self.load();want={str(i) for i in ids};now=_now_iso()
        for r in d['records']:
            if str(r.get('id')) in want:r['hits']=int(r.get('hits',0))+1;r['last_seen']=now
        self._write(d)
    def prune(self,max_records=None,ttl_days=0):
        """Deterministic cleanup: drop records untouched longer than ttl_days (when >0),
        then lowest-hits/oldest-last_seen until within max_records. Returns removed ids."""
        d=self.load();recs=list(d['records']);removed=[]
        if ttl_days and recs:
            try:cutoff=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=float(ttl_days))
            except ValueError:cutoff=None
            if cutoff is not None:
                stale=[r for r in recs if _rtime(r)<cutoff]
                if stale:
                    recs=[r for r in recs if r not in stale];removed+=[str(r['id']) for r in stale]
        if max_records is not None and len(recs)>int(max_records):
            cap=int(max_records)
            order=sorted(range(len(recs)),key=lambda i:(int(recs[i].get('hits',0)),str(recs[i].get('last_seen',''))))
            drop=set(order[:len(recs)-cap])
            removed+=[str(recs[i]['id']) for i in sorted(drop)]
            recs=[r for i,r in enumerate(recs) if i not in drop]
        if removed:d['records']=recs;self._write(d)
        return removed
    def consolidate(self,groups):
        """Apply upstream-proposed merges: primary id keeps identity, text/kind replaced,
        tags/paths unioned when not supplied, hits summed, dates spanned. Singleton and
        unknown-id groups are ignored. Returns (merged_ids, removed_ids)."""
        d=self.load();recs=d['records'];by={str(r.get('id')):r for r in recs};merged=[];removed=[]
        for g in groups or []:
            members=[by[str(i)] for i in (g.get('ids') or []) if str(i) in by]
            if len(members)<2 or not str(g.get('text') or '').strip():continue
            prim=members[0]
            prim['text']=str(g.get('text') or prim.get('text',''))
            prim['kind']=str(g.get('kind') or prim.get('kind','fact'))
            prim['tags']=list(dict.fromkeys([str(t) for t in (g.get('tags') if g.get('tags') is not None else sum((m.get('tags',[]) for m in members),[]))]))
            prim['paths']=list(dict.fromkeys([str(p) for p in (g.get('paths') if g.get('paths') is not None else sum((m.get('paths',[]) for m in members),[]))]))
            prim['hits']=sum(int(m.get('hits',0)) for m in members)
            prim['created']=min(str(m.get('created','')) for m in members)
            prim['last_seen']=max(str(m.get('last_seen','')) for m in members)
            for m in members[1:]:
                if m in recs:recs.remove(m);removed.append(str(m['id']))
            merged.append(str(prim['id']))
        if merged or removed:self._write(d)
        return merged,removed
    def text(self):
        lines=[render_record(r)+f" (hits:{r.get('hits',0)}, seen:{str(r.get('last_seen',''))[:10]})" for r in self.records()[:200]]
        return ('\n'.join(lines)[:12000]) or '(project memory is empty)'
