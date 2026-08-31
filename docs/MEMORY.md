# Project Memory

The agent has three different kinds of state:

1. **Workspace** — source files are the authoritative state.
2. **Persistent project memory** — `.coder-agent/memory.json` stores durable facts, decisions, and preferences as records (`id`, `kind`, `text`, `tags`, `paths`, `created`, `last_seen`, `hits`). The `remember` tool writes it (optional `tags`/`paths` sharpen retrieval), `recall_memory` reads all records, and `forget_memory` deletes by id prefix. Exact duplicates update `last_seen` instead of piling up. Legacy bucket-format files are migrated automatically. Memory is advisory; current files and tool results always win.
3. **Session memory** — `.coder-agent/sessions/*.jsonl` records what happened in a run: user requests, tool calls/results, per-model-call exact token `usage`, transport `error`s, `compact`ions, timings, and assistant responses. It is primarily for continuity (`/sessions`, `/resume`), debugging, and after-the-fact review.

The active LLM context is bounded one way: before every request the outgoing payload is measured against the context budget (`CODER_CONTEXT_WINDOW` minus reply headroom) and the conversation is reduced until it fits — summarizing older turns, and dropping the oldest only if summarizing fails. `/compact [focus]` runs the same summarization on demand. See `docs/ARCHITECTURE.md`.

A good mental model is:

**workspace = truth**  
**project memory = durable knowledge**  
**session logs = event history**  
**LLM context = temporary working memory**

## Lexical retrieval & per-turn injection

Once per user request the agent ranks memory records against the request text plus
the workspace paths touched by recent tool calls, and attaches the best matches to
the outgoing model payload as an advisory `[project memory]` block. The block is
never persisted into conversation history, so pair-aware reduction and history
integrity are untouched, and any memory failure is logged and skipped rather than
failing the request.

Scoring is deterministic, dependency-free lexical relevance:

- Query and record tokens are lowercased; path-shaped tokens (`foo/bar.py`) are kept whole *and* split into sub-tokens; a small stopword list is dropped.
- Field weights: text match 1x, tag match 2x, path-token match 3x, plus a flat bonus when the record touches an actively-edited path.
- Matches are IDF-weighted across the corpus; scores decay with recency (~90-day half-life) and grow slightly with usage (`hits`), so injected records self-tune.
- Selection applies a minimum-score threshold, a top-K cap, and a rendered-character budget; an exclusion set exists for callers that need to suppress known ids.

Every injection bumps `hits`/`last_seen` on the chosen records and journals a
`memory_injected` event (ids + size) to the session trace.

Knobs: `CODER_MEMORY_INJECT` (default on), `CODER_MEMORY_TOPK` (4),
`CODER_MEMORY_MAX_CHARS` (1500), `CODER_MEMORY_MIN_SCORE` (0.5).

## Distillation at compact time

When `/compact` (explicit or automatic) summarizes older turns, the summarizer is
also asked to propose durable project knowledge from the dropped turns via an
optional `memories` field in its JSON reply. Proposals are validated defensively
(junk dropped, lengths capped), deduped by the normal `add()` path, persisted to
project memory, and journaled as a `memory_distilled` event; the compact result
reports how many were saved (`memories_saved`). Distillation failures never break
compaction, and it only ever runs after the summary itself succeeded.
`CODER_MEMORY_DISTILL=0` disables.

## Why lexical, not semantic

Semantic (embedding-based) retrieval is deliberately not implemented; the scorer
sits behind one function so it can be swapped later without touching the wiring.

## Cross-session resume

`/sessions [n]` lists recent traces for the workspace (newest first; the active
session's own trace is excluded). `/resume [ref]` continues any of them: the
chosen trace is replayed *mechanically* into a compact digest — user requests,
files touched (deduplicated from recorded tool arguments), compaction/error
counts, and the last assistant message — which becomes the fresh context's
opening message, journaled as `resumed_from` in the new trace.

Deliberately **digest-based, not faithful replay**: tool-call pairs are never
reconstructed, so pair integrity holds by construction regardless of which
transport produced the old trace, and stale file hashes can't leak into editing.
Because durable knowledge lives in project memory (and is injected per turn),
resume only carries task state, keeping digests small.

Ref grammar: `last`/none = newest; `#N` or a 1–2 digit number = Nth newest;
anything else = session-id prefix match. Budget via `CODER_RESUME_MAX_CHARS`
(6000). If a digest would overflow it, oldest requests are dropped first — the
header and newest state always survive.

## Curation & limits

Memory is enforced-bounded so it can't silently bloat:

- **Cap** — `CODER_MEMORY_MAX_RECORDS` (200): every write path (`remember`,
  distillation) prunes back to the cap deterministically — lowest `hits`, then
  oldest `last_seen`, then insertion order, dropped first. Hot, recently used
  memories survive. Selection is by position, never by value: two records may
  legitimately carry identical text, and comparing whole records would drop both.
- **TTL** — `CODER_MEMORY_TTL_DAYS` (0 = off): records untouched longer than
  this expire on the same prune passes.
- **Consolidation** — `/memory consolidate [focus]` shows the model every record
  and asks it to group duplicates/paraphrases (2+ ids per group, canonical
  wording, merged tags/paths). Python performs the merge: the primary id keeps
  its identity, hits are summed, dates spanned (members with no timestamp are
  skipped rather than collapsing the span), members removed; singleton or
  unknown-id groups are ignored. Journaled as `memory_consolidated`. The model
  only ever *proposes groups* — grouping, wording and all writes are validated
  and executed by deterministic code.
- **Visibility** — after each turn the REPL prints which memory ids were
  injected (`memory: r2, r7`), so you always know what the model was reminded
  of.
