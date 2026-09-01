#!/usr/bin/env bash
set -euo pipefail
mkdir -p workspace
cat > workspace/hello.py <<'PY'
def greet(name: str) -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    print(greet("world"))
PY
cat > workspace/test_hello.py <<'PY'
from hello import greet

def test_greet():
    assert greet("Ada") == "Hello, Ada!"
PY
echo "Demo project created in ./workspace"
echo "Run: source .venv/bin/activate && python -m vela --workspace ./workspace"
