from __future__ import annotations
import json,re,time
from dataclasses import dataclass
from types import SimpleNamespace
from .providers import OpenAICompatibleProvider
from .prompts import SYSTEM_PROMPT
from .context import ContextManager
from .telemetry import Metrics,Timer,extract_usage,USAGE_ADVICE
from .tools import dispatch,tool_schemas
from .json_repair import parse_tool_arguments
from .events import EventBus
from .memory import ProjectMemory,select_records,render_record

COMPACT_SYSTEM=('You compress coding-agent conversation transcripts. Respond with ONLY a JSON object: '
 '{"summary":"...","keep_last_turns":<int 1-5>,"memories":[{"kind":"...","text":"..."}]}. The summary must preserve: current task state, files '
 'touched, key decisions, and open items. keep_last_turns selects how many of the most recent '
 'user-turns are already covered by your summary and should additionally be kept verbatim. '
 '"memories" lists durable project knowledge from the dropped turns worth keeping after this '
 'conversation ends (facts, decisions, preferences; optional "tags" and "paths" arrays sharpen '
 'later retrieval). Keep each text under ~200 chars; use an empty list when nothing qualifies.')
DEFAULT_KEEP_TURNS=2
KEEP_MAX=5
_EDIT_TOOLS=frozenset({'write_file','replace_text','apply_patch'})
_VERIFY_HINTS=('pytest',' test','tests','mypy','ruff','flake8','unittest','tox','compileall')
VERIFY_GATE_MSG=('[verify gate] Before finishing: you have open todos and/or edits not followed by a '
 'passing check. Run the relevant tests/checks now, or update your todo list to reflect reality. '
 'Do not claim success while work is pending.')
CONSOLIDATE_SYSTEM=("You curate a coding agent's persistent project memory. Respond with ONLY JSON: "
 '{"groups":[{"ids":["r1","r5"],"kind":"decision","text":"canonical merged text","tags":["..."],"paths":["..."]}]}. '
 'Group records that state the same underlying fact (duplicates or paraphrases; 2+ ids per group) and '
 'rewrite them into one canonical wording, merging their tags/paths. Never invent facts, never drop '
 'unique information, never group unrelated records. Omit groups when nothing overlaps.')

def _turns(history):
    """Group history into user-turns: one user message plus everything until the next."""
    turns=[];cur=None
    for h in history:
        if isinstance(h,dict) and h.get('role')=='user':
            cur=[h];turns.append(cur)
        elif cur is None:
            cur=[h];turns.append(cur)
        else:cur.append(h)
    return turns

def _item_text(h):
    if isinstance(h,dict):
        role=h.get('role') or h.get('type') or '?'
        c=h.get('content')
        if h.get('tool_calls'):
            names=' '.join((tc.get('function') or {}).get('name','') for tc in h['tool_calls'])
            c=f'{c or ""} [tools: {names}]'
        if h.get('type')=='function_call_output':c=f"call {h.get('call_id')}: {str(h.get('output',''))[:200]}"
    else:
        role=getattr(h,'type',None) or getattr(h,'role','?')
        c=getattr(h,'content',None) or getattr(h,'output_text','') or ''
        if role=='function_call_output':c=f"call {getattr(h,'call_id','')}: {str(getattr(h,'output',''))[:200]}"
    s=str(c).replace('\n',' ')
    return f'{role}: {s[:400]}'
@dataclass(frozen=True)
class AgentResult:
    text:str; tool_calls:int; metrics:dict; streamed:bool=False

def safe_json(raw):
    try:
        v=json.loads(raw or '{}')
        return (v,None) if isinstance(v,dict) else (None,'tool arguments must be a JSON object')
    except json.JSONDecodeError as e:
        return None,f'malformed tool JSON: {e}'

_RE_PATH_ARG=re.compile(r'"path"\s*:\s*"([^"\n]+)"');_RE_PATHS_ARR=re.compile(r'"paths"\s*:\s*\[([^\]]*)\]')
def _clean_memories(raw):
    """Validate summarizer-proposed memories -> [(kind,text,tags,paths)]; junk entries are dropped."""
    out=[]
    if not isinstance(raw,list):return out
    for m in raw:
        if not isinstance(m,dict):continue
        kind=str(m.get('kind') or 'fact').strip()[:40] or 'fact';text=str(m.get('text') or '').strip()
        if not text or len(text)>500:continue
        tags=[str(t)[:60] for t in (m.get('tags') or [])][:8];paths=[str(p)[:200] for p in (m.get('paths') or [])][:8]
        out.append((kind,text,tags,paths))
    return out

def _clean_groups(raw,valid_ids):
    """Validate consolidator-proposed merge groups; only known-id groups of 2+ survive."""
    out=[]
    if not isinstance(raw,list):return out
    for g in raw:
        if not isinstance(g,dict):continue
        seen=list(dict.fromkeys(str(i) for i in (g.get('ids') or [])))
        ids=[i for i in seen if i in valid_ids];text=str(g.get('text') or '').strip()
        if len(ids)>=2 and text and len(text)<=500:
            out.append({'ids':ids,'kind':str(g.get('kind') or 'fact').strip()[:40] or 'fact','text':text,
                        'tags':[str(t)[:60] for t in (g.get('tags') or [])][:8],
                        'paths':[str(p)[:200] for p in (g.get('paths') or [])][:8]})
    return out
def _recent_paths(history,limit=16):
    """Workspace paths touched by recent tool calls, harvested from recorded tool arguments."""
    out=[]
    for h in history[-limit:]:
        blob=''
        if isinstance(h,dict):
            for tc in h.get('tool_calls') or []:blob+=(tc.get('function') or {}).get('arguments') or ''
            blob+='\n'+str(h.get('arguments') or '')
        elif getattr(h,'type',None)=='function_call':blob=str(getattr(h,'arguments','') or '')
        out.extend(m.group(1) for m in _RE_PATH_ARG.finditer(blob))
        for m in _RE_PATHS_ARR.finditer(blob):out+=re.findall(r'"([^"\n]+)"',m.group(1))
    return list(dict.fromkeys(out))

class MemoryInjector:
    """Lexical per-turn recall: picks relevant project memories once per user request.

    The block is attached to outgoing model payloads (never persisted into history),
    so trimming/pair integrity are untouched; memory failures never break a request.
    """
    HEADER=('[project memory] Advisory recall from prior sessions — may be stale; '
            'current workspace files always win. Context only, do not respond to this.')
    def __init__(self,cfg,session):
        self.cfg=cfg;self.session=session;self.last_ids=[]
    def build(self,user_text,history):
        self.last_ids=[]
        if not getattr(self.cfg,'memory_inject',False):return None
        try:
            recs=ProjectMemory(self.cfg.workspace).records()
            if not recs:return None
            sel=select_records(recs,user_text,_recent_paths(history),top_k=self.cfg.memory_top_k,
                               max_chars=self.cfg.memory_max_chars,min_score=self.cfg.memory_min_score)
            if not sel:return None
            ids=[r.get('id') for _,r in sel];self.last_ids=ids
            ProjectMemory(self.cfg.workspace).touch(ids)
            body='\n'.join(f'- {render_record(r)}' for _,r in sel)
            self.session.record('memory_injected',{'ids':ids,'chars':len(body)})
            return f'{self.HEADER}\n{body}'
        except Exception as exc:
            self.session.record('memory_injected',{'error':str(exc)});return None
class CodingAgent:
    def __init__(self,config,context,session,events=None):
        if not config.api_key: raise RuntimeError('OPENAI_API_KEY is not configured. Add it to .env.')
        if not config.model: raise RuntimeError('OPENAI_MODEL is not configured. Add it to .env.')
        self.provider=OpenAICompatibleProvider(config.api_key,config.base_url)
        self.config=config;self.context=context;self.session=session;self.events=events or EventBus();self.history=[];self.mode=config.api_mode;self.metrics=Metrics();self.ctx=ContextManager(config.max_context_chars,config.max_history_items);self._auto_compact_attempted=False;self.memory=MemoryInjector(config,session);self._turn_memory=None;self._gate_nudged=False;self._edited_since_check=False
    def clear(self): self.history=[];self._turn_memory=None
    def start_resumed(self,digest_text,source_id):
        """Replace context with a resumed-session digest as the opening context message."""
        self.clear();self.history=[{'role':'user','content':digest_text}]
        self.session.record('resumed_from',{'session':source_id,'chars':len(digest_text)})
    def compact(self,focus=None):
        """Summarize older turns into one context message; recent turns kept verbatim.

        The summarizer model chooses how many recent user-turns to keep (clamped
        1..5, default on garbage). History is only replaced after a successful
        summarizer call; on transport failure the exception propagates and the
        conversation is left untouched.
        """
        turns=_turns(self.history)
        if len(turns)<=2:
            return {'compacted':False,'reason':f'only {len(turns)} turn(s) in history; nothing to compact'}
        transcript=self._summarizer_input(turns,focus)
        with Timer() as timer:r=self._with_retries(lambda:self.provider.chat(model=self.config.model,messages=[{'role':'system','content':COMPACT_SYSTEM},{'role':'user','content':transcript}]))
        u=extract_usage(getattr(r,'usage',None));self.metrics.add(getattr(r,'usage',None),timer.elapsed_ms)
        self.session.record('usage',u if u is not None else {'available':False,'advice':USAGE_ADVICE})
        raw=(r.choices[0].message.content or '').strip()
        value,err,repaired=parse_tool_arguments(raw)
        if err or not isinstance(value,dict) or 'summary' not in value:
            summary=raw;keep=DEFAULT_KEEP_TURNS  # non-JSON prose still works as a summary
        else:
            summary=str(value.get('summary',''))
            try:keep=int(value.get('keep_last_turns',DEFAULT_KEEP_TURNS))
            except (TypeError,ValueError):keep=DEFAULT_KEEP_TURNS
        keep=max(1,min(KEEP_MAX,keep));keep=min(keep,len(turns)-1)
        header='[Conversation summary]'+(f' — focus: {focus}' if focus else '')
        new_history=[{'role':'user','content':f'{header}\n{summary}'}]
        for t in turns[-keep:]:new_history.extend(t)
        before=len(self.history);self.history=new_history
        saved=self._persist_distilled(_clean_memories(value.get('memories') if isinstance(value,dict) else None))
        info={'compacted':True,'turns_removed':len(turns)-keep,'memories_saved':len(saved),
              'turns_kept':keep,'items_before':before,'items_after':len(self.history),'summary':summary}
        self.session.record('compact',{**{k:v for k,v in info.items() if k not in {'summary'}},'focus':focus,'summary_chars':len(summary)})
        return info
    def _persist_distilled(self,candidates):
        """Persist summarizer-proposed durable memories; failures never break compaction."""
        if not candidates or not getattr(self.config,'memory_distill',True):return []
        try:
            pm=ProjectMemory(self.config.workspace)
            saved=[pm.add(kind,text,tags,paths) for kind,text,tags,paths in candidates]
            pm.prune(getattr(self.config,'memory_max_records',None),getattr(self.config,'memory_ttl_days',0))
        except Exception as exc:
            self.session.record('memory_distilled',{'error':str(exc)});return []
        if saved:
            self.session.record('memory_distilled',{'ids':saved})
            self.events.emit('info',f'compact: persisted {len(saved)} project memory item(s)')
        return saved
    def consolidate_memory(self,focus=None):
        """LLM groups paraphrased/duplicate memories; Python merges them deterministically."""
        pm=ProjectMemory(self.config.workspace);recs=pm.records()
        if len(recs)<2:return {'merged':0,'removed':0,'pruned':0,'before':len(recs),'after':len(recs),'reason':'fewer than 2 records'}
        listing='\n'.join(f"{r['id']} [{r.get('kind','fact')}] tags={r.get('tags',[])} paths={r.get('paths',[])} hits={r.get('hits',0)} :: {r.get('text','')}" for r in recs)[:20000]
        prompt=(f'{"Focus: "+focus+"\n" if focus else ""}Current memory records:\n{listing}\n\n'
                'Return ONLY the JSON groups object; use an empty list when nothing overlaps.')
        with Timer() as timer:r=self._with_retries(lambda:self.provider.chat(model=self.config.model,messages=[{'role':'system','content':CONSOLIDATE_SYSTEM},{'role':'user','content':prompt}]))
        u=extract_usage(getattr(r,'usage',None));self.metrics.add(getattr(r,'usage',None),timer.elapsed_ms)
        self.session.record('usage',u if u is not None else {'available':False,'advice':USAGE_ADVICE})
        raw=(r.choices[0].message.content or '').strip()
        value,err,_=parse_tool_arguments(raw)
        groups=_clean_groups(value.get('groups') if isinstance(value,dict) else None,{str(x['id']) for x in recs})
        before=len(recs)
        merged,removed=pm.consolidate(groups)
        pruned=pm.prune(getattr(self.config,'memory_max_records',None),getattr(self.config,'memory_ttl_days',0))
        info={'merged':len(merged),'removed':len(removed),'pruned':len(pruned),'before':before,
              'after':before-len(removed)-len(pruned),'groups':len(groups)}
        self.session.record('memory_consolidated',{'focus':focus,**info})
        if merged or removed:
            self.events.emit('info',f"memory: merged {len(merged)} group(s), removed {len(removed)} record(s)")
        return info
    @property
    def last_memory_ids(self):return list(getattr(self.memory,'last_ids',[]))
    def _summarizer_input(self,turns,focus,max_chars=24000):
        lines=[]
        for ti,t in enumerate(turns,1):
            lines.append(f'--- turn {ti} ---')
            for h in t:lines.append(_item_text(h))
        blob='\n'.join(lines)
        if len(blob)>max_chars:blob=blob[-max_chars:]
        focus_line=f'Focus for this compaction: {focus}\n' if focus else ''
        return (f'{focus_line}Transcript (oldest first):\n{blob}\n\n'
                'Return ONLY JSON: {"summary": "...", "keep_last_turns": <int 1-5>, "memories": [{"kind": "...", "text": "..."}]}')
    def _trim(self): self.history=self.ctx.trim(self.history)
    def _with_context_blocks(self,items):
        """Advisory blocks appended to outgoing payloads (never persisted to history):
        this turn's memory recall and the current todo queue."""
        extra=[]
        if self._turn_memory:extra.append({'role':'user','content':self._turn_memory})
        tb=self._todos_block()
        if tb:extra.append({'role':'user','content':tb})
        return items+extra
    _TODO_ICON={'done':'x','in_progress':'>','pending':' '}
    def _todos_block(self):
        if not getattr(self.config,'show_todos',True):return None
        todos=self.todos
        if not todos:return None
        lines=[f"- [{self._TODO_ICON.get(str(t.get('status')),' ')}] {t.get('text')}" for t in todos]
        return ('[current todos] Your working queue as last reported. Keep it updated via '
                'write_todos; finish or drop every item before reporting overall success.\n'+'\n'.join(lines))
    @property
    def todos(self):
        ctx=getattr(self,'context',None)
        return list(getattr(ctx,'todos',None) or []) if ctx else []
    def _dispatch(self,name,args):
        result=dispatch(self.context,name,args)
        if name in _EDIT_TOOLS and '"status": "completed"' in result:self._edited_since_check=True
        elif name=='run_tests' or (name=='run_command' and any(h in str(args.get('command','')) for h in _VERIFY_HINTS)):
            if '"returncode": 0' in result:self._edited_since_check=False
        if name=='write_todos':
            try:
                payload=json.loads(result)
                self.session.record('todos_updated',{'todos':payload.get('todos'),'diff':payload.get('diff')})
                self.events.emit('todos','todo list updated',items=payload.get('todos'),diff=payload.get('diff'))
            except Exception:pass
        return result
    def _with_retries(self,fn,delays=None):
        """Call fn with exponential backoff; re-raises the last error after all attempts."""
        if delays is None:
            delays=[0.0]+[0.5*(2**i) for i in range(max(0,self.config.request_retries))]
        last=None
        for i,d in enumerate(delays):
            if d:
                self.events.emit('info',f'retrying in {d:.1f}s (attempt {i+1} failed)')
                time.sleep(d)
            try:return fn()
            except Exception as exc:
                last=exc
                self.events.emit('info',f'request attempt {i+1} failed: {type(exc).__name__}: {str(exc)[:120]}')
        raise last
    def _maybe_auto_compact(self):
        """Compact automatically once per user request when context crosses the threshold."""
        cfg=self.config
        if not cfg.auto_compact or self._auto_compact_attempted:return
        win=cfg.context_window_tokens;last=self.metrics.last_input_tokens
        if not win or not last or last/win*100<cfg.auto_compact_pct:return
        self._auto_compact_attempted=True
        res=self.compact()
        if res.get('compacted'):
            now=f"{self.metrics.last_input_tokens/win*100:.0f}%" if win else '?'
            self.events.emit('info',f"auto-compact: {res['turns_removed']} turn(s) summarized, kept last {res['turns_kept']}")
    def _emit_usage(self,u):
        """Publish per-turn token/context usage for live display."""
        if u is not None:
            self.events.emit('usage','model usage',available=True,input=u['input'],output=u['output'],
                             total=u['total'],last_input=u['input'],window=self.config.context_window_tokens)
        else:
            self.events.emit('usage','model usage',available=False,advice=USAGE_ADVICE)
    def run(self,user_text):
        self.events.emit('start','model request')
        self.session.record('user',{'text':user_text});self.history.append({'role':'user','content':user_text})
        self._turn_memory=self.memory.build(user_text,self.history[:-1])
        self._gate_nudged=False;self._edited_since_check=False
        calls=0
        self._auto_compact_attempted=False;self.streamed_any=False
        for _ in range(self.config.max_turns):
            self._maybe_auto_compact()
            try: result=self._responses() if self.mode in {'auto','responses'} else self._chat()
            except Exception as exc:
                self.events.emit('error','model request failed',error=str(exc))
                self.session.record('error',{'stage':'model_request','mode':self.mode,'message':str(exc)})
                # Local OpenAI-compatible servers sometimes reject malformed
                # function-call JSON before returning an API response. Retry once
                # with a corrective instruction and a compact history rather than
                # immediately crashing or blindly replaying the same request.
                if self.mode=='auto':
                    self.mode='chat'
                    self.history=[{'role':'user','content':user_text},{'role':'user','content':'The previous model request failed at the tool-call transport layer. Do not emit large inline file contents. Re-read the target file and use small apply_patch/replace_text operations with valid JSON.'}]
                    continue
                raise
            if result is not None:
                open_items=[t for t in self.todos if str(t.get('status'))!='done']
                if getattr(self.config,'verify_gate',False) and not self._gate_nudged and (open_items or self._edited_since_check):
                    self._gate_nudged=True
                    self.events.emit('info','verify gate: nudging before finish (open todos / unverified edits)')
                    self.session.record('verify_gate',{'open_todos':len(open_items),'edited_since_check':self._edited_since_check})
                    self.history.append({'role':'user','content':VERIFY_GATE_MSG})
                    continue
                self.metrics.price(self.config.price_input_per_million,self.config.price_output_per_million)
                self.events.emit('done','model response')
                return AgentResult(result,self.metrics.tool_calls,self.metrics.as_dict(),streamed=getattr(self,'streamed_any',False))
            calls=self.metrics.tool_calls;self._trim()
        raise RuntimeError(f'agent exceeded {self.config.max_turns} controller turns')
    def _responses(self):
        with Timer() as timer:r=self._with_retries(lambda:self.provider.responses(model=self.config.model,instructions=SYSTEM_PROMPT,input=self._with_context_blocks(self.history),tools=tool_schemas()))
        u=extract_usage(getattr(r,'usage',None));self.metrics.add(getattr(r,'usage',None),timer.elapsed_ms)
        self.session.record('usage',u if u is not None else {'available':False,'advice':USAGE_ADVICE});items=list(r.output);self.history.extend(items)
        self._emit_usage(u)
        calls=[x for x in items if getattr(x,'type',None)=='function_call']
        if not calls:
            text=r.output_text or '(no textual response)';self.session.record('assistant',{'text':text,'metrics':self.metrics.as_dict()});return text
        for c in calls:
            self.metrics.tool_calls+=1;args,err,repaired=parse_tool_arguments(c.arguments)
            self.events.emit('info',f'tool arguments: {c.name}',repaired=repaired)
            result=json.dumps({'status':'tool_argument_error','error':err,'recovery':'Re-read the file and retry with a smaller patch; do not repeat stale text.'}) if err else self._dispatch(c.name,args)
            self.session.record('tool_call',{'name':c.name,'arguments_raw':c.arguments});self.session.record('tool_result',{'name':c.name,'result':result})
            self.history.append({'type':'function_call_output','call_id':c.call_id,'output':result})
        return None
    def _chat(self):
        tools=[{'type':'function','function':{'name':x['name'],'description':x['description'],'parameters':x['parameters']}} for x in tool_schemas()]
        if self.config.stream_chat:return self._chat_streamed(tools)
        with Timer() as timer:r=self._with_retries(lambda:self.provider.chat(model=self.config.model,messages=[{'role':'system','content':SYSTEM_PROMPT}]+self._with_context_blocks(self.history),tools=tools,tool_choice='auto'))
        u=extract_usage(getattr(r,'usage',None));self.metrics.add(getattr(r,'usage',None),timer.elapsed_ms)
        self.session.record('usage',u if u is not None else {'available':False,'advice':USAGE_ADVICE});m=r.choices[0].message
        self._emit_usage(u)
        return self._handle_chat_message(m)
    def _chat_streamed(self,tools):
        """Streaming chat transport: text tokens emit live; tool calls accumulate."""
        with Timer() as timer:stream=self._with_retries(lambda:self.provider.chat_stream(model=self.config.model,messages=[{'role':'system','content':SYSTEM_PROMPT}]+self._with_context_blocks(self.history),tools=tools,tool_choice='auto'))
        parts=[];tcalls={};u=None;emitted=False
        for chunk in stream:
            cu=getattr(chunk,'usage',None)
            if cu is not None:u=cu
            if not getattr(chunk,'choices',None):continue
            d=chunk.choices[0].delta
            piece=getattr(d,'content',None) if d else None
            if piece:
                parts.append(piece);emitted=True
                self.events.emit('token','stream',text=piece)
            for tc in ((getattr(d,'tool_calls',None) or []) if d else []):
                slot=tcalls.setdefault(tc.index,{'id':'','name':'','args':''})
                if getattr(tc,'id',None):slot['id']=tc.id
                fn=getattr(tc,'function',None)
                if fn is not None:
                    if getattr(fn,'name',None):slot['name']=fn.name
                    if getattr(fn,'arguments',None):slot['args']+=fn.arguments
        u_norm=extract_usage(u);self.metrics.add(u,timer.elapsed_ms)
        self.session.record('usage',u_norm if u_norm is not None else {'available':False,'advice':USAGE_ADVICE})
        self.streamed_any=emitted
        if emitted:self.events.emit('token','stream',end=True)
        self._emit_usage(u_norm)
        if tcalls:
            calls=[SimpleNamespace(id=s['id'],function=SimpleNamespace(name=s['name'],arguments=s['args'])) for _,s in sorted(tcalls.items())]
            m=SimpleNamespace(content=''.join(parts) or None,tool_calls=calls)
        else:
            m=SimpleNamespace(content=''.join(parts) or None,tool_calls=None)
        return self._handle_chat_message(m)
    def _handle_chat_message(self,m):
        if not m.tool_calls:
            text=m.content or '(no textual response)';self.history.append({'role':'assistant','content':text});self.session.record('assistant',{'text':text,'metrics':self.metrics.as_dict()});return text
        calls=[{'id':c.id,'type':'function','function':{'name':c.function.name,'arguments':c.function.arguments}} for c in m.tool_calls]
        self.history.append({'role':'assistant','content':m.content or '','tool_calls':calls})
        for c in m.tool_calls:
            self.metrics.tool_calls+=1;args,err,repaired=parse_tool_arguments(c.function.arguments)
            self.events.emit('info',f'tool arguments: {c.function.name}',repaired=repaired)
            result=json.dumps({'status':'tool_argument_error','error':err,'recovery':'Re-read the file and retry with a smaller patch; do not repeat stale text.'}) if err else self._dispatch(c.function.name,args)
            self.session.record('tool_call',{'name':c.function.name,'arguments_raw':c.function.arguments});self.session.record('tool_result',{'name':c.function.name,'result':result})
            self.history.append({'role':'tool','tool_call_id':c.id,'content':result})
        return None
