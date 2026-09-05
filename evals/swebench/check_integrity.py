"""Flag a run whose agent consulted the network for the answer.

A SWE-bench instance is a public commit with a public fix. If the agent can
reach the internet it can fetch the fixed file instead of reasoning about the
bug, and the result stops measuring anything. The model API needs network
access, so the container cannot simply be cut off -- until egress is restricted
to the API endpoint, every run is screened here and a run that fetched source
is reported as CONTAMINATED rather than scored.
"""
import json
import re
import sys

FETCH = re.compile(
    r"urllib\.request|urlopen|\bcurl\b|\bwget\b|githubusercontent|"
    r"requests\.get\(|httpx\.|socket\.create_connection|pip\s+download",
    re.I)

trace = sys.argv[1]
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
print("CONTAMINATED — result is not a valid measurement" if hits else "CLEAN")
sys.exit(1 if hits else 0)
