from __future__ import annotations
import time


def backoff_delays(retries):
    """Attempt schedule: one immediate try, then exponential backoff."""
    return [0.0]+[0.5*(2**i) for i in range(max(0,int(retries)))]


# 4xx statuses that a retry can actually clear. Everything else in that range is a
# verdict on the request itself: resending it unchanged just pays the latency again.
_RETRYABLE_CLIENT_STATUS={408,409,425,429}


def is_transient(exc):
    """Whether re-sending the identical request could plausibly succeed.

    A malformed request is deterministic — the 400 that taught us this repeated three
    times per call and delayed the transport fallback that actually fixed it. Errors
    with no status (connection resets, timeouts) are assumed transient, because that
    is what they almost always are.
    """
    status=getattr(exc,'status_code',None)
    if status is None:status=getattr(getattr(exc,'response',None),'status_code',None)
    if not isinstance(status,int):return True
    if status in _RETRYABLE_CLIENT_STATUS:return True
    return not 400<=status<500


def with_retries(fn,delays,on_wait=None,on_failure=None,retry_on=is_transient):
    """Call fn with exponential backoff; re-raise the last error after all attempts.

    Lives beside the client rather than on the agent because retrying is a property
    of talking to a provider — sub-agents need it just as much as the main loop, and
    the one call site that lacked it was the one that fell over on a transient error.

    `retry_on` decides which failures are worth repeating; a deterministic rejection
    is re-raised immediately so the caller can act on it while it is still cheap.
    """
    last:Exception|None=None
    for i,delay in enumerate(delays):
        if delay:
            if on_wait:on_wait(delay,i)
            time.sleep(delay)
        try:return fn()
        except Exception as exc:
            last=exc
            if on_failure:on_failure(exc,i)
            if retry_on is not None and not retry_on(exc):raise
    # Only reachable once every attempt has failed, so `last` is always set; an
    # empty schedule would mean "never call fn at all", which is a caller bug.
    if last is None:raise ValueError('with_retries needs at least one attempt in `delays`')
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
