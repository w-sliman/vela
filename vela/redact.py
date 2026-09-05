"""Keep secrets out of anything we write down.

Subprocess environments are scrubbed before a command runs, but that only stops
a child *inheriting* a secret -- it does nothing about a command that prints the
one Vela itself was given. An agent investigating its own environment (`env`,
`/proc/self/environ`) captures the API key into its tool output, and from there
into the session trace and the terminal, where it outlives the run. This
replaces any value that looks like a live credential on the way out.
"""
from __future__ import annotations

import os
import re

PLACEHOLDER = '[redacted]'
# Long, high-entropy, provider-shaped tokens. Deliberately narrow: a false
# positive silently corrupts a trace, which is the artifact people debug from.
_PATTERNS = [
    re.compile(r'\bsk-[A-Za-z0-9_\-]{16,}'),               # OpenAI-style
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'),            # GitHub
    re.compile(r'\bxox[abposr]-[A-Za-z0-9\-]{10,}'),       # Slack
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),                   # AWS key id
]
_SECRET_ENV_RE = re.compile(r'(API_?KEY|TOKEN|SECRET|PASSWORD)', re.I)
_MIN_ENV_SECRET = 8


def _live_secrets():
    """Values of secret-shaped environment variables, longest first.

    Whatever the patterns miss, the process's own credentials are known exactly,
    so they can be removed by value.
    """
    vals = [v for k, v in os.environ.items()
            if _SECRET_ENV_RE.search(k) and v and len(v) >= _MIN_ENV_SECRET]
    return sorted(set(vals), key=len, reverse=True)


def redact(text):
    """Return *text* with credential-shaped values replaced."""
    if not text:
        return text
    s = str(text)
    for value in _live_secrets():
        if value in s:
            s = s.replace(value, PLACEHOLDER)
    for pattern in _PATTERNS:
        s = pattern.sub(PLACEHOLDER, s)
    return s


def redact_obj(obj):
    """Redact recursively through the containers a trace payload is made of."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_obj(v) for v in obj)
    return obj
