"""Transports: the only place that knows a provider's wire format.

Each transport encodes canonical `conversation` items into a request payload and
decodes the reply back into canonical items. The agent above never sees a
`tool_call_id` or a `function_call`, so switching transports mid-conversation is
a re-encode of the same history rather than a reset.

`Reply` is what every transport returns: assistant text, canonical tool calls,
normalized usage, and whether text was streamed live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from .conversation import AssistantMsg, ToolCall, ToolResult, UserMsg
from .telemetry import extract_usage


@dataclass
class Reply:
    text:str=''
    tool_calls:list[ToolCall]=field(default_factory=list)
    usage:dict|None=None
    streamed:bool=False
    raw_usage:object=None      # kept so Metrics.add can count usage-less responses


def _advisory(blocks):
    return [UserMsg(text=b) for b in blocks if b]


class ChatTransport:
    """OpenAI Chat Completions. Tool calls travel as `tool_calls` on an assistant
    message, answered by `role='tool'` messages keyed on `tool_call_id`."""
    name='chat'
    streams=False

    def __init__(self,provider,model,system_prompt):
        self.provider=provider;self.model=model;self.system=system_prompt

    def _tools(self,schemas):
        return [{'type':'function','function':{'name':s['name'],'description':s['description'],
                 'parameters':s['parameters']}} for s in schemas]

    def encode(self,history,advisory=()):
        out=[{'role':'system','content':self.system}]
        for item in list(history)+_advisory(advisory):
            if isinstance(item,UserMsg):out.append({'role':'user','content':item.text})
            elif isinstance(item,AssistantMsg):
                msg:dict[str,Any]={'role':'assistant','content':item.text or ''}
                if item.tool_calls:
                    msg['tool_calls']=[{'id':c.id,'type':'function',
                                        'function':{'name':c.name,'arguments':c.arguments}}
                                       for c in item.tool_calls]
                out.append(msg)
            elif isinstance(item,ToolResult):
                out.append({'role':'tool','tool_call_id':item.call_id,'content':item.output})
        return out

    def send(self,history,advisory,schemas,on_token=None):
        return self.send_payload(self.encode(history,advisory),schemas,on_token)

    def send_payload(self,payload,schemas,on_token=None):
        r=self.provider.chat(model=self.model,messages=payload,
                             tools=self._tools(schemas),tool_choice='auto')
        m=r.choices[0].message
        return Reply(text=m.content or '',tool_calls=_decode_chat_calls(m.tool_calls),
                     usage=extract_usage(getattr(r,'usage',None)),raw_usage=getattr(r,'usage',None))


class StreamingChatTransport(ChatTransport):
    """Chat Completions with live token streaming; tool-call fragments reassembled."""
    name='chat+stream'
    streams=True

    def send_payload(self,payload,schemas,on_token=None):
        stream=self.provider.chat_stream(model=self.model,messages=payload,
                                         tools=self._tools(schemas),tool_choice='auto')
        parts:list[str]=[];slots:dict[int,dict[str,str]]={};usage=None;emitted=False
        for chunk in stream:
            cu=getattr(chunk,'usage',None)
            if cu is not None:usage=cu
            if not getattr(chunk,'choices',None):continue
            d=chunk.choices[0].delta
            piece=getattr(d,'content',None) if d else None
            if piece:
                parts.append(piece);emitted=True
                if on_token:on_token(piece)
            for tc in ((getattr(d,'tool_calls',None) or []) if d else []):
                slot=slots.setdefault(tc.index,{'id':'','name':'','args':''})
                if getattr(tc,'id',None):slot['id']=tc.id
                fn=getattr(tc,'function',None)
                if fn is not None:
                    if getattr(fn,'name',None):slot['name']=fn.name
                    if getattr(fn,'arguments',None):slot['args']+=fn.arguments
        calls=[ToolCall(id=s['id'],name=s['name'],arguments=s['args']) for _,s in sorted(slots.items())]
        return Reply(text=''.join(parts),tool_calls=calls,usage=extract_usage(usage),
                     streamed=emitted,raw_usage=usage)


class ResponsesTransport:
    """OpenAI Responses API. Tool calls are top-level `function_call` items answered
    by `function_call_output` items keyed on `call_id`; the system prompt travels
    out-of-band as `instructions`."""
    name='responses'
    streams=False

    def __init__(self,provider,model,system_prompt):
        self.provider=provider;self.model=model;self.system=system_prompt

    def encode(self,history,advisory=()):
        # Role items carry an explicit 'type': the Responses `input` list is
        # heterogeneous, and strict servers (llama.cpp) reject a bare role item with
        # "Cannot determine type of 'item'" rather than inferring it. OpenAI accepts
        # the explicit form too, so it is the portable spelling.
        out=[]
        for item in list(history)+_advisory(advisory):
            if isinstance(item,UserMsg):out.append({'type':'message','role':'user','content':item.text})
            elif isinstance(item,AssistantMsg):
                if item.text:out.append({'type':'message','role':'assistant','content':item.text})
                for c in item.tool_calls:
                    out.append({'type':'function_call','call_id':c.id,'name':c.name,
                                'arguments':c.arguments})
            elif isinstance(item,ToolResult):
                out.append({'type':'function_call_output','call_id':item.call_id,'output':item.output})
        return out

    def send(self,history,advisory,schemas,on_token=None):
        return self.send_payload(self.encode(history,advisory),schemas,on_token)

    def send_payload(self,payload,schemas,on_token=None):
        r=self.provider.responses(model=self.model,instructions=self.system,
                                  input=payload,tools=schemas)
        items=list(r.output)
        calls=[ToolCall(id=str(getattr(x,'call_id',None) or getattr(x,'id','') or ''),name=x.name,
                        arguments=x.arguments)
               for x in items if getattr(x,'type',None)=='function_call']
        return Reply(text=r.output_text or '',tool_calls=calls,
                     usage=extract_usage(getattr(r,'usage',None)),raw_usage=getattr(r,'usage',None))


def _decode_chat_calls(raw):
    return [ToolCall(id=c.id,name=c.function.name,arguments=c.function.arguments) for c in (raw or [])]


# Fallback order for api_mode='auto': try the richer transport, drop to the one
# every OpenAI-compatible server implements.
def build(mode,provider,model,system_prompt,stream=True):
    """Return the ordered transports to try for the configured api_mode."""
    chat=(StreamingChatTransport if stream else ChatTransport)(provider,model,system_prompt)
    responses=ResponsesTransport(provider,model,system_prompt)
    if mode=='responses':return [responses]
    if mode=='chat':return [chat]
    return [responses,chat]


__all__=['Reply','ChatTransport','StreamingChatTransport',
         'ResponsesTransport','build']
