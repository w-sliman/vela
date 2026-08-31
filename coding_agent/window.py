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

Learned values are cached per (endpoint, model) so the one failed request is not
repaid every session.
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
    re.compile(r"context window[^.\d]{0,30}(\d+)\s*tokens",re.I),
]
_OVERFLOW_HINTS=('context length','context window','context size','context_length_exceeded',
                 'too many tokens','maximum context','max_model_len','longer than the maximum',
                 'reduce the length','exceeds the maximum','exceeds the available',
                 'prompt is too long','string too long','input is too long')

MIN_PLAUSIBLE_WINDOW=512
MAX_PLAUSIBLE_WINDOW=20_000_000


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


def parse_limit(text):
    """Extract the stated context limit from a rejection, or None.

    Returns None rather than guessing: a wrong number here silently corrupts every
    later budget decision, which is worse than not learning at all.
    """
    if not text or not looks_like_overflow(text):return None
    blob=str(text)
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
    """Per-(endpoint, model) cache of learned windows, so a rejection is paid once.

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

    def get(self,base_url,model):
        value=self._load().get(self.key(base_url,model))
        return int(value) if isinstance(value,int) and value>0 else None

    def remember(self,base_url,model,tokens):
        if not tokens:return
        try:
            data=self._load();data[self.key(base_url,model)]=int(tokens)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_name(self.path.name+'.tmp')
            tmp.write_text(json.dumps(data,indent=2))
            os.replace(tmp,self.path)
        except Exception:
            pass


def resolve(config,store=None):
    """Best known window at startup, plus where it came from.

    Order: a previously learned value, then a probe, then configuration. Probing
    is skipped when the window was set explicitly — that is a stated intention,
    and only a rejection should override it.
    """
    learned=store.get(config.base_url,config.model) if store else None
    if learned:return learned,'learned'
    if not getattr(config,'context_window_explicit',False):
        found=probe(config.base_url,config.model)
        if found:
            if store:store.remember(config.base_url,config.model,found)
            return found,'probed'
    return config.context_window_tokens,'configured'
