"""Discovering the model's context window.

No portable way exists to ask for it. OpenAI, DeepSeek, Kimi and GLM return
nothing useful from `/v1/models`; Anthropic and Gemini expose it only on their
native APIs, which this OpenAI-compatible client never speaks. Hardcoding a
model→window table is the common workaround and goes stale immediately.

So the window is *learned*, from three sources in descending authority:

1. **A rejection.** When a server refuses an oversized request it states the real
   limit. Parsing that is provider-agnostic — it works for every endpoint above,
   costs one failed request once per model, and is ground truth rather than a
   guess. Observation outranks configuration: a learned limit overrides even an
   explicit `CODER_CONTEXT_WINDOW`, because the server is never wrong about its
   own ceiling.
2. **A probe**, for local servers that do report it (vLLM, llama.cpp, Ollama).
3. **Configuration**, as the starting assumption.

Both observed sources are cached per (endpoint, model) so neither the failed
request nor the probe round-trip is repaid every session — but the cache records
*which* source produced a value, because they do not carry the same authority. A
cached probe stored as though it were a rejection outranks the operator's own
`CODER_CONTEXT_WINDOW` forever, which silently defeats the setting; that is the
bug this provenance exists to prevent.
"""
from __future__ import annotations

import json
import os
import re

# Phrasings used by OpenAI, vLLM, llama.cpp, DeepSeek, Together and others. Each
# must capture the *limit*, never the request size — picking the wrong number
# would teach the budget a ceiling that grows every time it is hit.
_LIMIT_PATTERNS=[
    re.compile(r"maximum context length is\s*(\d+)\s*tokens",re.I),
    re.compile(r"maximum context length\D{0,20}?(\d+)",re.I),
    re.compile(r"context length of only\s*(\d+)",re.I),
    re.compile(r"model'?s? max(?:imum)? (?:context|seq(?:uence)? len(?:gth)?)\D{0,20}?(\d+)",re.I),
    re.compile(r"max(?:imum)?_?(?:model_len|seq_len|context_length)\D{0,20}?(\d+)",re.I),
    re.compile(r"reduce the length of the messages.{0,80}?(\d+)\s*tokens",re.I),
    # llama.cpp: "request (90016 tokens) exceeds the available context size (65536
    # tokens)". The limit is the *second* number, so anchor on the phrase that
    # introduces it rather than taking the first match in the sentence.
    re.compile(r"context (?:size|window|length)[^.\d]{0,30}\(?(\d+)\)?\s*tokens",re.I),
    re.compile(r"context (?:size|window|length) of\s*(\d+)",re.I),
]
_OVERFLOW_HINTS=('context length','context window','context size','context_length_exceeded',
                 'too many tokens','maximum context','max_model_len','longer than the maximum',
                 'reduce the length','exceeds the maximum','exceeds the available',
                 'exceed_context_size','prompt is too long','string too long','input is too long')

MIN_PLAUSIBLE_WINDOW=512
MAX_PLAUSIBLE_WINDOW=20_000_000

# Where a window came from, in descending authority. A rejection is the server
# stating its own ceiling; a probe is the server describing its configuration;
# configuration is what the operator assumed. Only the first outranks the operator.
REJECTION='learned'
PROBE='probed'
CONFIGURED='configured'
_AUTHORITY={REJECTION:2,PROBE:1,CONFIGURED:0}

# Keys llama.cpp and friends put in a structured error body. Reading the number the
# server actually sent beats pattern-matching the sentence it wrapped around it.
_LIMIT_FIELDS=('n_ctx','max_model_len','max_context_length','context_length','limit')


def plausible(value):
    """Coerce a reported window to a sane int, or None.

    Servers return junk ('huge', null, a float); comparing that against the range
    raises, and trusting it would corrupt every later budget decision.
    """
    if isinstance(value,bool) or not isinstance(value,(int,float)):return None
    value=int(value)
    return value if MIN_PLAUSIBLE_WINDOW<=value<=MAX_PLAUSIBLE_WINDOW else None


def looks_like_overflow(text):
    """Whether an error reads like a context-length rejection rather than any other failure."""
    low=str(text).lower()
    return any(h in low for h in _OVERFLOW_HINTS)


def _limit_from_fields(blob):
    """Read the limit out of a structured error body, or None.

    Servers that reject an oversized request usually also *name* their ceiling in a
    field (llama.cpp sends `n_ctx`). That number needs no sentence parsing and cannot
    be confused with the request size, so it is tried before any regex.

    The body reaches us as the string form of an exception, so it is scanned for the
    field rather than parsed as JSON — the SDK's repr is not valid JSON.
    """
    for field in _LIMIT_FIELDS:
        for m in re.finditer(rf"['\"]{field}['\"]\s*[:=]\s*(\d+)",blob,re.I):
            found=plausible(int(m.group(1)))
            if found:return found
    return None


def parse_limit(text):
    """Extract the stated context limit from a rejection, or None.

    Returns None rather than guessing: a wrong number here silently corrupts every
    later budget decision, which is worse than not learning at all.
    """
    if not text or not looks_like_overflow(text):return None
    blob=str(text)
    found=_limit_from_fields(blob)
    if found:return found
    for pattern in _LIMIT_PATTERNS:
        m=pattern.search(blob)
        if m:
            try:value=int(m.group(1))
            except (TypeError,ValueError):continue
            found=plausible(value)
            if found:return found
    return None


def _root(base_url):
    """Server root for native endpoints that do not live under /v1."""
    return re.sub(r'/v\d+(?:beta)?/?(?:openai/?)?$','',str(base_url or '').rstrip('/'))


def probe(base_url,model,timeout=1.0):
    """Ask a local server for its served context window; None when unavailable.

    Best-effort by construction: every failure is swallowed, because a probe that
    can break startup is worse than no probe. Whichever endpoint answers also
    identifies the backend, so no configuration needs to say which to try.

    A connection failure aborts the whole sweep rather than retrying the other two
    paths against the same unreachable host — otherwise an endpoint that is simply
    down costs three timeouts on every start.
    """
    if not base_url:return None
    import httpx
    for fn in (_probe_vllm,_probe_llamacpp,_probe_ollama):
        try:
            value=fn(base_url,model,timeout)
        except (httpx.ConnectError,httpx.ConnectTimeout):
            return None
        except Exception:
            continue
        found=plausible(value)
        if found:return found
    return None


def _get_json(url,timeout,method='GET',body=None):
    import httpx
    r=httpx.request(method,url,timeout=timeout,json=body)
    r.raise_for_status()
    return r.json()


def _probe_vllm(base_url,model,timeout):
    """vLLM reports `max_model_len` on each model card in /v1/models."""
    data=_get_json(f"{str(base_url).rstrip('/')}/models",timeout)
    cards=data.get('data') or []
    for card in cards:
        if model and card.get('id')==model:return card.get('max_model_len')
    return cards[0].get('max_model_len') if cards else None


def _probe_llamacpp(base_url,model,timeout):
    """llama.cpp reports the served context at /props.

    `n_ctx` there is the slot's context, which is the total divided across
    `total_slots` — so it is what a single request may actually use.
    """
    data=_get_json(f'{_root(base_url)}/props',timeout)
    settings=data.get('default_generation_settings') or {}
    return settings.get('n_ctx') or data.get('n_ctx')


def _probe_ollama(base_url,model,timeout):
    """Ollama, read carefully.

    `parameters` is a newline-separated *string*, and `num_ctx` appears there only
    when a Modelfile set it. `model_info["<arch>.context_length"]` is deliberately
    NOT used as a fallback: that is the model's maximum, not what Ollama serves,
    and Ollama's real default is far smaller. Overestimating is worse than not
    knowing, because it silently disables reduction.
    """
    if not model:return None
    data=_get_json(f'{_root(base_url)}/api/show',timeout,method='POST',body={'model':model})
    for line in str(data.get('parameters') or '').splitlines():
        parts=line.split()
        if len(parts)>=2 and parts[0]=='num_ctx':
            try:return int(parts[1])
            except ValueError:return None
    return None


class WindowStore:
    """Per-(endpoint, model) cache of observed windows, so neither a rejection nor a
    probe is paid twice.

    Each entry records the value *and* the source that produced it, because the two
    observed sources carry different authority (see the module docstring). Entries
    written by older versions are bare integers; those are read back as rejections,
    which is what they were — probing did not cache before provenance existed.

    Best-effort: an unreadable or unwritable cache degrades to not remembering,
    never to failing a request.
    """

    def __init__(self,root):
        self.path=root/'.coder-agent'/'windows.json'

    def _load(self):
        try:
            data=json.loads(self.path.read_text())
            return data if isinstance(data,dict) else {}
        except Exception:
            return {}

    @staticmethod
    def key(base_url,model):
        return f'{base_url or "default"}::{model or "unknown"}'

    def entry(self,base_url,model):
        """Return (tokens, source) for a cached window, or (None, None)."""
        raw=self._load().get(self.key(base_url,model))
        if isinstance(raw,int):
            return (raw,REJECTION) if raw>0 else (None,None)
        if isinstance(raw,dict):
            value=plausible(raw.get('tokens'))
            source=raw.get('source')
            if value and source in _AUTHORITY:return value,source
        return None,None

    def get(self,base_url,model):
        """The cached window regardless of provenance; None when nothing is cached."""
        return self.entry(base_url,model)[0]

    def remember(self,base_url,model,tokens,source=REJECTION):
        """Cache a window, refusing to let a weaker source overwrite a stronger one."""
        if not tokens or source not in _AUTHORITY:return
        _,known=self.entry(base_url,model)
        if known is not None and _AUTHORITY[source]<_AUTHORITY[known]:return
        try:
            data=self._load();data[self.key(base_url,model)]={'tokens':int(tokens),'source':source}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_name(self.path.name+'.tmp')
            tmp.write_text(json.dumps(data,indent=2))
            os.replace(tmp,self.path)
        except Exception:
            pass


def resolve(config,store=None):
    """Best known window at startup, plus where it came from.

    Authority, highest first: a rejection (cached or not), then an explicit
    `CODER_CONTEXT_WINDOW`, then a probe, then the configured default. Setting the
    window by hand is a stated intention, so it suppresses probing *and* outranks a
    probe cached by an earlier session — only the server contradicting itself may
    override it.
    """
    cached,source=store.entry(config.base_url,config.model) if store else (None,None)
    if cached and source==REJECTION:return cached,REJECTION
    if getattr(config,'context_window_explicit',False):
        return config.context_window_tokens,CONFIGURED
    if cached:return cached,source
    found=probe(config.base_url,config.model)
    if found:
        if store:store.remember(config.base_url,config.model,found,PROBE)
        return found,PROBE
    return config.context_window_tokens,CONFIGURED
