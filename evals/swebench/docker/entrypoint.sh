#!/bin/bash
# Run the agent on the prompt mounted at /out/prompt.txt, then copy every trace
# out before the container dies. /out is the only path shared with the host.
#
# Vela runs on the system interpreter, which has its dependencies; PATH still
# puts the project's virtualenv first, so a bare `python` in an agent command
# means the environment the project's tests need.
set -uo pipefail
cd /work/repo
git config --global --add safe.directory /work/repo
source /opt/start_httpbin.sh
BASE=$(git -C /work/repo rev-parse HEAD)
echo "$BASE" > /out/base_commit.txt

# --prompt-file, not a pipe: the REPL reads one line per turn, so piping this
# 67-line issue in would become sixteen separate requests.
timeout "${RUN_TIMEOUT:-1800}" /usr/local/bin/python -m vela --workspace /work/repo \
    --prompt-file /out/prompt.txt > /out/run.log 2>&1
echo "vela_exit=$?" > /out/exit_code.txt

git -C /work/repo diff "$BASE" > /out/agent.diff
git -C /work/repo diff "$BASE" --stat > /out/agent.diffstat
git -C /work/repo log --oneline "$BASE"..HEAD > /out/checkpoints.txt
cp -r /work/repo/.vela /out/vela-state 2>/dev/null || true
echo done
