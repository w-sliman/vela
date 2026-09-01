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

- `cli.py`: terminal REPL, slash commands, approval prompts (honors `VELA_APPROVAL_MODE`), live token/context HUD.
- `conversation.py`: the canonical, provider-neutral conversation items
  (`UserMsg`, `AssistantMsg`, `ToolResult`). History holds only these.
- `transports.py`: the **only** place a provider wire format exists. Each transport
  encodes canonical items into a request and decodes the reply back into canonical
  items: `ResponsesTransport`, `ChatTransport`, `StreamingChatTransport`.
- `llm.py`: the controller loop — one `_step()` over whichever transport is active,
  plus request retries with backoff, budget-driven context reduction, `/compact`
  summarization, the verify gate, and interrupt repair.
- `providers.py`: thin OpenAI-compatible client wrapper (responses / chat / chat_stream),
  wrapped by the transports above, plus the shared retry/backoff helper — retrying is a
  property of talking to a provider, so sub-agents get it too.
- `net.py`: outbound URL containment for the opt-in network tools.
- `tools.py`: tool schemas (single source of truth for validation) and deterministic dispatcher; per-edit git checkpoints hook in here.
- `workspace.py`: path-safe filesystem operations (symlink-resolving containment).
- `shell.py`: subprocess execution; merged output stream, timeouts, output limits,
  secret-scrubbed child environment. Output is drained by a reader thread that is
  given a real grace period after the process exits — a short bound truncated the
  tail whenever the consumer (a Rich print per line) lagged the process.
- `policy.py`: command classification (allow-list + compound/inline-exec/install/
  destructive-find/host-path gates) and path containment. Classification is purely
  lexical; *path* containment is enforced separately by `ensure_within`.
- `editor.py`: unified-diff application, exact/fuzzy/line-range replacement, closest-match error hints, and the guard that refuses an edit which would newly break a file the tools can parse.
- `search.py`: regex text search plus AST-based Python symbol index.
- `window.py`: learns the model's context window — rejection first, local-server probe second, configuration last; caches per endpoint+model.
- `budget.py`: the single owner of "does this payload fit?" — token estimation with self-calibration, pair-aware blocks, and the reduction ladder (summarize, elide the largest tool result, then drop).
- `telemetry.py`: exact usage extraction from both API naming conventions, metrics, timers.
- `session.py`: UTC-stamped JSONL session traces (user/tool_call/tool_result/usage/error/compact/assistant events).
- `git.py`: repo bootstrap, per-edit snapshots, undo, status/diff/checkpoint. Snapshots exclude `.vela/`, so undoing an edit cannot rewrite the agent's own session traces.
- `resume.py`: session-trace index, `/resume` ref resolution (index/prefix), and mechanical digest construction from traces.
- `memory.py` curation selects records by position rather than by value, so records
  with identical text are pruned individually rather than as a group.
- `memory.py`, `events.py`, `ui.py`, `agents.py`, `json_repair.py`, `prompts.py`, `config.py`: persistent memory with lexical retrieval/scoring, event bus, rendering, sub-agent delegation, defensive tool-JSON parsing, system prompt, environment configuration.
- `browser.py`, `github.py`, `sandbox.py`: opt-in integrations, all disabled unless
  explicitly enabled by environment (`VELA_ENABLE_BROWSER` / `_GITHUB` / `_SANDBOX`).
  When enabled, the network ones route every target through `net.py` — the URL is
  chosen by the model, and the model reads untrusted repository content.

## Tool loop

1. User asks for a task.
2. Model receives the request plus tool definitions.
3. Model requests one or more tools.
4. Python dispatches the tool (args validated against schema).
5. Tool result is returned to the model.
6. Model decides whether to continue, fix a failure, or finish.
7. Final text is shown to the user.

The model has no direct filesystem or subprocess primitive outside these tools.

## Transports and the canonical conversation

The agent's history holds provider-neutral items. Wire formats — the Responses API's
`function_call` / `function_call_output` items, Chat Completions' `tool_calls` and
`role='tool'` messages — exist only inside `transports.py`, at the moment a request is
encoded or a reply decoded.

That boundary buys three things:

- **Transport failure costs a retry, not the conversation.** `api_mode='auto'` holds an
  ordered chain (`responses` → `chat`); when one fails the next re-encodes the *same*
  history. Local OpenAI-compatible servers that reject a malformed tool call before
  returning a response are the case this exists for. Earlier versions had to discard
  the conversation to switch, because the stored items were in the wrong format.
- **One code path instead of two.** Trimming, compaction, interrupt repair and path
  harvesting each read canonical items, so none of them branches on wire format.
- **A downgrade is scoped to a conversation.** `/clear` restores the configured
  preference; `/model` reports the transport actually in use.

Every fallback is journaled as a `transport_fallback` event and announced in the REPL.

## Advisory context blocks

Two blocks are appended to outgoing model payloads but **never persisted into
history** — so pair-aware reduction and history integrity are untouched by
construction, and either block failing only costs its own presence:

- **Memory recall** (`memory_injected`): lexically selected project memories,
  ranked against the request text and recently touched paths; injections bump
  record usage so future ranking self-tunes.
- **Todo queue** (`todos_updated`): the model's working list via the
  `write_todos` tool — validated and diffed deterministically, rendered live in
  the REPL, journaled to the trace, and carried into `/resume` digests.

Because both survive context reduction while ordinary turns do not, they act
as durable anchors: remembered knowledge across sessions, stated intent across
a long run.

## Context management

One question owns this: **does the payload we are about to send fit?**

`ContextBudget` answers it, and it is the only thing that does. Before every request
the agent encodes the conversation, measures that payload, and reduces until it fits:

```text
payload = transport.encode(history, advisory)
while not budget.fits(payload) and budget.reducible(history):
    reduce(history)            # summarize -> elide a tool result -> drop
    payload = transport.encode(history, advisory)
send(payload)
```

- **Measured, not guessed.** The payload sized is the payload sent — the transport
  encodes once and the same object goes to the provider. The earlier design read the
  *previous* call's reported usage, so it was blind on the first turn, blind on
  endpoints that report no usage, and one fat tool result behind the truth.
- **Self-calibrating.** When a server does report usage, the true token count for a
  payload we measured corrects the chars-per-token ratio. Endpoints that report
  nothing keep the default and still get enforcement.
- **Reduction is a ladder, cheapest first.** Summarize older turns; failing that,
  elide the body of the largest tool result (keeping its call/result pair intact, so
  history stays valid and the model is told what it lost); only then drop the oldest
  block. The middle rung exists because the other two reason in *user turns*, and a
  single agentic turn can fill the window on its own — there is nothing older to
  summarize, and the bulk is in the newest block rather than the oldest. Every
  reduction takes this ladder, including the forced one after a server rejection.
- **Progress is verified, never assumed.** A summary can be as large as the turns it
  replaced. A reduction that reports success but frees nothing is treated as a
  failure and the blunter method runs instead, so the loop cannot spin.
- **The limit is derived, not tuned.** It is the window minus headroom for the reply
  (`VELA_REPLY_RESERVE_TOKENS`, default window/8), rather than a percentage someone
  picked. Headroom is capped at half the window so it can never swallow it.
### Knowing the window

The budget needs the model's context window, and there is no portable way to ask for
it. OpenAI, DeepSeek, Kimi and GLM report nothing useful from `/v1/models`; Anthropic
and Gemini expose it only on native APIs this OpenAI-compatible client never speaks.
Hardcoding a model→window table is the usual workaround and is stale the day it ships.

So the window is learned, from three sources in descending authority:

1. **A rejection.** A server refusing an oversized request states its real limit
   (`"maximum context length is 8192 tokens"`). Parsing that is provider-agnostic,
   costs one failed request once per model, and is ground truth. It overrides even an
   explicit `VELA_CONTEXT_WINDOW` — observation outranks configuration, because the
   server is not wrong about its own ceiling. Cached per (endpoint, model) in
   `.vela/windows.json`, *with the source that produced it*: a probe cached as though
   it were a rejection would outrank the operator's own setting from the second
   session onwards, which is a bug, not a feature.
2. **A probe** of local servers that do report it, tried at startup unless the window
   was set by hand: vLLM's `max_model_len` on `/v1/models`, llama.cpp's
   `default_generation_settings.n_ctx` on `/props`, Ollama's `num_ctx` from
   `/api/show`. Whichever endpoint answers also identifies the backend, so nothing
   needs to be configured to say which to try. A connection failure aborts the sweep
   rather than paying three timeouts against a host that is down.
3. **Configuration**, as the starting assumption.

Two deliberate refusals, both because a wrong window is worse than an unknown one —
it silently disables reduction and the request is rejected anyway:

- Ollama's `model_info["<arch>.context_length"]` is **not** used as a fallback. That
  is the model's maximum, not what Ollama serves; its real default is far smaller.
- A limit is never inferred when the rejection does not state one. The parser reads a
  structured field where the server sends one (llama.cpp's `n_ctx`) and matches the
  common prose phrasings otherwise; when neither yields a number the agent sheds a
  block and retries rather than guessing one.

The resolved window and its source are journaled as a `context_window` event, printed
at startup when it was not simply configured, and shown by `/model`.

`/compact [focus]` invokes the same summarization by hand. How many recent turns stay
verbatim is the operator's setting (`VELA_COMPACT_KEEP_TURNS`, default 3), not the
summarizer's: the model cannot see the token budget, and keeping only one turn leaves
`[summary] + one turn`, which is exactly the size at which compaction refuses to run
again. Every automatic reduction is journaled as a `budget_reduced` event recording
the method, the estimate and the limit.

### No turn cap

There is no `max_turns`. A long task is a long task; the loop is bounded by the
context budget and by Ctrl+C, which pauses cooperatively. The old limit of 30 raised
a `RuntimeError` that discarded a request's worth of work for no safety benefit the
budget does not already provide.
