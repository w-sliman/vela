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

# The project under test gets its own virtualenv, ahead of Vela's on PATH, so a
# bare `python` in an agent command means the project's environment.
RUN git clone --quiet "$REPO_URL" /work/repo \
    && git -C /work/repo checkout --quiet "$BASE_COMMIT" \
    && python -m venv /work/env \
    && /work/env/bin/python -m pip install --quiet --upgrade pip \
    && /work/env/bin/python -m pip install --quiet -e /work/repo \
    && /work/env/bin/python -m pip install --quiet $TEST_DEPS

COPY requirements.txt /opt/vela/requirements.txt
RUN python -m pip install --quiet -r /opt/vela/requirements.txt
COPY vela /opt/vela/vela

WORKDIR /work/repo
ENV PYTHONPATH=/opt/vela \
    PATH=/work/env/bin:/usr/local/bin:/usr/bin:/bin \
    VELA_APPROVAL_MODE=auto \
    VELA_STREAM=0
COPY evals/swebench/docker/entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh
ENTRYPOINT ["/opt/entrypoint.sh"]
