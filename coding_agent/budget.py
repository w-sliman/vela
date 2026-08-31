"""One owner for the question "does this payload fit?".

Three mechanisms used to guess independently: a char-budget trim on every turn, a
once-per-request auto-compact reading the *previous* call's usage, and a manual
`/compact`. None measured the actual outgoing payload against the actual limit.

Trimming and compaction were never two concerns either. Both answer *reduce the
conversation to fit* — compaction is the reduction that preserves knowledge,
dropping blocks is the one that does not. They are ordered here as preference and
fallback, so losing history is the exception rather than the default path.

Measurement runs on the encoded payload, because the transport is the only thing
that knows what will actually be sent. Estimates self-calibrate: whenever the
server reports true token usage for a payload we measured, the chars-per-token
ratio is corrected, so the estimate stops being a permanent guess.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass

from .conversation import ToolResult, is_call

# Starting ratio. English prose runs ~4 chars/token; the payloads here are JSON
# and source code, which run denser. Calibration replaces this within a turn or
# two of any endpoint that reports usage.
DEFAULT_CHARS_PER_TOKEN=3.6
MIN_CHARS_PER_TOKEN=1.5
MAX_CHARS_PER_TOKEN=12.0


def payload_chars(payload):
    """Serialized size of an encoded request payload."""
    return len(json.dumps(payload,default=_encode))


def _encode(obj):
    return asdict(obj) if is_dataclass(obj) else str(obj)


def blocks(history):
    """Group history into atomic blocks that must never be split.

    A tool call and the results answering it move together; anything else is its
    own block. One rule, because history holds canonical items rather than two
    providers' wire formats.
    """
    out=[];i=0
    while i<len(history):
        if is_call(history[i]):
            ids={c.id for c in history[i].tool_calls};j=i+1
            while j<len(history) and isinstance(history[j],ToolResult) and history[j].call_id in ids:j+=1
            out.append(history[i:j]);i=j
        else:
            out.append(history[i:i+1]);i+=1
    return out


def orphaned(history):
    """True when a tool result lost the call it answers — always a bug."""
    seen=set()
    for item in history:
        if is_call(item):seen.update(c.id for c in item.tool_calls)
        elif isinstance(item,ToolResult) and item.call_id not in seen:return True
    return False


class ContextBudget:
    """Owns measurement and reduction for one conversation.

    `reserve_tokens` is headroom for the model's reply — the reason a limit is not
    simply the context window. It is a knowable quantity, not a tuned percentage.
    """

    def __init__(self,window_tokens,reserve_tokens=None,chars_per_token=DEFAULT_CHARS_PER_TOKEN):
        self.window=int(window_tokens or 0)
        want=int(reserve_tokens) if reserve_tokens is not None else max(1024,self.window//8)
        # Headroom may never swallow the window: on a small window that would leave
        # a limit of zero, which reads as "no limit" and disables reduction exactly
        # where it is needed most.
        self.reserve=min(want,max(1,self.window//2)) if self.window else want
        self.chars_per_token=float(chars_per_token)
        self.calibrations=0

    @property
    def limit(self):
        """Tokens available to the prompt once the reply's headroom is set aside.

        Zero means the window is unknown — the only case where nothing is enforced.
        """
        return max(1,self.window-self.reserve) if self.window else 0

    def estimate(self,payload):
        """Token estimate for an encoded payload."""
        return int(payload_chars(payload)/self.chars_per_token)

    def fits(self,payload):
        """False only when we can both measure a limit and exceed it."""
        return True if not self.limit else self.estimate(payload)<=self.limit

    def calibrate(self,payload_chars_sent,reported_input_tokens):
        """Correct chars-per-token from a payload whose true cost the server reported.

        Turns the estimate into a measurement after the first usage-reporting call;
        endpoints that report nothing simply keep the default.
        """
        if not payload_chars_sent or not reported_input_tokens:return self.chars_per_token
        ratio=payload_chars_sent/float(reported_input_tokens)
        if MIN_CHARS_PER_TOKEN<=ratio<=MAX_CHARS_PER_TOKEN:
            self.chars_per_token=ratio;self.calibrations+=1
        return self.chars_per_token

    def reducible(self,history):
        """Whether anything can still be given up; guards against reducing forever."""
        return len(blocks(history))>1

    def drop_oldest(self,history):
        """Fallback reduction: discard the oldest whole block.

        Lossy and unintelligent — reachable only when summarizing is unavailable or
        has failed, so a conversation degrades rather than dying.
        """
        bl=blocks(history)
        if len(bl)<=1:return list(history),0
        dropped=len(bl[0])
        return [x for b in bl[1:] for x in b],dropped
