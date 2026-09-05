#!/bin/bash
# End-to-end for one SWE-bench instance: fetch, build, validate, run, grade.
#
# Validation is not optional. An instance whose base grades RESOLVED, or whose
# gold does not, is a broken environment and its run would be noise, so the
# script stops there rather than producing a number.
set -uo pipefail
ID="$1"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
R="$ROOT/evals/swebench/runs/$ID"
IMG="vela-swebench:${ID//__/-}"
mkdir -p "$R"
rm -f "$R/status"          # never let a previous attempt's verdict linger

log() { echo "[$ID] $*"; }

REPO=$(python3 -c "import json;print(json.load(open('$R/instance.json'))['repo'])")
BASE=$(python3 -c "import json;print(json.load(open('$R/instance.json'))['base_commit'])")
log "repo=$REPO base=${BASE:0:10}"

log "building image"
docker build -q --build-arg REPO_URL="https://github.com/$REPO" --build-arg BASE_COMMIT="$BASE" \
  --build-arg PROJECT_PYTHON="${PROJECT_PYTHON:-3.12}" \
  -f "$ROOT/evals/swebench/docker/Dockerfile" -t "$IMG" "$ROOT" > "$R/build.log" 2>&1 || {
    log "BUILD FAILED"; tail -5 "$R/build.log"; echo "build_failed" > "$R/status"; exit 3; }

grade_in() {   # $1 = extra patch to apply first (or "-")
  docker run --rm --network none -v "$R:/inst:ro" \
    -v "$ROOT/evals/swebench/grade.py:/grade.py:ro" --entrypoint bash "$IMG" -c "
      cd /work/repo; source /opt/start_httpbin.sh
      [ '$1' != '-' ] && { git apply '$1' || exit 9; }
      git apply /inst/test_patch.diff || exit 9
      EX=''; [ -f /inst/excluded_ids.txt ] && EX=/inst/excluded_ids.txt
      python /grade.py /work/repo /work/env/bin/python /inst/instance.json \$EX
    " 2>&1
}

log "validating base (expect NOT RESOLVED)"
BASE_OUT=$(grade_in -); echo "$BASE_OUT" > "$R/validate_base.txt"
log "validating gold (expect RESOLVED)"
GOLD_OUT=$(grade_in /inst/gold_patch.diff); echo "$GOLD_OUT" > "$R/validate_gold.txt"
# A PASS_TO_PASS id that fails with the gold patch applied cannot be measuring
# the agent's work -- it is the environment. Those are excluded and validation
# is retried once. A FAIL_TO_PASS id that gold cannot make pass is different:
# the instance simply does not reproduce here, and no exclusion may rescue it.
if grep -qE "^FAIL_TO_PASS +([0-9]+)/\1$" <<<"$GOLD_OUT" && ! grep -q "^RESOLVED" <<<"$GOLD_OUT"; then
  grep -E "^   (FAILED|MISSING|ERROR)" <<<"$GOLD_OUT" | awk '{print $2}' >> "$R/excluded_ids.txt"
  sort -u -o "$R/excluded_ids.txt" "$R/excluded_ids.txt"
  log "excluding $(wc -l < "$R/excluded_ids.txt") id(s) that gold cannot pass here; revalidating"
  BASE_OUT=$(grade_in -); echo "$BASE_OUT" > "$R/validate_base.txt"
  GOLD_OUT=$(grade_in /inst/gold_patch.diff); echo "$GOLD_OUT" > "$R/validate_gold.txt"
fi
if ! grep -q "^NOT RESOLVED" <<<"$BASE_OUT" || ! grep -q "^RESOLVED" <<<"$GOLD_OUT"; then
  log "INSTANCE INVALID (base/gold validation failed)"; echo "invalid_env" > "$R/status"; exit 4
fi
log "instance valid"

rm -rf "$R/out"; mkdir -p "$R/out"; cp "$R/prompt.txt" "$R/out/prompt.txt"
set -a; . "$ROOT/.env"; set +a
log "running agent (egress restricted)"
docker run --rm --network vela-eval-int --memory=3g --cpus=2 --pids-limit=256 \
  -e HTTPS_PROXY=http://vela-proxy:8888 -e HTTP_PROXY=http://vela-proxy:8888 \
  -e VELA_SHELL_NETWORK=0 \
  -e OPENAI_API_KEY -e OPENAI_BASE_URL -e OPENAI_MODEL -e OPENAI_API_MODE \
  -e RUN_TIMEOUT="${RUN_TIMEOUT:-1800}" -v "$R/out:/out" "$IMG" > "$R/out/docker.log" 2>&1

log "grading agent patch"
if [ -s "$R/out/agent.diff" ]; then
  grep -q "^+++ b/tests/" "$R/out/agent.diff" && echo "TESTS_TOUCHED" > "$R/out/tests_touched.txt"
  grade_in /inst/out/agent.diff > "$R/out/grade.txt" 2>&1
else
  echo "NO CHANGES MADE" > "$R/out/grade.txt"
fi
TRACE=$(ls "$R/out"/vela-state/sessions/* 2>/dev/null | head -1)
docker logs vela-proxy > "$R/out/proxy.log" 2>&1
[ -n "$TRACE" ] && "$ROOT/.venv/bin/python" "$ROOT/evals/swebench/check_integrity.py" "$TRACE" "$R/out/proxy.log" > "$R/out/integrity.txt" 2>&1
echo "done" > "$R/status"
log "RESULT: $(tail -1 "$R/out/grade.txt") | $(tail -1 "$R/out/integrity.txt" 2>/dev/null)"
