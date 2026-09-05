# Vela

A local coding agent built on one principle: **the LLM proposes; deterministic Python
executes and enforces policy.**

The model never touches the filesystem or a subprocess. It emits tool calls; Python
validates the arguments against a schema, applies a command policy, performs the
operation, and returns a structured result — including structured *recovery guidance*
when the operation fails. Safety and correctness properties come from the Python
layer, so they hold regardless of which model you point at it.

That constraint shapes everything else here: a conversation model that belongs to no
provider, so a failing transport costs a retry instead of your context; history
context reduction that can never orphan a tool-call pair; edits that fail closed on a stale hash rather than half-applying, a
`/resume` that rebuilds task state as a digest instead of replaying tool calls, and
token telemetry that reports "unknown" rather than guessing.

It runs entirely from a normal Python `venv` — no Docker required — and gives you a
Rich terminal REPL where the agent inspects a workspace, edits files, applies patches,
runs tests, diagnoses failures, and iterates.

## Status

**Unreleased.** This project has never been published or tagged: there is no release,
no version guarantee, and no license grant yet. Version numbers in `pyproject.toml`
and `CHANGELOG.md` track development history only — treat every interface here as
subject to change without notice.

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

Two safety properties are POSIX-shaped and degrade on Windows: `set -o pipefail`
needs bash, so without it a pipeline reports only its last stage and a failing
test piped into another command looks like it passed; and process-group kill
falls back to killing only the direct child. Vela warns at startup when bash is
missing. Running under WSL or Git Bash keeps both.


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
python -m vela --workspace ./workspace
```

Or install the package in editable mode:

```bash
pip install -e .
vela --workspace ./workspace
```

### Non-interactive

The REPL reads one line per turn, so piping a multi-line request into it becomes
one agent turn per line — the model starts work having seen only the first line.
For scripts and evaluations, pass the whole request as one message and exit:

```bash
vela --workspace ./workspace --prompt "fix the failing test in tests/test_parser.py"
vela --workspace ./workspace --prompt-file task.md
vela --workspace ./workspace --prompt-file -   # all of stdin as one request
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
- `/memory [consolidate [focus]]` — persistent memory; `consolidate` LLM-merges duplicate/paraphrased records
- `/todos` — the agent's current working todo list
- `/sessions [n]` — list recent session traces (id, start, turns, first request)
- `/resume [id|#|last]` — continue a past session as fresh digest context (`#N`/short digits = Nth newest, otherwise id prefix)
- `/history` — recent session events
- `/clear` — clear current model context
- `/quit` — exit

## Observability & context management

- Assistant text streams live as tokens (chat transport; `VELA_STREAM=0` disables).
- Every model call journals an exact `usage` event (input/output tokens) to the session trace; calls without server-reported usage are counted and flagged, never estimated.
- A live status line shows tokens in/out/total plus current context fill after each model turn; `/usage` shows session totals.
- The context window is worked out rather than assumed: probed from local servers
  that report it (vLLM, llama.cpp, Ollama), otherwise learned from the first
  oversized-request rejection and cached per endpoint+model with the source it came
  from. A limit the server states by rejecting a request overrides configuration; a
  probe does not, so setting `VELA_CONTEXT_WINDOW` by hand still means something.
- Before every request the outgoing payload is measured against the context budget
  (that window minus reply headroom) and the conversation is reduced until it fits,
  by a ladder: summarize older turns (`VELA_COMPACT_KEEP_TURNS`, default 3, are kept
  verbatim), then elide the largest tool results — keeping every call/result pair
  intact, so the model is told what it lost and can re-read it — and only then drop
  the oldest turn. The middle rung is what lets a single huge request survive, where
  there are no older turns to summarize. Token estimates self-calibrate from any usage
  the server reports. `VELA_AUTO_COMPACT=0` disables reduction.
- There is no turn limit: long tasks run as long as they need, bounded by the context
  budget and by Ctrl+C.
- Failed model requests retry with exponential backoff (`VELA_REQUEST_RETRIES`, default 2) before falling back from the Responses API to Chat Completions. Deterministic rejections (a malformed request) are not retried — they are re-raised immediately so the fallback happens while it is still cheap.
- Subprocesses run with secret-shaped environment variables (API keys, tokens) removed,
  with the interpreter Vela itself runs under prepended to `PATH` (so a bare `python`
  in a command means this venv), and under `set -o pipefail` where bash is available —
  otherwise `pytest | head` would report the exit status of `head` and a failing suite
  would look like a passing check.
- **The file tools are confined to the workspace; the shell tool is not.** `read_file`
  and the edit tools resolve paths under the workspace root and refuse to escape it.
  `run_command` runs with the workspace as its working directory but has whatever reach
  the invoking user has — the command policy and the approval mode, not a path
  boundary, are what constrain it. Run Vela as a user whose reach you accept, and
  prefer `prompt` approval outside a throwaway workspace.
- Each successful edit auto-commits a git checkpoint in the workspace (`VELA_AUTO_CHECKPOINT=0` disables); `/undo` reverts the last one. Checkpoints cover your work only — the agent's own state under `.vela/` is never committed, so undoing an edit cannot rewrite session traces.
- Edits fail closed: an edit that would newly break a parseable Python file is refused, arguments over the schema's declared limits are refused, and edits that cannot check themselves against the current text (overwriting an existing file, replacing a line range) require the `expected_hash` from `read_file`.
- Relevant project memories are retrieved lexically once per user request and attached as advisory context; the ids used are shown after each turn (`memory: r2, r7`). Disable with `VELA_MEMORY_INJECT=0`; tune via `VELA_MEMORY_TOPK`, `VELA_MEMORY_MAX_CHARS`, `VELA_MEMORY_MIN_SCORE`. `/compact` also distills durable decisions into memory (`VELA_MEMORY_DISTILL=0` disables). See `docs/MEMORY.md`.
- Memory stays curated automatically: writes enforce a record cap (`VELA_MEMORY_MAX_RECORDS`, coldest dropped first) and an optional age limit (`VELA_MEMORY_TTL_DAYS`, off by default); `/memory consolidate [focus]` asks the model to group paraphrased duplicates and merges them deterministically.
- For non-trivial tasks the agent maintains a visible working todo list (`write_todos` tool): announced before work starts, updated live as steps finish, re-injected into every model request so it survives context reduction, and journaled with per-change diffs. Inspect anytime with `/todos`; disable via `VELA_TODOS=0`.
- Verify gate (on by default, `VELA_VERIFY_GATE=0` disables): if the model tries to finish while todos are open or edits were never followed by a passing check, it gets one corrective nudge first. On the same task with the gate off the model edited a file and declared itself done having run nothing; with it on it ran the tests. It costs at most one extra check per request.
- **Ctrl+C pauses instead of destroying**: an interrupt mid-run closes any dangling tool-call pair, journals the pause, and returns you to the prompt with full context intact. `/continue` resumes exactly where it stopped.
- Past sessions can be continued: `/resume` rebuilds task state from any recorded trace as a compact digest (never as raw replay), keeping history/pair integrity intact.

## Evaluation

`evals/swebench/` runs the agent against real SWE-bench Verified instances: a
container holding the repository at its base commit plus Vela itself, egress
allowlisted to the model API, every git ref except the base ancestry destroyed,
and grading against the benchmark's own test lists. Each instance is validated
both ways before it counts — base must fail, gold must pass — and a run whose
trace shows it reached the network is reported as contaminated rather than
scored.

Results, including the instances that could not be validated and the run that
was disqualified, are in `evals/swebench/results.jsonl`. They are evidence that
the harness works end to end, not a benchmark score, and
`evals/swebench/README.md` says why. Nothing here is required to *run* Vela,
which still needs no Docker.

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

Any OpenAI-compatible endpoint works — hosted or local. Two transports are supported
and selected with `OPENAI_API_MODE`:

- **Responses API** with function tools;
- **Chat Completions** with live token streaming (the default in `auto` mode once
  the Responses path is unavailable, and where `VELA_STREAM` applies).

In `auto` mode the agent starts on Responses and falls back to Chat if the transport
rejects a request — the failure mode local inference servers hit when a model emits
malformed tool-call JSON. The fallback is lossless: conversation history is stored as
provider-neutral items, so switching transport re-encodes the same conversation rather
than discarding it. No model name is hard-coded; set `OPENAI_MODEL` to whatever your
account or server offers.

The agent loops over model responses and tool calls until it gets a final response.
Tool results — including failures, with recovery hints — are fed back so the model can
inspect, change, test, and iterate.

## Project layout

```text
vela/
  cli.py         # Rich REPL, slash commands, approval prompts
  llm.py         # controller loop, compaction, retries, verify gate, pause
  conversation.py# canonical, provider-neutral conversation items
  transports.py  # the only place a provider wire format exists
  providers.py   # OpenAI-compatible client wrapper + shared retry/backoff
  tools.py       # tool schemas (single source of truth) + dispatcher
  workspace.py   # path-safe, size-bounded file access
  shell.py       # subprocess execution, timeouts, secret-scrubbed env
  policy.py      # command classification + path containment
  editor.py      # unified diffs, exact/fuzzy/line-range replacement
  search.py      # regex search + AST symbol index
  budget.py      # context budget: measure the payload, reduce until it fits
  window.py      # learn the context window: rejection, probe, then config
  memory.py      # persistent records, lexical scoring, curation
  resume.py      # trace index and digest construction
  telemetry.py   # exact usage extraction, metrics, timers
  session.py     # JSONL session traces
  git.py         # repo bootstrap, per-edit checkpoints, scoped undo
  events.py ui.py            # event bus, rendering
  agents.py json_repair.py   # sub-agent delegation, defensive tool-JSON parsing
  prompts.py config.py       # system prompt, environment config
  net.py         # outbound URL containment for the network tools
  browser.py github.py sandbox.py   # opt-in integrations, off by default

tests/   docs/   smoke/   scripts/   workspace/
```

Conventions and the invariants that must not be broken are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Tests

```bash
pytest -q
```

The tests do not call the LLM.

## Known limitations

Stated plainly, because each of these is a deliberate boundary rather than an
oversight.

- **This is a developer tool, not a sandbox.** Approved shell commands run as your
  local user with your permissions. The workspace bounds *file* access; it does not
  bound what an approved command can do. Point it at a disposable copy of a project,
  not your main checkout, and read what you approve.
- **Prompt-injection resistance is explicitly not a security boundary.** A workspace
  is untrusted input — including any convention files it ships. The deterministic policy layer
  (command classification, path containment, URL judging) is the only real control
  point, which is why those checks live in Python and never in the prompt. A
  sufficiently persuasive repository can influence what the model *asks* for; it
  cannot widen what the executor *allows*.
- **Exercised against one backend.** Development and end-to-end testing ran against
  llama.cpp serving a local model. The transport, context-window and reduction layers
  are written to be provider-neutral and are unit-tested as such, but every defect
  found in live use so far came from one of those layers being wrong about a specific
  server's behaviour. Expect a comparable shake-out on a backend it has not met.
- **Model-dependent quality.** The deterministic layer holds regardless, but how good
  the *work* is tracks the model driving it. A small local model produces more failed
  edits and more retries; the guards turn those into refusals with recovery guidance
  rather than silent corruption, which is the point.
- **Opt-in integrations are lightly exercised.** `VELA_ENABLE_SANDBOX`,
  `VELA_ENABLE_GITHUB` and `browser_open` are off by default and have had far less
  real-world use than the core loop.
- **Unreleased.** Nothing here is published or tagged; the changelog records
  development history rather than shipped versions.

## Recommended workflow

Give the agent explicit constraints, for example:

```text
Inspect the existing implementation first. Add retry handling to the HTTP client. Preserve the public API. Update tests. Run the relevant test suite. Do not change unrelated files.
```

For larger or untrusted tasks, work inside a disposable copy of the repository.

See `CONTRIBUTING.md` for engineering conventions, `CHANGELOG.md` for release history, and `docs/SETUP.md`, `docs/USAGE.md`, `docs/ARCHITECTURE.md`, `docs/TOOLS.md`, `docs/MEMORY.md`, `docs/SECURITY.md` and `docs/DEBUG.md` for details.
