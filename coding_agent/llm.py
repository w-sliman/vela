from __future__ import annotations
import json,re,time
from dataclasses import dataclass
from .providers import OpenAICompatibleProvider
from .prompts import SYSTEM_PROMPT
from .context import ContextManager
from .telemetry import Metrics,Timer,extract_usage,USAGE_ADVICE
from .tools import dispatch,tool_schemas
from .json_repair import parse_tool_arguments
from .events import EventBus
from .memory import ProjectMemory,select_records,render_record
from .conversation import AssistantMsg,ToolResult,UserMsg,INTERRUPTED,answered_ids,is_call,item_text,tool_arguments
from . import transports

COMPACT_SYSTEM=('You compress coding-agent conversation transcripts. Respond with ONLY a JSON object: '
 '{"summary":"...","keep_last_turns":<int 1-5>,"memories":[{"kind":"...","text":"..."}]}. The summary must preserve: current task state, files '
 'touched, key decisions, and open items. keep_last_turns selects how many of the most recent '
 'user-turns are already covered by your summary and should additionally be kept verbatim. '
 '"memories" lists durable project knowledge from the dropped turns worth keeping after this '
 'conversation ends (facts, decisions, preferences; optional "tags" and "paths" arrays sharpen '
 'later retrieval). Keep each text under ~200 chars; use an empty list when nothing qualifies.')
DEFAULT_KEEP_TURNS=2
KEEP_MAX=5

class PauseInterrupt(Exception):
    """Raised when the user interrupted a run; context stays intact for /continue."""
_EDIT_TOOLS=frozenset({'write_file','replace_text','apply_patch'})
_VERIFY_HINTS=('pytest','test','tests','mypy','ruff','flake8','unittest','tox','compileall')
_QUOTED_ARG_RE=re.compile(r'"[^"]*"|\'[^\']*\'')
def _is_check_command(command):
    """True when the command looks like a test/check invocation.

    Quoted arguments are ignored first, so e.g. `git commit -m "test"` does
    not count as a check.
    """
    s=_QUOTED_ARG_RE.sub(' ',str(command))
    return any(re.search(r'\b'+h+r'\b',s) for h in _VERIFY_HINTS)
TRANSPORT_RETRY_MSG=('The previous model request failed at the tool-call transport layer. '
 'Do not emit large inline file contents. Re-read the target file and use small '
 'apply_patch/replace_text operations with valid JSON.')
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
        if isinstance(h,UserMsg):
            cur=[h];turns.append(cur)
        elif cur is None:
            cur=[h];turns.append(cur)
        else:cur.append(h)
    return turns

@dataclass(frozen=True)
class AgentResult:
    text:str; tool_calls:int; metrics:dict; streamed:bool=False

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
    """Workspace paths touched by recent tool calls, harvested from tool arguments."""
    out=[]
    for blob in tool_arguments(history[-limit:]):
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
        self.config=config;self.context=context;self.session=session;self.events=events or EventBus()
        self.history=[];self.metrics=Metrics()
        self.ctx=ContextManager(config.max_context_chars,config.max_history_items)
        self._auto_compact_attempted=False;self.memory=MemoryInjector(config,session)
        self._turn_memory=None;self._gate_nudged=False;self._edited_since_check=False
        self.provider=OpenAICompatibleProvider(config.api_key,config.base_url)
    @property
    def provider(self):return self._provider
    @provider.setter
    def provider(self,value):
        """Swapping the API client rebuilds the transports bound to it, so the
        preferred transport is restored along with the client."""
        self._provider=value
        self._transports=transports.build(self.config.api_mode,value,self.config.model,SYSTEM_PROMPT,
                                          stream=getattr(self.config,'stream_chat',True))
        self.transport=self._transports[0]
    @property
    def mode(self):
        """Name of the transport actually in use — what /model should report."""
        return self.transport.name
    def _repair_partial_turn(self):
        """Close dangling tool-call pairs after an interrupt, so the next request
        stays valid. Only the trailing block is examined. Returns outputs added."""
        h=self.history
        idx=next((i for i in range(len(h)-1,-1,-1) if is_call(h[i])),None)
        if idx is None:return 0
        answered=answered_ids(h[idx+1:]);added=0
        for call in h[idx].tool_calls:
            if call.id and call.id not in answered:
                h.append(ToolResult(call_id=call.id,output=INTERRUPTED,name=call.name));added+=1
        return added
    def resume(self,instruction=None):
        """Continue a paused run: re-enter the loop with a synthetic nudge."""
        if not self.history:raise RuntimeError('nothing to continue — context is empty')
        return self.run(instruction or '[paused] Continue where you left off; finish your remaining todos.')
    def clear(self):
        """Reset the conversation. A transport downgrade is scoped to a conversation,
        not to the process, so clearing restores the configured preference."""
        self.history=[];self._turn_memory=None;self.transport=self._transports[0]
    def start_resumed(self,digest_text,source_id):
        """Replace context with a resumed-session digest as the opening context message."""
        self.clear();self.history=[UserMsg(text=digest_text)]
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
        new_history=[UserMsg(text=f'{header}\n{summary}')]
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
        focus_line=f'Focus: {focus}\n' if focus else ''
        prompt=(f'{focus_line}Current memory records:\n{listing}\n\n'
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
            for h in t:lines.append(item_text(h))
        blob='\n'.join(lines)
        if len(blob)>max_chars:blob=blob[-max_chars:]
        focus_line=f'Focus for this compaction: {focus}\n' if focus else ''
        return (f'{focus_line}Transcript (oldest first):\n{blob}\n\n'
                'Return ONLY JSON: {"summary": "...", "keep_last_turns": <int 1-5>, "memories": [{"kind": "...", "text": "..."}]}')
    def _trim(self): self.history=self.ctx.trim(self.history)
    def _advisory_blocks(self):
        """Blocks attached to the outgoing payload but never persisted into history:
        this turn's memory recall and the current todo queue."""
        return [b for b in (self._turn_memory,self._todos_block()) if b]
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
        try:payload=json.loads(result) if isinstance(result,str) else None
        except Exception:payload=None
        if not isinstance(payload,dict):payload={}
        if name in _EDIT_TOOLS and payload.get('status')=='completed':self._edited_since_check=True
        elif name=='run_tests' or (name=='run_command' and _is_check_command(args.get('command',''))):
            if payload.get('returncode')==0:self._edited_since_check=False
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
        # A summarizer failure must not take the user's request down with it:
        # degrade to "not compacted" and let normal trimming bound the context.
        try:res=self.compact()
        except Exception as exc:
            self.events.emit('info',f'auto-compact failed, continuing untrimmed: {type(exc).__name__}')
            self.session.record('error',{'stage':'auto_compact','message':str(exc)});return
        if res.get('compacted'):
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
        self.session.record('user',{'text':user_text});self.history.append(UserMsg(text=user_text))
        self._turn_memory=self.memory.build(user_text,self.history[:-1])
        self._gate_nudged=False;self._edited_since_check=False
        self._auto_compact_attempted=False;self.streamed_any=False
        try:
            for _ in range(self.config.max_turns):
                self._maybe_auto_compact()
                result=self._step()
                if result is not None:
                    if self._verify_gate_should_nudge():continue
                    self.metrics.price(self.config.price_input_per_million,self.config.price_output_per_million)
                    self.events.emit('done','model response')
                    return AgentResult(result,self.metrics.tool_calls,self.metrics.as_dict(),
                                       streamed=getattr(self,'streamed_any',False))
                self._trim()
            raise RuntimeError(f'agent exceeded {self.config.max_turns} controller turns')
        except KeyboardInterrupt:
            # Cooperative pause: close any dangling tool-call pair so history stays
            # valid, journal it, and surface a typed exception the REPL catches.
            added=self._repair_partial_turn()
            self.session.record('paused',{'repaired_outputs':added})
            self.events.emit('info','paused by user — context kept; /continue resumes')
            raise PauseInterrupt from None
    def _verify_gate_should_nudge(self):
        """Append one corrective nudge when finishing with open todos or unverified
        edits. At most once per request; returns True when the loop should continue."""
        if not getattr(self.config,'verify_gate',False) or self._gate_nudged:return False
        open_items=[t for t in self.todos if str(t.get('status'))!='done']
        if not (open_items or self._edited_since_check):return False
        self._gate_nudged=True
        self.events.emit('info','verify gate: nudging before finish (open todos / unverified edits)')
        self.session.record('verify_gate',{'open_todos':len(open_items),
                                           'edited_since_check':self._edited_since_check})
        self.history.append(UserMsg(text=VERIFY_GATE_MSG))
        return True
    def _next_transport(self):
        """The next transport to try after the current one failed, or None."""
        try:i=self._transports.index(self.transport)
        except ValueError:return None
        return self._transports[i+1] if i+1<len(self._transports) else None
    def _step(self):
        """One controller turn: send the conversation, record the reply, dispatch any
        tools. Returns the final assistant text, or None when tools ran.

        A transport failure falls through to the next transport and re-encodes the
        SAME history — canonical items belong to no wire format, so a downgrade
        costs a retry rather than the conversation.
        """
        try:reply=self._send()
        except Exception as exc:
            self.events.emit('error','model request failed',error=str(exc))
            self.session.record('error',{'stage':'model_request','transport':self.transport.name,
                                         'message':str(exc)})
            nxt=self._next_transport()
            if nxt is None:raise
            self.session.record('transport_fallback',{'from':self.transport.name,'to':nxt.name,
                                                      'reason':str(exc)[:300]})
            self.events.emit('info',f'transport: {self.transport.name} → {nxt.name} (previous request failed)')
            self.transport=nxt
            # The model may also have produced the malformed call that was rejected.
            self.history.append(UserMsg(text=TRANSPORT_RETRY_MSG))
            return None
        return self._consume(reply)
    def _send(self):
        with Timer() as timer:
            reply=self._with_retries(lambda:self.transport.send(
                self.history,self._advisory_blocks(),tool_schemas(),
                on_token=lambda t:self.events.emit('token','stream',text=t)))
        self.metrics.add(reply.raw_usage,timer.elapsed_ms)
        self.session.record('usage',reply.usage if reply.usage is not None
                            else {'available':False,'advice':USAGE_ADVICE})
        self._emit_usage(reply.usage)
        return reply
    def _consume(self,reply):
        """Append the reply to history and run any tools it requested."""
        if reply.streamed:
            self.streamed_any=True;self.events.emit('token','stream',end=True)
        if not reply.tool_calls:
            text=reply.text or '(no textual response)'
            self.history.append(AssistantMsg(text=text))
            self.session.record('assistant',{'text':text,'metrics':self.metrics.as_dict()})
            return text
        self.history.append(AssistantMsg(text=reply.text,tool_calls=reply.tool_calls))
        for call in reply.tool_calls:
            self.metrics.tool_calls+=1
            args,err,repaired=parse_tool_arguments(call.arguments)
            self.events.emit('info',f'tool arguments: {call.name}',repaired=repaired)
            result=(json.dumps({'status':'tool_argument_error','error':err,
                                'recovery':'Re-read the file and retry with a smaller patch; do not repeat stale text.'})
                    if err else self._dispatch(call.name,args))
            self.session.record('tool_call',{'name':call.name,'arguments_raw':call.arguments})
            self.session.record('tool_result',{'name':call.name,'result':result})
            self.history.append(ToolResult(call_id=call.id,output=result,name=call.name))
        return None
