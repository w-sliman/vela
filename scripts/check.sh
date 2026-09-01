#!/usr/bin/env bash
set -euo pipefail
python -m pytest -q
python -m compileall -q vela
# A missing checker used to print "skipping" and still exit 0, so a green local run
# could hide what CI would reject. Report it as the gap it is.
missing=0
for tool in ruff mypy; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is not installed: pip install -e '.[dev]'" >&2; missing=1; }
done
[ "$missing" -eq 0 ] || exit 1
ruff check vela
mypy vela
echo "checks passed"
