# Architecture

The central design principle is: **the LLM proposes; deterministic Python executes and enforces policy.**

```text
                 +------+
                 | User |
                 +--+---+
                    |
                    v
              +-----------+
              | Rich CLI  |
              +-----+-----+
                    |
                    v
              +-----------+
              | LLM loop  |
              +-----+-----+
                    |
             function calls
                    |
                    v
              +-----------+
              |  tools.py |
              +--+-----+--+
                 |     |
                 v     v
            filesystem shell
                    |
                    v
                 policy
                    |
                    v
              local process
```

## Components

- `cli.py`: terminal REPL, slash commands, approval prompts (honors `CODER_APPROVAL_MODE`), live token/context HUD.
- `llm.py`: tool-calling loop over two transports — Responses API and Chat Completions, the latter with live token streaming. Includes request retries with backoff, auto-compact at a context threshold, `/compact` summarization, and transport fallback.
- `providers.py`: thin OpenAI-compatible client wrapper (responses / chat / chat_stream).
- `tools.py`: tool schemas (single source of truth for validation) and deterministic dispatcher; per-edit git checkpoints hook in here.
- `workspace.py`: path-safe filesystem operations (symlink-resolving containment).
- `shell.py`: subprocess execution; merged output stream, timeouts, output limits, secret-scrubbed child environment.
- `policy.py`: command classification (allow-list + compound/inline-exec/install/destructive-find gates) and path containment.
- `editor.py`: unified-diff application, exact/fuzzy/line-range replacement, closest-match error hints.
- `search.py`: regex text search plus AST-based Python symbol index.
- `context.py`: pair-aware history blocks and trimming (tool-call pairs never split).
- `telemetry.py`: exact usage extraction from both API naming conventions, metrics, timers.
- `session.py`: UTC-stamped JSONL session traces (user/tool_call/tool_result/usage/error/compact/assistant events).
- `git.py`: repo bootstrap, per-edit snapshots, undo, status/diff/checkpoint.
- `memory.py`, `events.py`, `ui.py`, `agents.py`, `json_repair.py`, `prompts.py`, `config.py`: persistent memory, event bus, rendering, sub-agent delegation, defensive tool-JSON parsing, system prompt, environment configuration.

## Tool loop

1. User asks for a task.
2. Model receives the request plus tool definitions.
3. Model requests one or more tools.
4. Python dispatches the tool (args validated against schema).
5. Tool result is returned to the model.
6. Model decides whether to continue, fix a failure, or finish.
7. Final text is shown to the user.

The model has no direct filesystem or subprocess primitive outside these tools.

## Context management

History is grouped into atomic blocks (a tool call and its output always move
together). Trimming drops whole leading blocks under char/item budgets;
`/compact` replaces older turns with an LLM-written summary whose retention
window the summarizer itself chooses (clamped 1–5 turns). Auto-compact fires
once per request when the last prompt exceeds `CODER_AUTO_COMPACT_PCT` of
`CODER_CONTEXT_WINDOW`.
