# Usage

The agent supports a simple interactive loop.

### Build something

```text
create a FastAPI app with a health endpoint and pytest tests
```

### Modify something

```text
Inspect the HTTP client first. Add exponential backoff for 429 responses, preserve the public API, update tests, and run them.
```

### Debug something

```text
Run the test suite. Diagnose the first failure and fix it. Then rerun the relevant tests.
```

### Review without modifying

```text
Review this repository for security and correctness issues. Do not modify files.
```

## Slash commands

- `/help` — commands
- `/pwd` — workspace path
- `/tree` — workspace tree
- `/model` — configured model and endpoint
- `/usage` — session token usage, estimated cost, context fill
- `/compact [focus]` — summarize older turns into one context message; optionally tell it what to focus on. The summarizer itself chooses how many recent turns to keep verbatim, and may also distill durable decisions into project memory.
- `/undo` — revert the workspace to the state before the last agent edit (asks for confirmation; backed by automatic per-edit git checkpoints)
- `/memory [consolidate [focus]]` — show persistent project memory; `consolidate` asks the model to group duplicate/paraphrased records and merges them deterministically
- `/todos` — the agent's current working todo list (what it announced, what is done)
- `/sessions [n]` — list recent session traces for this workspace, newest first
- `/resume [ref]` — continue a past session: the trace is rebuilt into a compact digest (requests, files touched, open todos) as fresh context. `last`/none = newest, `#N` or 1–2 digits = Nth newest, anything else = id prefix
- `/history` — recent session events
- `/clear` — wipe the model context entirely (no summary)
- `/quit` — exit

When the conversation approaches the context limit (`CODER_AUTO_COMPACT_PCT` of `CODER_CONTEXT_WINDOW`, 80% by default), the agent compacts automatically using the same mechanism.

## Working todo list

For non-trivial tasks the agent lays out its steps with the `write_todos` tool
*before* starting work. You can watch intent, not just activity:

```text
todos
  ✓ inspect http client
  › add backoff for 429s
  ○ update tests
```

The list updates live as steps finish, each turn reports `todos: N/M done`, and
the current queue is re-injected into every model request so context trimming or
auto-compaction cannot make the model forget its own plan. Objections work like
interrupting a colleague: just type your correction — the model revises the list
first, then continues. Open todos carry over into `/resume` digests.

## CONTRIBUTING.md

Put project-specific engineering conventions in `CONTRIBUTING.md`, e.g.:

```text
Always run pytest after changing Python files.
Use pathlib.
Do not modify generated files.
```

Workspace content remains untrusted input; it cannot override the agent's global safety policy.
