import json
from dataclasses import asdict, is_dataclass

from .conversation import ToolResult, answered_ids, is_call


def _blocks(history):
    """Group history into atomic blocks that must never be split by trimming.

    A tool call and the results answering it move together; anything else is its
    own block. One rule, because history holds canonical items rather than two
    providers' wire formats.
    """
    blocks=[];i=0
    while i<len(history):
        if is_call(history[i]):
            ids={c.id for c in history[i].tool_calls};j=i+1
            while j<len(history) and isinstance(history[j],ToolResult) and history[j].call_id in ids:j+=1
            blocks.append(history[i:j]);i=j
        else:
            blocks.append(history[i:i+1]);i+=1
    return blocks


def _orphaned(history):
    """True when a tool result lost the call it answers — always a bug."""
    seen=set()
    for item in history:
        if is_call(item):seen.update(c.id for c in item.tool_calls)
        elif isinstance(item,ToolResult) and item.call_id not in seen:return True
    return False


def _size(block):
    return len(json.dumps([asdict(x) if is_dataclass(x) else x for x in block],default=str))


class ContextManager:
    def __init__(self,max_chars,max_history_items=None):
        self.max_chars=max_chars;self.max_history_items=max_history_items
    def trim(self,history):
        """Drop whole leading blocks while over budget; never orphan a tool call/result pair."""
        blocks=_blocks(history)
        total=sum(_size(b) for b in blocks)
        while len(blocks)>2 and total>self.max_chars:
            total-=_size(blocks[0]);blocks.pop(0)
        if self.max_history_items:
            items=sum(len(b) for b in blocks)
            while len(blocks)>1 and items>self.max_history_items:
                items-=len(blocks[0]);blocks.pop(0)
        return [x for b in blocks for x in b]


__all__=['ContextManager','_blocks','_orphaned','answered_ids']
