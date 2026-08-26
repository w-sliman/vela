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
- `/model` — configured model
- `/usage` — session token usage, estimated cost, context fill
- `/compact [focus]` — summarize older turns into one context message; optionally tell it what to focus on. The summarizer itself chooses how many recent turns to keep verbatim.
- `/undo` — revert the workspace to the state before the last agent edit (asks for confirmation; backed by automatic per-edit git checkpoints)
- `/memory` — persistent project memory
- `/history` — recent session events
- `/clear` — wipe the model context entirely (no summary)
- `/quit` — exit

When the conversation approaches the context limit (`CODER_AUTO_COMPACT_PCT` of `CODER_CONTEXT_WINDOW`, 80% by default), the agent compacts automatically using the same mechanism.

## CONTRIBUTING.md

Put project-specific engineering conventions in `CONTRIBUTING.md`, e.g.:

```text
Always run pytest after changing Python files.
Use pathlib.
Do not modify generated files.
```

Workspace content remains untrusted input; it cannot override the agent's global safety policy.
