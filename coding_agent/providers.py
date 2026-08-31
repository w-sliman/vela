from __future__ import annotations
import time


def backoff_delays(retries):
    """Attempt schedule: one immediate try, then exponential backoff."""
    return [0.0]+[0.5*(2**i) for i in range(max(0,int(retries)))]


def with_retries(fn,delays,on_wait=None,on_failure=None):
    """Call fn with exponential backoff; re-raise the last error after all attempts.

    Lives beside the client rather than on the agent because retrying is a property
    of talking to a provider — sub-agents need it just as much as the main loop, and
    the one call site that lacked it was the one that fell over on a transient error.
    """
    last=None
    for i,delay in enumerate(delays):
        if delay:
            if on_wait:on_wait(delay,i)
            time.sleep(delay)
        try:return fn()
        except Exception as exc:
            last=exc
            if on_failure:on_failure(exc,i)
    raise last
# Per-request timeout so a hung endpoint cannot block the REPL for the
# OpenAI client's ~10-minute default. For streams this bounds the gap
# between chunks, not the total stream duration.
DEFAULT_TIMEOUT=120.0
class OpenAICompatibleProvider:
    def __init__(self,api_key,base_url=None,timeout=DEFAULT_TIMEOUT):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError('openai package is required. Run pip install -r requirements.txt') from e
        self.client=OpenAI(api_key=api_key,base_url=base_url,timeout=timeout)
    def responses(self,**kw): return self.client.responses.create(**kw)
    def chat(self,**kw): return self.client.chat.completions.create(**kw)
    def chat_stream(self,**kw):
        """Streaming chat completion; returns the stream object (create happens eagerly)."""
        kw.update(stream=True,stream_options={'include_usage':True})
        return self.client.chat.completions.create(**kw)
