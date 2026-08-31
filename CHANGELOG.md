# Changelog

This project is **unreleased**: nothing here has been published or tagged, and the
version headings below record development history rather than shipped releases.

## Unreleased

### Fixes & hardening (code dive, round 2)
- **Command output could lose its tail.** The reader thread was joined for 0.2s after
  the process exited; when the consumer lagged the process — which it does, since the
  REPL prints each line through Rich — the remainder was dropped and the model reasoned
  from a truncated view of its own test output. The join now allows a real grace period,
  and the reader tolerates the pipe closing underneath it.
- **Edit approval asked before validating.** With `CODER_APPROVAL_EDITS=1`, `write_file`
  prompted for a diff and only then checked size, truncation marker, and `expected_hash`
  — so a stale hash cost the user a decision on an edit that could never apply.
  Preconditions moved into `Workspace.preflight_write()` and run first.
- **Model-supplied `run_command` timeouts are clamped** to 1..`CODER_COMMAND_TIMEOUT`.
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
  `CODER_MAX_FILE_CHARS` but hashes the *whole* file, so echoing a truncated view back
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
- `.env.example` documents `CODER_APPROVAL_EDITS` and `GITHUB_TOKEN`; the per-turn status
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
- Compact-time distillation: the `/compact` summarizer may propose durable facts/decisions/preferences in its JSON reply (`memories` field); proposals are validated, deduped via `add()`, persisted to project memory, journaled as `memory_distilled`, and counted in the compact result (`memories_saved`). Failures never break compaction; `CODER_MEMORY_DISTILL=0` disables.
- `remember` accepts optional `tags`/`paths`; new `forget_memory` tool deletes by id prefix; exact-duplicate `remember` calls refresh instead of duplicating.
- Knobs: `CODER_MEMORY_INJECT`, `CODER_MEMORY_TOPK`, `CODER_MEMORY_MAX_CHARS`, `CODER_MEMORY_MIN_SCORE`, `CODER_MEMORY_DISTILL` (all documented in `.env.example`). 26 new unit tests.

### Memory curation & visibility
- `/memory consolidate [focus]`: the model proposes duplicate/paraphrase groups; deterministic merge keeps the primary id (text/kind replaced, tags/paths unioned, hits summed, dates spanned), drops members, journals `memory_consolidated`. Prose replies are safe no-ops; transport failures propagate like `/compact`.
- Bounded growth: `CODER_MEMORY_MAX_RECORDS` (200) prunes coldest-first on every write; optional `CODER_MEMORY_TTL_DAYS` expiry (off by default).
- The REPL now shows which memory ids were injected after each turn (`memory: r2, r7`).

### Working todo list
- New `write_todos` tool: full-list replacement semantics (no stale ids possible), items `{text, status}` capped at 12×120 chars, statuses `pending/in_progress/done`.
- The queue is rendered live in the REPL on every update, inspectable via `/todos`, summarized as `todos: N/M done` next to turn stats, and re-injected into every model request so it survives pair-aware trimming and auto-compact.
- Deterministic Python validates lists, diffs old-vs-new (completed/reopened/added/removed/in_progress), journals `todos_updated` events to the session trace, and feeds open items into `/resume` digests.
- System prompt encodes the behavioral contract: plan before multi-step work, one in_progress at a time, done-with-evidence immediately, revise-first when instructions change. Knob: `CODER_TODOS=1`.

### Verification gate (experimental)
- `CODER_VERIFY_GATE=1` (off by default): edits set a dirty flag; only recognized checks (`run_tests`, or `run_command` invoking pytest/mypy/ruff/unittest/etc.) clear it when they pass. If the model finishes a request with open todos or unverified edits, deterministic code appends one corrective nudge and lets the loop continue — at most once per request, journaled as `verify_gate`.

### Cooperative pause + /continue
- Ctrl+C during a run no longer discards the turn: KeyboardInterrupt is converted at a safe boundary, `_repair_partial_turn()` closes any dangling tool-call pair with `[interrupted by user]` outputs (chat and responses transports both handled), the pause is journaled as `paused`, and the REPL prints how to resume. `/continue` re-enters the loop with a synthetic nudge; typing anything else continues the same context instead.

### Cross-session resume
- `/sessions [n]`: newest-first index of recorded session traces (id, UTC start, turns, first request), excluding the active session.
- `/resume [ref]`: continue any past session as fresh context. The trace is rebuilt mechanically into a bounded digest (requests, deduplicated touched files, compaction/error counts, last assistant answer) — never raw replay, so history/pair integrity and stale-hash safety hold by construction. Lineage journaled as `resumed_from`.
- Ref grammar: `last`/none = newest; `#N`/1–2 digit = Nth newest; otherwise session-id prefix (unique match required).
- Knob: `CODER_RESUME_MAX_CHARS` (6000); oldest requests drop first when the budget binds. 10 new unit/smoke tests.

## 1.1.0 — 2026-08-26

### Streaming & observability
- Live token streaming for the Chat Completions transport (`CODER_STREAM`, default on); tool-call fragments are reassembled transparently.
- Per-turn token/context status line (in / out / total / context window / %) rendered always; exact `usage` events journaled per model call; `/usage` command with session totals, estimated cost, and average latency.
- Endpoints that return no usage object are counted and flagged with advice — usage is never estimated.

### Context management
- Pair-aware history trimming: assistant tool-call + tool-output pairs (and Responses-API call/output pairs) are never split.
- `/compact [focus]`: LLM summarizes older turns into one context message; the summarizer chooses how many recent turns stay verbatim (clamped 1–5); focus text steers the summary and is persisted in its header. Transport failure leaves history untouched.
- Auto-compact once per request when context crosses `CODER_AUTO_COMPACT_PCT` (default 80%) of `CODER_CONTEXT_WINDOW` (`CODER_AUTO_COMPACT=0` disables).

### Safety & robustness
- Command policy: compound/redirecting commands, inline scripts (`python -c`), `pip install`, destructive `find` now require approval; simple read-only commands remain auto-allowed.
- Subprocess environments scrubbed of secret-shaped variables.
- `CODER_APPROVAL_MODE` (`prompt`/`auto`/`deny`) is actually enforced now.
- Model-request retries with exponential backoff before Responses→Chat fallback.
- Session traces: UTC filenames matching log timestamps; `error` events journaled.

### Editing UX
- Tool arguments validated against schemas (named missing-argument errors).
- Failed anchors list closest matching lines from the file; fuzzy replacement requires `expected_hash`; new line-range replacement mode; hunk-header errors teach the correct format.

### New capabilities
- `/undo` with automatic per-edit git checkpoints (`CODER_AUTO_CHECKPOINT`, default on); workspace git bootstrapped lazily with fallback identity.
- AST-based `search_symbols`: qualified names, kinds, line spans, signatures; regex fallback for unparsable files.

### Hygiene
- CI runs ruff (`F,E9`) alongside tests and the smoke check; unused imports removed; dead code dropped (a duplicate runner class, unused UI hooks wired up instead).
- Docs refreshed across README and all of `docs/`; `.env.example` documents every knob.

## 1.0.x — initial releases

See `docs/ROADMAP.md` for the original feature plan.
