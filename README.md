# Workspace Coding Agent

A local coding agent that runs entirely from a normal Python `venv`. No Docker is required.

It gives you a Rich terminal interface where an LLM can inspect a workspace, create/edit files, apply patches, run tests/scripts, diagnose failures, and iterate. The model proposes actions; deterministic Python tools perform the actual operations.

## Quick start

Requirements: Python 3.11+ and an OpenAI API key.

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set these in `.env`:

```text
OPENAI_API_KEY=your_key
OPENAI_MODEL=your_tool_capable_model
```

Run:

```bash
python -m coding_agent --workspace ./workspace
```

Or install the package in editable mode:

```bash
pip install -e .
coder --workspace ./workspace
```

## First tasks to try

```text
create a small Python calculator package with pytest tests
```

```text
inspect this project, find the highest-value bug, fix it, and run the relevant tests
```

```text
review this repository for correctness issues; do not modify files
```

## CLI commands

- `/help` — commands
- `/pwd` — workspace path
- `/tree` — workspace tree
- `/model` — configured model
- `/usage` — session token usage, estimated cost, context fill
- `/compact [focus]` — summarize older conversation turns; optional focus instruction
- `/undo` — revert the workspace to the state before the last agent edit
- `/memory` — persistent memory
- `/sessions [n]` — list recent session traces (id, start, turns, first request)
- `/resume [id|#|last]` — continue a past session as fresh digest context (`#N`/short digits = Nth newest, otherwise id prefix)
- `/history` — recent session events
- `/clear` — clear current model context
- `/quit` — exit

## Observability & context management

- Assistant text streams live as tokens (chat transport; `CODER_STREAM=0` disables).
- Every model call journals an exact `usage` event (input/output tokens) to the session trace; calls without server-reported usage are counted and flagged, never estimated.
- A live status line shows tokens in/out/total plus current context fill after each model turn; `/usage` shows session totals.
- When context crosses `CODER_AUTO_COMPACT_PCT` (default 80%) of `CODER_CONTEXT_WINDOW`, older turns are summarized automatically once per request (`CODER_AUTO_COMPACT=0` disables).
- Failed model requests retry with exponential backoff (`CODER_REQUEST_RETRIES`, default 2) before falling back from the Responses API to Chat Completions.
- Subprocesses run with secret-shaped environment variables (API keys, tokens) removed.
- Each successful edit auto-commits a git checkpoint in the workspace (`CODER_AUTO_CHECKPOINT=0` disables); `/undo` reverts the last one.
- Relevant project memories are retrieved lexically once per user request and attached as advisory context (`CODER_MEMORY_INJECT=0` disables; tuned by `CODER_MEMORY_TOPK`, `CODER_MEMORY_MAX_CHARS`, `CODER_MEMORY_MIN_SCORE`). `/compact` also distills durable decisions into memory (`CODER_MEMORY_DISTILL=0` disables). See `docs/MEMORY.md`.
- Past sessions can be continued: `/resume` rebuilds task state from any recorded trace as a compact digest (never as raw replay), keeping history/pair integrity intact.

## Architecture

```text
User -> Rich CLI -> LLM controller -> function tools
                                      |
                         +------------+-------------+
                         |                          |
                    filesystem                   shell
                         |                          |
                         +------------+-------------+
                                      |
                              deterministic policy
                                      |
                                 local workspace
```

The workspace is the filesystem boundary. File paths escaping it are rejected. Risky shell commands require user approval.

This is **not a security sandbox**: shell commands ultimately run as your local user. For untrusted repositories, use a VM/container outside this application.

## LLM

The project uses the OpenAI Responses API with function tools. The model is configured with `OPENAI_MODEL`; no model name is hard-coded so you can choose what is available to your account.

The agent loops over model responses and tool calls until it gets a final response. Tool results are fed back into the model so it can inspect, change, test, and iterate.

## Project layout

```text
coding_agent/
  cli.py       # Rich interface
  llm.py       # Responses API loop
  tools.py     # tool schemas + dispatcher
  workspace.py # bounded file access
  shell.py     # subprocess execution
  policy.py    # deterministic safety policy
  session.py   # JSONL session logging
  config.py    # environment config
  prompts.py   # agent instructions

tests/
docs/
scripts/
workspace/
```

## Tests

```bash
pytest -q
```

The tests do not call the LLM.

## Recommended workflow

Give the agent explicit constraints, for example:

```text
Inspect the existing implementation first. Add retry handling to the HTTP client. Preserve the public API. Update tests. Run the relevant test suite. Do not change unrelated files.
```

For larger or untrusted tasks, work inside a disposable copy of the repository.

See `CHANGELOG.md` for release history, and `docs/SETUP.md`, `docs/USAGE.md`, `docs/ARCHITECTURE.md`, `docs/TOOLS.md`, `docs/EDITING.md`, `docs/SECURITY.md`, and `docs/ROADMAP.md` for details.
