# Security

This application can execute arbitrary commands as your local user. It is therefore a developer tool, **not** a sandbox.

Current protections:

- file-tool paths are contained within the workspace (symlinks resolved before the containment check); shell commands that reference `..` path segments, `~` expansions, or `$HOME`-style environment variables require approval
- risky shell commands require approval (`CODER_APPROVAL_MODE=prompt`, the default); `deny` rejects them outright; `auto` runs them without asking
- an allow-list of recognized development commands runs without prompting. Note that
  this list is *not* limited to read-only commands: `python`, `pytest`, `ruff` and
  `mypy` all execute code from the workspace, which is what makes the agent able to
  test its own edits. The allow-list bounds *which* commands run unprompted, not
  whether they have effects
- everything else requires approval: unrecognized commands, compound/redirecting
  commands, inline scripts (`python -c`), package installs, destructive `find`
  operations, and any command referencing a host-sensitive absolute path
  (`/etc`, `/home`, `/proc`, `/sys`, `/dev`, `/tmp`, `/mnt`, … or bare `/`)
- `sandbox_run` commands are subject to the same policy/approval layer as `run_command`
- subprocess environments have secret-shaped variables (API keys, tokens, secrets, passwords) removed
- command output is bounded, and file reads are bounded by `CODER_MAX_FILE_CHARS`.
  A partially-read file cannot be written back as a whole-file rewrite: the write
  path rejects content carrying the truncation marker, since the stale-hash guard
  cannot catch that case (the hash covers the whole file, the content does not)
- command execution has timeouts, and timed-out commands are killed together with
  their whole process group. A timeout supplied by the model is clamped to the
  configured `CODER_COMMAND_TIMEOUT` ceiling
- `.env` is ignored by Git and kept mode 600
- workspace content is treated as untrusted data by the global prompt

For hostile/untrusted repositories, put the whole application inside a disposable VM/container with limited network and filesystem access. Do not expose a host Docker socket.

Prompt-injection resistance is not a security boundary. Deterministic execution policy is the real control point.
