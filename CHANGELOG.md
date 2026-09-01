# Changelog

This project is **unreleased**: nothing here has been published or tagged, and the
version headings below record development history rather than shipped releases.

## Unreleased

### Live-run fixes
The first end-to-end run against a real model (llama.cpp) found twelve defects that
319 passing unit tests had not. The safety layer held throughout — path, command and
URL containment refused every attempt — but several designs that were correct in
isolation were wrong in practice. Commit messages carry the detail.

- Responses transport encoded bare role items, which strict servers reject; every
  session silently downgraded to Chat Completions after its first assistant reply.
- Reduction had no rung for a single oversized turn. Compaction and dropping both
  reason in user-turns, but one agentic request can fill the window alone. Reduction
  is now a ladder — compact, elide the largest tool result, drop — and the forced
  reduction after a rejection walks it too instead of dropping outright.
- The elision stub told the model to re-run the dropped call, which livelocked: the
  fresh result was the same size and was elided again. It now names cheaper reads.
- A cached probe was read back as a rejection, so `VELA_CONTEXT_WINDOW` was silently
  void from the second session on. The cache records provenance; only a rejection
  outranks configuration.
- Rejection parsing missed llama.cpp's wording, so the highest-authority source never
  fired. It now reads the structured limit the server sends before trying prose.
- The summarizer chose how much history to keep and picked the most aggressive value,
  which left too few turns for compaction to run again. Now `VELA_COMPACT_KEEP_TURNS`
  (default 3), owned by the operator.
- Checkpoints swept in `.vela/`, so `/undo` rewound session traces along with
  the code and corrupted the digests `/resume` reads back.
- Edits were applied but never verified, corrupting three files across three sessions.
  Edits that would newly break a parseable file are refused, schema length limits are
  enforced, `end_line` is required alongside `start_line` (defaulting it read as
  "insert" and duplicated a function body), and edits that cannot check themselves
  against current text now require `expected_hash` rather than merely recommending it.
- Context percentages were measured against the configured window rather than the one
  in force, understating real pressure about twofold.
- Deterministic 4xx rejections are no longer retried before the transport fallback.
- The verify gate is on by default. On one A/B task the model edited a file and
  declared itself done having run nothing; with the gate on it ran the tests.
  `VELA_VERIFY_GATE=0` restores the old behaviour.

### Sub-agent retries and outbound URL containment
- **`delegate_role` was the one model call without the retry policy.** It called the
  provider directly, so a transient blip failed the delegation outright. Backoff moved
  out of `CodingAgent` into `providers.with_retries` — retrying is a property of
  talking to a provider, not of the agent — and both call sites now use it. The
  sub-agent's client is also built on first use, so a session that never delegates
  never opens one.
- **The opt-in network tools had no target restrictions.** Once enabled,
  `browser_fetch`/`browser_open` would reach `169.254.169.254` (cloud metadata) or
  anything on loopback and the LAN. The URL is chosen by the model, and the model reads
  untrusted repository content — the documented prompt-injection exposure — so the
  check belongs in deterministic Python. New `net.py` refuses non-http(s) schemes and
  loopback/private/link-local/reserved/multicast targets, judging *resolved* addresses
  so a public hostname with a private DNS record is still caught, unwrapping
  IPv4-mapped IPv6, and re-checking every redirect hop (`follow_redirects=True`
  previously validated only the URL the model supplied).
- **`github_get` could be re-targeted by its path.** `'https://api.github.com'+path`
  with `//evil.com/x` moves the host — and that request attaches `GITHUB_TOKEN`, so it
  leaks a credential. Paths are now validated before they are appended.
- `VELA_ALLOW_PRIVATE_URLS=1` lifts the restriction for deliberate local use.
- 44 new tests, again weighted toward the negative cases: DNS rebinding, IPv4-mapped
  loopback, redirect-onto-metadata, token-leaking API paths, and no request being made
  at all for a blocked URL.

### The context window is learned, not assumed
- **`VELA_CONTEXT_WINDOW=128000` was an unsafe default.** Point the agent at a 32k
  model and the budget believed it had 128k, so it never reduced and the request was
  rejected anyway. The window is now worked out rather than assumed.
- **Learned from a rejection (primary).** A server refusing an oversized request
  states its real limit; that is parsed, adopted, cached per (endpoint, model) in
  `.vela/windows.json`, and the request retried. Provider-agnostic — it works
  for OpenAI, DeepSeek, Kimi and GLM, none of which expose the window any other way —
  and it costs one failed request once per model rather than once per session.
  A learned limit overrides even an explicit `VELA_CONTEXT_WINDOW`: observation
  outranks configuration, because the server is not wrong about its own ceiling.
- **Probed at startup (secondary)** for the local servers that do report it, unless
  the window was set by hand: vLLM `max_model_len` on `/v1/models`, llama.cpp
  `default_generation_settings.n_ctx` on `/props`, Ollama `num_ctx` from `/api/show`.
  Field names verified against vLLM PR #4643, the llama-server endpoint docs and the
  Ollama API reference. Whichever endpoint answers identifies the backend, so no
  configuration says which to try; a connection failure aborts the sweep instead of
  paying three timeouts to a host that is down.
- **Two deliberate refusals**, both because a wrong window is worse than an unknown
  one: Ollama's `model_info["<arch>.context_length"]` is never used as a fallback (it
  is the model's maximum, not what Ollama serves), and no limit is invented when a
  rejection does not state one — llama.cpp reports overflow with no number, so the
  agent sheds a block and retries rather than guessing.
- Context overflow is handled before transport fallback: an oversized payload is not
  a transport problem, and every transport would reject it identically.
- The resolved window and its source are journaled as `context_window`, shown at
  startup when not simply configured, and reported by `/model`.
- No `VELA_BACKEND`-style knob: the probe that answers already identifies the
  backend, so declaring it in advance would add a way to be wrong without adding
  information.
- 33 new tests, weighted toward the negative cases — an unrelated 401 must not shrink
  the budget, the request size in a rejection must never be mistaken for the limit,
  and junk values from a server must not corrupt the estimate.

### Context budget: one owner for "does this payload fit?"
- **Three mechanisms were guessing independently.** A char-budget `trim` ran every
  turn, a once-per-request auto-compact read the *previous* call's reported usage, and
  `/compact` was manual. None measured the actual outgoing payload against the actual
  limit — so the agent was blind on the first turn of a request, blind on endpoints
  that report no usage, and always one fat tool result behind the truth.
- **Trimming and compaction were never two concerns.** Both answer *reduce the
  conversation to fit*; compaction preserves knowledge, dropping does not. New
  `budget.py` orders them as preference and fallback, so losing history is the
  degraded path rather than the default behaviour of a trimmer running every turn.
  That silent trimming was also what made a long run forget its own earlier attempts.
- **Measurement replaced guessing.** `ContextBudget` sizes the payload the transport
  just encoded — the same object handed to the provider, so there is no gap between
  what was measured and what was sent. Estimates self-calibrate: any server that
  reports usage corrects the chars-per-token ratio; servers that report nothing keep
  the default and still get enforcement.
- **Progress is verified, not assumed.** A summary can be as large as the turns it
  replaced. A reduction that reports success while freeing nothing now falls through
  to the blunter method — without that check the fit loop spins forever, which the
  tests caught.
- **The threshold is derived, not tuned.** `VELA_AUTO_COMPACT_PCT` is gone; the limit
  is the window minus reply headroom (`VELA_REPLY_RESERVE_TOKENS`, default window/8,
  capped at half the window so it can never collapse the limit to zero).
- **`max_turns` removed.** Long tasks are bounded by the context budget and by Ctrl+C,
  which pauses cooperatively. The old cap of 30 raised a `RuntimeError` that discarded
  a request's worth of completed work for no safety the budget does not provide.
- Removed `context.py` and the now-dead knobs `VELA_MAX_TURNS`,
  `VELA_MAX_HISTORY_ITEMS`, `VELA_MAX_HISTORY_CHARS`, `VELA_MAX_CONTEXT_CHARS`,
  `VELA_AUTO_COMPACT_PCT`.
- Tests: new `tests/conftest.py` provides `make_config`, replacing 25 positional
  `Config(...)` constructions whose field order made every schema change a 25-file
  edit. `test_auto_compact.py` became `test_budget.py`.

### Transport abstraction: a canonical conversation model
- **The provider wire format had leaked out of the transport layer.** History stored
  either Responses items (`{'type':'function_call','call_id':…}`) or Chat messages
  (`{'role':'assistant','tool_calls':[…]}`) depending on which transport produced them,
  and five call sites downstream branched on which. New `conversation.py` defines
  provider-neutral `UserMsg` / `AssistantMsg` / `ToolResult`; history holds only these.
- **New `transports.py` is the only place a wire format exists.** `ResponsesTransport`,
  `ChatTransport` and `StreamingChatTransport` each encode canonical items into a
  request and decode the reply back. `api_mode` selects an ordered fallback chain
  instead of mutating a string.
- **Transport fallback is now lossless.** It previously had to discard the whole
  conversation to switch, because the stored items were in the wrong format for the new
  transport — the wipe was load-bearing, not merely cautious. A downgrade now re-encodes
  the same history. It is also journaled (`transport_fallback`), announced in the REPL,
  and reset by `/clear`, so a downgrade is scoped to a conversation rather than to the
  process.
- **`/model` reported the wrong transport.** It printed `config.api_mode` while the
  session could be pinned elsewhere; it now reports the transport actually in use.
- Duplicate branches collapsed to one path each: `context._blocks`, `_repair_partial_turn`,
  `_item_text` (now `conversation.item_text`), `_recent_paths`. `_responses`, `_chat` and
  `_chat_streamed` became a single `_step`/`_send`/`_consume`. Removed `safe_json`,
  `_stream_with_retries` and `context._get`, all dead after the collapse.
- The v0.3 roadmap item "model/provider abstraction" was listed as shipped when
  `providers.py` was a pass-through. It is now true.
- 12 new transport tests; two format-specific interrupt-repair tests became one. Suite
  216 → 228. `llm.py` shrank 461 → 416 lines and every dual-branch site collapsed, but
  the two new modules make production code net larger (+192 lines) — the win is one
  code path per concern and a lossless fallback, not fewer lines.

### Fixes & hardening (code dive, round 2)
- **Command output could lose its tail.** The reader thread was joined for 0.2s after
  the process exited; when the consumer lagged the process — which it does, since the
  REPL prints each line through Rich — the remainder was dropped and the model reasoned
  from a truncated view of its own test output. The join now allows a real grace period,
  and the reader tolerates the pipe closing underneath it.
- **Edit approval asked before validating.** With `VELA_APPROVAL_EDITS=1`, `write_file`
  prompted for a diff and only then checked size, truncation marker, and `expected_hash`
  — so a stale hash cost the user a decision on an edit that could never apply.
  Preconditions moved into `Workspace.preflight_write()` and run first.
- **Model-supplied `run_command` timeouts are clamped** to 1..`VELA_COMMAND_TIMEOUT`.
  They were previously passed through unbounded, so one bad value could hang the REPL.
- **`memory.prune` selected records by value.** `r not in stale` compared whole records:
  O(n²), and two records legitimately holding identical text were both dropped when only
  one should be. Selection is now positional. `consolidate` no longer collapses a merged
  record's date span to `''` when a member lacks a timestamp.
- **`list_files` announces its cap.** The 1000-entry limit was applied silently, so a
  partial listing read as a complete one — the same failure shape as the truncated-read
  bug above. It now returns a `truncated` flag and advice to narrow the query.
- **Dead code removed**: `Git.undo_last()` (superseded by the checkpoint-scoped
  `undo_last_checkpoint()` the CLI actually calls, and covered by its tests), and the
  unused `workspace` parameter on `classify_command()` — classification is purely
  lexical; path containment lives in `ensure_within`.
- 17 new tests, including a deterministic regression guard for the output-drain race
  (it fails against the previous bound rather than depending on machine timing).

### Fixes & hardening (code dive)
- **The package did not parse on Python 3.11** despite `requires-python = ">=3.11"`,
  a "Python 3.11+" README, and Windows setup instructions that say `py -3.11`.
  `llm.py` used a backslash escape inside an f-string expression — legal only from
  3.12 (PEP 701) — making the whole module unimportable on the advertised floor
  version. CI tested 3.12 only, so it never surfaced. Fixed, and CI now runs a
  3.11 + 3.12 matrix.
- **Truncated reads can no longer silently destroy files.** `read_file` bounds content at
  `VELA_MAX_FILE_CHARS` but hashes the *whole* file, so echoing a truncated view back
  through `write_file` passed the stale-hash guard and dropped the tail. `read_file` now
  returns a `truncated` flag plus a warning, and `write_file` refuses any content carrying
  the truncation marker.
- **`apply_patch` accepts blank context lines without a trailing space.** A blank context
  line is canonically `' \n'`, but most producers strip the trailing space; the bare `'\n'`
  form was rejected as an "unsupported patch line", failing patches against any file with
  blank lines in the hunk.
- **Policy: host-sensitive path detection widened** from `/home /root /etc /var /usr /opt`
  to also cover `/proc /sys /boot /dev /mnt /media /srv /run /tmp /lib /bin /sbin` and bare
  `/`. `cat /proc/self/environ` and `ls /` previously classified as allowed read-only
  development commands.
- Auto-compact failures no longer abort the user's request: a failed summarizer call
  degrades to "not compacted" (journaled) and the turn proceeds under ordinary trimming.
- Engineering conventions restructured around the project's invariants; the system
  prompt and docs follow.
- Docs corrected against the code: `SECURITY.md` no longer describes the allow-list as
  "read-only" (it includes `python`/`pytest`, which execute workspace code); `README.md`
  layout and LLM sections rewritten (dual transport, 26 modules, not 9); `EDITING.md` and
  `ARCHITECTURE.md` now state that the transport fallback discards conversation history.
- `.env.example` documents `VELA_APPROVAL_EDITS` and `GITHUB_TOKEN`; the per-turn status
  line no longer prints the context window twice in two formats.
- 13 new tests (truncation guard, patch blank lines, widened policy + no-false-positive
  regression).

### Fixes & hardening
- `/usage` no longer crashes with `NameError` (undefined `win`); the context-fill line renders again.
- Policy: shell commands referencing `..` path segments, `~` expansions, or `$HOME`-style environment variables now require approval, closing a workspace-escape bypass for safe-prefixed commands (`cat ../../etc/passwd`, `cat ~/.ssh/id_rsa`, …).
- `sandbox_run` is now subject to the same policy/approval layer as `run_command` (previously it bypassed both, including the edit-approval gate).
- `/undo` honors the configured approval mode and only reverts agent auto-checkpoints (commit messages starting with `auto: `) when that checkpoint is the latest commit — user commits are never reverted.
- Verify gate now parses tool-result JSON instead of substring-matching, and check-command detection ignores quoted arguments (`git commit -m "test"` no longer counts as a passing check).
- Timed-out shell commands are killed with their whole process group (`start_new_session` + `killpg`), so grandchildren cannot outlive the timeout.
- Streaming chat: mid-stream failures restart the request with the usual backoff, and the latency timer now covers the full stream instead of only stream creation.
- OpenAI client gets a 120s per-request timeout (was the ~10-minute default).
- `ContextManager.trim` no longer re-serializes the whole history on every pop (O(n²) → O(n)).
- `memory.json` writes are atomic (temp file + rename) and read-modify-write cycles take an advisory `flock`, so concurrent access cannot corrupt or lose records.
- `.env` kept mode 600.
- Docs (SECURITY/USAGE/TOOLS/EDITING) updated to match; 13 new tests (policy escapes, sandbox gate, checkpoint-scoped undo, apply_patch denial, diff-truncation preview, `/usage` regression).

## 1.2.0 — 2026-08-26

### Memory: lexical retrieval & per-turn injection
- Project memory upgraded from append-only buckets to versioned records (`id`, `kind`, `text`, `tags`, `paths`, `created`, `last_seen`, `hits`); legacy `memory.json` files migrate automatically on first read.
- Deterministic lexical scorer: field-weighted token overlap (text 1x / tags 2x / path tokens 3x + active-path bonus), IDF weighting, recency decay, usage boost.
- Once per user request the best records are attached to the model payload as an advisory `[project memory]` block — never persisted into history, so trimming/pair integrity and the approval/policy layer are untouched; memory failures are journaled and skipped.
- Injections bump record `hits`/`last_seen` (self-tuning ranking) and journal a `memory_injected` event; `/memory` and `recall_memory` now render readable records.
- Compact-time distillation: the `/compact` summarizer may propose durable facts/decisions/preferences in its JSON reply (`memories` field); proposals are validated, deduped via `add()`, persisted to project memory, journaled as `memory_distilled`, and counted in the compact result (`memories_saved`). Failures never break compaction; `VELA_MEMORY_DISTILL=0` disables.
- `remember` accepts optional `tags`/`paths`; new `forget_memory` tool deletes by id prefix; exact-duplicate `remember` calls refresh instead of duplicating.
- Knobs: `VELA_MEMORY_INJECT`, `VELA_MEMORY_TOPK`, `VELA_MEMORY_MAX_CHARS`, `VELA_MEMORY_MIN_SCORE`, `VELA_MEMORY_DISTILL` (all documented in `.env.example`). 26 new unit tests.

### Memory curation & visibility
- `/memory consolidate [focus]`: the model proposes duplicate/paraphrase groups; deterministic merge keeps the primary id (text/kind replaced, tags/paths unioned, hits summed, dates spanned), drops members, journals `memory_consolidated`. Prose replies are safe no-ops; transport failures propagate like `/compact`.
- Bounded growth: `VELA_MEMORY_MAX_RECORDS` (200) prunes coldest-first on every write; optional `VELA_MEMORY_TTL_DAYS` expiry (off by default).
- The REPL now shows which memory ids were injected after each turn (`memory: r2, r7`).

### Working todo list
- New `write_todos` tool: full-list replacement semantics (no stale ids possible), items `{text, status}` capped at 12×120 chars, statuses `pending/in_progress/done`.
- The queue is rendered live in the REPL on every update, inspectable via `/todos`, summarized as `todos: N/M done` next to turn stats, and re-injected into every model request so it survives pair-aware trimming and auto-compact.
- Deterministic Python validates lists, diffs old-vs-new (completed/reopened/added/removed/in_progress), journals `todos_updated` events to the session trace, and feeds open items into `/resume` digests.
- System prompt encodes the behavioral contract: plan before multi-step work, one in_progress at a time, done-with-evidence immediately, revise-first when instructions change. Knob: `VELA_TODOS=1`.

### Verification gate (experimental)
- `VELA_VERIFY_GATE=1` (off by default): edits set a dirty flag; only recognized checks (`run_tests`, or `run_command` invoking pytest/mypy/ruff/unittest/etc.) clear it when they pass. If the model finishes a request with open todos or unverified edits, deterministic code appends one corrective nudge and lets the loop continue — at most once per request, journaled as `verify_gate`.

### Cooperative pause + /continue
- Ctrl+C during a run no longer discards the turn: KeyboardInterrupt is converted at a safe boundary, `_repair_partial_turn()` closes any dangling tool-call pair with `[interrupted by user]` outputs (chat and responses transports both handled), the pause is journaled as `paused`, and the REPL prints how to resume. `/continue` re-enters the loop with a synthetic nudge; typing anything else continues the same context instead.

### Cross-session resume
- `/sessions [n]`: newest-first index of recorded session traces (id, UTC start, turns, first request), excluding the active session.
- `/resume [ref]`: continue any past session as fresh context. The trace is rebuilt mechanically into a bounded digest (requests, deduplicated touched files, compaction/error counts, last assistant answer) — never raw replay, so history/pair integrity and stale-hash safety hold by construction. Lineage journaled as `resumed_from`.
- Ref grammar: `last`/none = newest; `#N`/1–2 digit = Nth newest; otherwise session-id prefix (unique match required).
- Knob: `VELA_RESUME_MAX_CHARS` (6000); oldest requests drop first when the budget binds. 10 new unit/smoke tests.

## 1.1.0 — 2026-08-26

### Streaming & observability
- Live token streaming for the Chat Completions transport (`VELA_STREAM`, default on); tool-call fragments are reassembled transparently.
- Per-turn token/context status line (in / out / total / context window / %) rendered always; exact `usage` events journaled per model call; `/usage` command with session totals, estimated cost, and average latency.
- Endpoints that return no usage object are counted and flagged with advice — usage is never estimated.

### Context management
- Pair-aware history trimming: assistant tool-call + tool-output pairs (and Responses-API call/output pairs) are never split.
- `/compact [focus]`: LLM summarizes older turns into one context message; the summarizer chooses how many recent turns stay verbatim (clamped 1–5); focus text steers the summary and is persisted in its header. Transport failure leaves history untouched.
- Auto-compact once per request when context crosses `VELA_AUTO_COMPACT_PCT` (default 80%) of `VELA_CONTEXT_WINDOW` (`VELA_AUTO_COMPACT=0` disables).

### Safety & robustness
- Command policy: compound/redirecting commands, inline scripts (`python -c`), `pip install`, destructive `find` now require approval; simple read-only commands remain auto-allowed.
- Subprocess environments scrubbed of secret-shaped variables.
- `VELA_APPROVAL_MODE` (`prompt`/`auto`/`deny`) is actually enforced now.
- Model-request retries with exponential backoff before Responses→Chat fallback.
- Session traces: UTC filenames matching log timestamps; `error` events journaled.

### Editing UX
- Tool arguments validated against schemas (named missing-argument errors).
- Failed anchors list closest matching lines from the file; fuzzy replacement requires `expected_hash`; new line-range replacement mode; hunk-header errors teach the correct format.

### New capabilities
- `/undo` with automatic per-edit git checkpoints (`VELA_AUTO_CHECKPOINT`, default on); workspace git bootstrapped lazily with fallback identity.
- AST-based `search_symbols`: qualified names, kinds, line spans, signatures; regex fallback for unparsable files.

### Hygiene
- CI runs ruff (`F,E9`) alongside tests and the smoke check; unused imports removed; dead code dropped (a duplicate runner class, unused UI hooks wired up instead).
- Docs refreshed across README and all of `docs/`; `.env.example` documents every knob.

## 1.0.x — initial releases

