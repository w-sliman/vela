"""Provider-neutral conversation items.

The agent's history holds only these. Wire formats — the Responses API's
`{'type':'function_call','call_id':…}` items and Chat Completions'
`{'role':'assistant','tool_calls':[…]}` messages — exist solely inside
`transports.py`, at the moment of encoding a request or decoding a reply.

That boundary is what lets the agent switch transports mid-conversation: the same
history re-encodes into either wire format, so a transport failure costs a retry
rather than the conversation. It is also why trimming, compaction, interrupt
repair and path harvesting each need one code path instead of two.
"""
from __future__ import annotations

from dataclasses import dataclass, field

INTERRUPTED='[interrupted by user]'


@dataclass
class ToolCall:
    """One tool invocation requested by the model."""
    id:str
    name:str
    arguments:str          # raw JSON string, parsed (and repaired) by the caller


@dataclass
class UserMsg:
    text:str
    role:str='user'


@dataclass
class AssistantMsg:
    text:str=''
    tool_calls:list[ToolCall]=field(default_factory=list)
    role:str='assistant'


@dataclass
class ToolResult:
    call_id:str
    output:str
    name:str=''
    role:str='tool'


def is_call(item):
    """True for an assistant turn that requested at least one tool."""
    return isinstance(item,AssistantMsg) and bool(item.tool_calls)


def answered_ids(items):
    """Ids of tool calls that already have a result among `items`."""
    return {r.call_id for r in items if isinstance(r,ToolResult)}


def item_text(item,limit=400):
    """One-line rendering for summarizer transcripts."""
    if isinstance(item,UserMsg):body=item.text
    elif isinstance(item,AssistantMsg):
        names=' '.join(c.name for c in item.tool_calls)
        body=f'{item.text}{f" [tools: {names}]" if names else ""}'
    elif isinstance(item,ToolResult):body=f'call {item.call_id}: {item.output[:200]}'
    else:body=str(item)
    return f'{item.role}: {str(body).replace(chr(10)," ")[:limit]}'


def tool_arguments(items):
    """Raw argument JSON from every tool call in `items`, for path harvesting."""
    return [c.arguments or '' for it in items if isinstance(it,AssistantMsg) for c in it.tool_calls]
