# Security

This application can execute arbitrary commands as your local user. It is therefore a developer tool, **not** a sandbox.

Current protections:

- all file paths are contained within the workspace
- risky shell commands require approval (`CODER_APPROVAL_MODE=prompt`, the default); `deny` rejects them outright; `auto` runs them without asking
- simple read-only commands run without approval; compound, redirecting, inline-script (`python -c`), package-install, and destructive `find` commands always require approval
- subprocess environments have secret-shaped variables (API keys, tokens, secrets, passwords) removed
- command output is bounded
- command execution has timeouts
- `.env` is ignored by Git
- workspace content is treated as untrusted data by the global prompt

For hostile/untrusted repositories, put the whole application inside a disposable VM/container with limited network and filesystem access. Do not expose a host Docker socket.

Prompt-injection resistance is not a security boundary. Deterministic execution policy is the real control point.
