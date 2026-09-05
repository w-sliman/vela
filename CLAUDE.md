# Notes for future sessions

## Open issue: agent state lives inside the workspace

`.vela/` (session traces, memory, todos) is written *inside* the workspace the
agent operates on, so it is part of the agent's own file space: `list_files`
lists it, `search_text` matches it, and `read_file` opens it. This is not
theoretical — during the SWE-bench run on `psf__requests-6028` the agent read
its own session trace (`read_file .vela/sessions/20260904-235330-864058.jsonl`),
re-injecting into context everything that was already in it, from a file that
grows as it is read.

Three costs: context bloat, a self-referential loop (the trace records the act of
reading the trace), and exposure of unrelated prior sessions and memory records
to the current task.

**Decision taken, not yet implemented:** separate the two rather than filtering
`.vela/` out of each tool. Agent state should live outside the workspace root,
keyed by workspace. No migration of existing `.vela/` directories is wanted.

Sketch, to be re-discussed before implementing:
- `Config.state_dir` — `VELA_STATE_DIR`, else `~/.vela/<basename>-<hash of the
  resolved workspace path>`; created with `parents=True` before `Session` opens
  a trace in it.
- `Session(c.workspace)` -> `Session(c.state_dir)` in `cli.py`, and the same for
  the memory store. `/sessions`, `/resume` and `/history` follow automatically.
- Delete the `.vela/`-is-never-committed special-casing in `git.py`; once state
  lives outside the workspace it is dead weight, and removing it proves the
  separation is real.
- Tests that assert traces appear under the workspace should instead assert the
  workspace stays clean.

Open question for that session: state stops being self-contained — moving or
deleting a workspace no longer takes its history with it. Decide whether that is
acceptable or whether the keying should work differently.
