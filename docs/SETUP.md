# Setup

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

Every knob is documented inline in `.env.example` — approval mode, context
window size (for the live context-% display), auto-compact, streaming,
auto-checkpoint, retries, prices. Defaults are sensible; only the two keys
above are required.

## Run

```bash
python -m coding_agent --workspace ./workspace
```

To use an existing project:

```bash
python -m coding_agent --workspace /absolute/path/to/project
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
