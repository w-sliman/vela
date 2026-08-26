# Tools

All tools are dispatched deterministically in `tools.py`. Required arguments are
validated against each tool's schema before execution, so missing arguments
produce a named error (e.g. `missing required argument(s): path`) instead of a
traceback. Edit tools return structured recovery guidance on failure.

## Files & editing

- **list_files** — workspace tree; `.git/` is hidden from listings.
- **read_file** — UTF-8 content plus SHA-256; pass that hash as `expected_hash` when editing.
- **write_file** — create or replace a file. Optional `expected_hash` guards against concurrent modification (including deletion-since-read). Auto-checkpoints on success.
- **replace_text** — two modes:
  - *anchor mode*: exact `old` → `new` replacement with optional `occurrence`;
  - *line mode*: `start_line`..`end_line` (1-based, inclusive) replaced verbatim by `new`.
  - `fuzzy=True` allows near-miss anchors but **requires `expected_hash`**. Failed anchors return the closest matching lines from the current file so the model can self-correct in one retry.
- **apply_patch** — single-file unified diff with strict context validation. Malformed hunk headers return the correct `@@ -a,b +c,d @@` format plus advice to switch tools.
- **make_directory**

Every successful edit auto-commits a git checkpoint (`CODER_AUTO_CHECKPOINT=0`
disables); `/undo` reverts the most recent one.

## Search

- **search_text** — regex search across workspace text files.
- **search_symbols** — AST-based Python symbol index: qualified names (`Greeter.method_one`), kind (`def`/`async def`/`class`), full line span, and real signature. Files that fail to parse fall back to a line-regex scan (signature `(?)`) rather than disappearing.

## Execution

- **run_command** — shell command classified by `policy.py` into allow / approve / deny. Compound, redirecting, inline-script (`python -c`), package-install, and destructive-`find` commands always require approval. Child processes inherit a scrubbed environment (API keys/tokens/secrets removed).
- **run_tests** — pytest selection inferred from changed paths, or an explicit command.
- **sandbox_run** — opt-in no-network Docker container (`CODER_ENABLE_SANDBOX=1`). Off by default.

## Git

- **git_status**, **git_diff**, **git_checkpoint** (checkpoint always asks for approval unless approval mode says otherwise).

## Memory, delegation, integrations

- **remember** / **recall_memory** — persistent project memory (`.coder-agent/memory.json`).
- **delegate_role** — isolated planner/reviewer sub-agent; advisory only, cannot edit files.
- **browser_fetch** / **browser_open** / **github_get** — disabled until explicitly enabled via environment.
