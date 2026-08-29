import json

def _get(h,key,default=None):
    """Read a field from either a dict (chat format) or an SDK object (responses format)."""
    if isinstance(h,dict):return h.get(key,default)
    return getattr(h,key,default)

def _blocks(history):
    """Group history into atomic blocks that must never be split by trimming."""
    blocks=[];i=0
    while i<len(history):
        h=history[i]
        if _get(h,'role')=='assistant' and _get(h,'tool_calls'):
            ids={c.get('id') for c in _get(h,'tool_calls') or []}
            j=i+1
            while j<len(history) and _get(history[j],'role')=='tool' and _get(history[j],'tool_call_id') in ids:j+=1
            blocks.append(history[i:j]);i=j
        elif _get(h,'type')=='function_call':
            cid=_get(h,'call_id',_get(h,'id'));j=i+1
            while j<len(history) and _get(history[j],'type')=='function_call_output' and _get(history[j],'call_id')==cid:j+=1
            blocks.append(history[i:j]);i=j
        else:
            blocks.append(history[i:i+1]);i+=1
    return blocks

class ContextManager:
    def __init__(self,max_chars,max_history_items=None):
        self.max_chars=max_chars;self.max_history_items=max_history_items
    def trim(self,history):
        """Drop whole leading blocks while over budget; never orphan a tool call/output pair."""
        blocks=_blocks(history)
        def bsize(b):return len(json.dumps(b,default=str))
        total=sum(bsize(b) for b in blocks)
        while len(blocks)>2 and total>self.max_chars:
            total-=bsize(blocks[0]);blocks.pop(0)
        if self.max_history_items:
            while len(blocks)>1 and sum(len(b) for b in blocks)>self.max_history_items:blocks.pop(0)
        return [x for b in blocks for x in b]
