# Runs the agent itself inside the container, not just the repository under test.
# That is the point: run_command has no path confinement, so the container is the
# boundary. Nothing here reaches the host except the mounted output directory.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

ARG REPO_URL
ARG BASE_COMMIT
ARG TEST_DEPS="pytest pytest-httpbin httpbin trustme"
# The project under test and Vela need different interpreters: Vela requires
# 3.11+, while pre-2015 code cannot run on 3.10+ at all (collections.Mapping was
# removed). Vela stays on the image's own 3.12; the project gets whichever
# version its era needs, installed standalone by uv.
ARG PROJECT_PYTHON=3.12
RUN python -m pip install --quiet uv

# The project under test gets its own virtualenv, ahead of Vela's on PATH, so a
# bare `python` in an agent command means the project's environment.
# Every ref other than the base commit is destroyed after checkout. A full clone
# carries the fix for the instance under test in its own history, and the agent
# will find it: given one, it ran `git log --all` looking for the fix commit by
# name. What remains reachable here is the base commit and its ancestors.
RUN git clone --quiet "$REPO_URL" /work/repo \
    && git -C /work/repo checkout --quiet --detach "$BASE_COMMIT" \
    && git -C /work/repo remote remove origin \
    && git -C /work/repo for-each-ref --format='%(refname)' \
         | xargs -r -n1 git -C /work/repo update-ref -d \
    && git -C /work/repo reflog expire --expire=now --all \
    && git -C /work/repo gc --quiet --prune=now \
    && uv python install "$PROJECT_PYTHON" \
    && uv venv --quiet --python "$PROJECT_PYTHON" /work/env \
    && VIRTUAL_ENV=/work/env uv pip install --quiet -e /work/repo \
    && VIRTUAL_ENV=/work/env uv pip install --quiet $TEST_DEPS

COPY requirements.txt /opt/vela/requirements.txt
RUN python -m pip install --quiet -r /opt/vela/requirements.txt
COPY vela /opt/vela/vela

WORKDIR /work/repo
ENV PYTHONPATH=/opt/vela \
    PATH=/work/env/bin:/usr/local/bin:/usr/bin:/bin \
    VELA_APPROVAL_MODE=auto \
    VELA_STREAM=0 \
    VELA_DEBUG=1
COPY evals/swebench/docker/start_httpbin.sh /opt/start_httpbin.sh
COPY evals/swebench/docker/entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh /opt/start_httpbin.sh
ENTRYPOINT ["/opt/entrypoint.sh"]
