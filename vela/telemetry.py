from __future__ import annotations
import time

USAGE_ADVICE=('endpoint returned no usage object. If you control the server, enable usage '
 "reporting: OpenAI always includes it; streaming requires stream_options={'include_usage': True}; "
 'vLLM/LM Studio/llama.cpp have their own usage/stats options - check the provider docs.')

def _uget(usage,name):
    if isinstance(usage,dict):return usage.get(name)
    return getattr(usage,name,None)

def extract_usage(usage):
    """Normalize Responses/Chat usage into {'input','output','total',...}; None when absent.

    Accepts SDK objects or plain dicts, in either field-naming convention.
    An all-zero/empty usage object counts as absent (never fabricate zeros).
    """
    if usage is None:return None
    def g(*names):
        for n in names:
            v=_uget(usage,n)
            if v is not None:return int(v)
        return 0
    inp=g('input_tokens','prompt_tokens');out=g('output_tokens','completion_tokens');tot=g('total_tokens')
    if inp==0 and out==0 and tot==0:return None
    d={'input':inp,'output':out,'total':tot or inp+out}
    details=_uget(usage,'output_tokens_details')
    reasoning=_uget(details,'reasoning_tokens') if details is not None else None
    if reasoning is not None:d['reasoning']=int(reasoning)
    return d

class Timer:
 def __enter__(self): self.start=time.perf_counter(); self.elapsed_ms=0.0; return self
 def __exit__(self,*_): self.elapsed_ms=(time.perf_counter()-self.start)*1000
class Metrics:
 def __init__(self): self.input_tokens=0; self.output_tokens=0; self.calls=0; self.tool_calls=0; self.latency_ms=0; self.estimated_cost_usd=0.0; self.missing_usage=0; self.last_input_tokens=0
 def add(self,usage=None,latency_ms=0):
  """Record one model request. Responses without usable usage are counted, never guessed."""
  self.calls+=1; self.latency_ms+=latency_ms
  u=extract_usage(usage)
  if u is None:self.missing_usage+=1;return
  self.input_tokens+=u['input']; self.output_tokens+=u['output']; self.last_input_tokens=u['input']
 def price(self,input_per_million=0.0,output_per_million=0.0):
  self.estimated_cost_usd=(self.input_tokens/1_000_000)*input_per_million+(self.output_tokens/1_000_000)*output_per_million
 def as_dict(self): return self.__dict__.copy()
