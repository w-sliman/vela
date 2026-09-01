# Setup

> **Unreleased project.** There is no packaged release or license grant; install from
> a checkout only, and expect interfaces to change.

## Requirements

- Python 3.11+
- OpenAI API key
- A tool-capable model available to your API account

## Virtual environment

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and set:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=...
```

Never commit `.env`.

You do not normally need to set `VELA_CONTEXT_WINDOW`: the agent probes local
servers that report their window and otherwise learns the real limit from the first
rejection, caching it per endpoint and model. Set it only to skip the probe — a
server rejection still overrides it.

Every knob is documented inline in `.env.example` — approval mode, context
window size (drives the context budget and the live context-% display),
context reduction, streaming,
auto-checkpoint, retries, prices. Defaults are sensible; only the two keys
above are required.

## Run

```bash
python -m vela --workspace ./workspace
```

To use an existing project:

```bash
python -m vela --workspace /absolute/path/to/project
```

## Test

Activate the venv first (the suite and smoke check expect `pytest` on PATH):

```bash
source .venv/bin/activate
pytest -q
python smoke/runner.py
./scripts/check.sh        # pytest + compileall + ruff/mypy when installed
```

No API key or network is needed for the unit tests.
