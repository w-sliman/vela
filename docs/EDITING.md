# Robust Editing

Editing follows **read -> fingerprint -> edit -> verify -> recover**.

- `read_file` returns the file and SHA-256.
- `expected_hash` prevents stale writes — including writes to files deleted since the read. Fuzzy matching *requires* it.
- Exact replacements fail closed when the target is missing, and the failure lists the closest matching lines from the current file so the model can correct its anchor in one retry.
- `replace_text` also accepts a line-range mode (`start_line`/`end_line`, 1-based inclusive) taken from a fresh read, which avoids anchor recall entirely.
- Unified patches validate context before changing the file; malformed hunk headers return the expected `@@` format and advice to switch to `replace_text`.
- Tool arguments are validated against each tool's schema (missing arguments produce named errors), and parsed defensively for common transport-level JSON failures.
- Write preconditions are validated before the optional edit-approval prompt
  (`CODER_APPROVAL_EDITS=1`), so a stale hash is reported without first asking you to
  approve a diff that was never going to apply.
- Every successful edit auto-commits a git checkpoint (commit messages start with `auto: `); `/undo` reverts the last one, but never touches user commits.
- Large HTML/CSS/JS edits are deliberately bounded; use patches or several smaller writes.
- Reads are bounded too. A file over `CODER_MAX_FILE_CHARS` is returned truncated and
  flagged, and writing that truncated view back is refused outright — the full-file
  hash would otherwise match while the content silently lost its tail.

Important limitation: if a local inference server itself rejects malformed tool-call JSON before returning an API response, the client cannot repair the already-rejected call. v1.x therefore catches that failure, switches to the compatible chat path in `auto`
mode, and sends a corrective instruction telling the model to re-read and use smaller
patches. Note the cost of that recovery: the conversation is restarted from the
current user request plus the corrective instruction, so earlier turns in the
conversation are dropped — durable knowledge survives only via project memory, which
is re-injected per request. The transport stays on `chat` for the rest of the session.
The model remains responsible for producing the next valid action.
