"""Flag a run whose agent consulted the network for the answer.

A SWE-bench instance is a public commit with a public fix. If the agent can
reach the internet it can fetch the fixed file instead of reasoning about the
bug, and the result stops measuring anything. The model API needs network
access, so the container cannot simply be cut off -- until egress is restricted
to the API endpoint, every run is screened here and a run that fetched source
is reported as CONTAMINATED rather than scored.
"""
import json
import os
import re
import sys

FETCH = re.compile(
    r"urllib\.request|urlopen|\bcurl\b|\bwget\b|githubusercontent|"
    r"requests\.get\(|httpx\.|socket\.create_connection|pip\s+download",
    re.I)

# An attempt that the egress proxy refused is not contamination -- it is the
# control working. Pass the proxy log as a second argument and a run is only
# condemned when something outside the allowlist was actually reached.
trace = sys.argv[1]
proxy_log = sys.argv[2] if len(sys.argv) > 2 else None
leaked = None
if proxy_log:
    allowed = [l for l in open(proxy_log) if l.startswith("ALLOWED")]
    allowlist = os.environ.get("ALLOW_HOSTS", "opencode.ai").split(",")
    leaked = [l.strip() for l in allowed
              if not any(h.strip() in l for h in allowlist)]
rows = [json.loads(line) for line in open(trace) if line.strip()]
hits = []
for r in rows:
    if r.get("kind") != "tool_call":
        continue
    raw = r["payload"].get("arguments_raw") or ""
    if FETCH.search(raw):
        try:
            cmd = json.loads(raw).get("command", raw)
        except Exception:
            cmd = raw
        hits.append(" ".join(str(cmd).split())[:160])

print(f"tool calls: {sum(1 for r in rows if r.get('kind') == 'tool_call')}")
print(f"network-reaching calls: {len(hits)}")
for h in hits[:8]:
    print("   -", h)
if leaked is not None:
    print(f"egress reached outside the allowlist: {len(leaked)}")
    if leaked:
        print("CONTAMINATED — result is not a valid measurement")
        sys.exit(1)
    print(f"CLEAN — {len(hits)} network attempt(s), all refused by the proxy")
    sys.exit(0)
print("CONTAMINATED — result is not a valid measurement" if hits else "CLEAN")
sys.exit(1 if hits else 0)
