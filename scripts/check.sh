#!/usr/bin/env bash
set -euo pipefail
python -m pytest -q
python -m compileall -q coding_agent
if command -v ruff >/dev/null 2>&1; then ruff check coding_agent; else echo "ruff not installed; skipping"; fi
if command -v mypy >/dev/null 2>&1; then mypy coding_agent; else echo "mypy not installed; skipping"; fi
echo "checks passed"
