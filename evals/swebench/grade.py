"""Grade one SWE-bench instance against a checkout the agent has already edited.

Usage: grade.py <repo> <python> <instance.json> [excluded_ids.txt]

Passing node ids on the command line is unreliable: some ids in the dataset
contain spaces and were split when the lists were generated, so the grader runs
the test files once with -rA and reads each test's status out of the report,
matching a split id by prefix.
"""
import json
import re
import subprocess
import sys

repo, py, inst_path = sys.argv[1], sys.argv[2], sys.argv[3]
inst = json.load(open(inst_path))

# Some parametrized ids embed absolute filesystem paths, so they can never match
# outside the image the dataset was built in. They are excluded only when a run
# against the gold patch proves they are unmatchable here -- never to make a
# failing result look better.
excluded = set()
if len(sys.argv) > 4:
    excluded = {line.strip() for line in open(sys.argv[4]) if line.strip()}

f2p = [i for i in json.loads(inst["FAIL_TO_PASS"]) if i not in excluded]
p2p = [i for i in json.loads(inst["PASS_TO_PASS"]) if i not in excluded]
files = sorted({i.split("::")[0] for i in f2p + p2p})

proc = subprocess.run(
    [py, "-m", "pytest", "-rA", "-q", "--no-header",
     "-o", "addopts=", "-p", "no:cacheprovider", *files],
    cwd=repo, capture_output=True, text=True, timeout=1800)
out = proc.stdout + proc.stderr

status = {}
for line in out.splitlines():
    m = re.match(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+(\S.*?)\s*$", line)
    if m:
        status[m.group(2)] = m.group(1)


def lookup(node):
    if node in status:
        return status[node]
    hits = {v for k, v in status.items() if k.startswith(node)}   # split-id fallback
    return hits.pop() if len(hits) == 1 else "MISSING"


def score(ids, want):
    ok = [i for i in ids if lookup(i) in want]
    bad = [(i, lookup(i)) for i in ids if lookup(i) not in want]
    return ok, bad


f2p_ok, f2p_bad = score(f2p, {"PASSED", "XFAIL"})
p2p_ok, p2p_bad = score(p2p, {"PASSED", "XFAIL", "SKIPPED"})
resolved = not f2p_bad and not p2p_bad

print(f"collected statuses: {len(status)}"
      + (f"  (excluded {len(excluded)} env-dependent ids)" if excluded else ""))
print(f"FAIL_TO_PASS  {len(f2p_ok)}/{len(f2p)}")
for i, s in f2p_bad[:5]:
    print(f"   {s:8} {i}")
print(f"PASS_TO_PASS  {len(p2p_ok)}/{len(p2p)}")
for i, s in p2p_bad[:5]:
    print(f"   {s:8} {i}")
print("RESOLVED" if resolved else "NOT RESOLVED")
