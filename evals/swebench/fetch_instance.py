"""Fetch one SWE-bench Verified instance and lay out its run directory."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

inst_id = sys.argv[1]
out = sys.argv[2]
repo = inst_id.split("__")[0] + "/" + inst_id.split("__")[1].rsplit("-", 1)[0]
url = ("https://datasets-server.huggingface.co/filter?"
       + urllib.parse.urlencode({
           "dataset": "princeton-nlp/SWE-bench_Verified", "config": "default",
           "split": "test", "where": f"\"instance_id\"='{inst_id}'", "length": "1"}))

row = None
for attempt in range(8):                       # the dataset index warms up lazily
    with urllib.request.urlopen(url, timeout=60) as r:
        payload = json.load(r)
    if payload.get("rows"):
        row = payload["rows"][0]["row"]
        break
    print("waiting for dataset index:", payload.get("error", payload)[:80])
    time.sleep(20)
if row is None:
    sys.exit(f"could not fetch {inst_id}")

os.makedirs(out, exist_ok=True)
json.dump(row, open(f"{out}/instance.json", "w"), indent=1)
open(f"{out}/test_patch.diff", "w").write(row["test_patch"])
open(f"{out}/gold_patch.diff", "w").write(row["patch"])
open(f"{out}/problem.txt", "w").write(row["problem_statement"])
with open(f"{out}/prompt.txt", "w") as f:
    f.write(row["problem_statement"])
    f.write("\n\nFix this bug in the repository. Do not modify any file under "
            "tests/. Run the relevant tests to confirm your fix.\n")
print(f"{inst_id}  repo={row['repo']}  base={row['base_commit'][:10]}  "
      f"F2P={len(json.loads(row['FAIL_TO_PASS']))}  P2P={len(json.loads(row['PASS_TO_PASS']))}")
