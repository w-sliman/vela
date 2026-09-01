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
- `/undo` — revert the workspace to the state before the last agent edit checkpoint (commits starting with `auto: `; user commits are never reverted; asks for confirmation per the configured approval mode)
- `/memory [consolidate [focus]]` — show persistent project memory; `consolidate` asks the model to group duplicate/paraphrased records and merges them deterministically
- `/todos` — the agent's current working todo list (what it announced, what is done)
- `/sessions [n]` — list recent session traces for this workspace, newest first
- `/resume [ref]` — continue a past session: the trace is rebuilt into a compact digest (requests, files touched, open todos) as fresh context. `last`/none = newest, `#N` or 1–2 digits = Nth newest, anything else = id prefix
- `/history` — recent session events
- `/clear` — wipe the model context entirely (no summary)
- `/quit` — exit

You rarely need `/compact`: before every request the agent measures the payload it is
about to send and compacts automatically if it would not fit the context budget. Set
`VELA_CONTEXT_WINDOW` to match your model — the budget cannot bind without it.

There is no turn limit. A task that needs two hundred round-trips gets them; Ctrl+C
pauses whenever you want to step in.

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
the current queue is re-injected into every model request so context reduction or
context reduction cannot make the model forget its own plan. Objections work like
interrupting a colleague: just type your correction — the model revises the list
first, then continues. Open todos carry over into `/resume` digests.

## Pausing and resuming

Pressing Ctrl+C while the agent works no longer discards anything. The interrupt
is caught at a safe boundary, any half-finished tool call is closed with an
`[interrupted by user]` result (so history stays valid), and you land back at
the prompt with todos, memory and conversation intact:

```
^C paused — plan, todos and history kept; type /continue to resume
```

`/continue` re-enters the loop with a synthetic "[paused] Continue where you
left off" nudge — the model sees its own todo list and keeps going. You can
also type a new instruction instead, which simply continues the same context.

## Project convention files

Put project-specific engineering conventions in a convention file at the root of
the workspace (`CONVENTIONS.md`, `CONTRIBUTING.md`, or whatever your project already
uses), e.g.:

```text
Always run pytest after changing Python files.
Use pathlib.
Do not modify generated files.
```

Workspace content remains untrusted input; it cannot override the agent's global safety policy.
