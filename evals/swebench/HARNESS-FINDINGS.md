# Harness findings from the SWE-bench runs

Failures are classified as *harness* (tooling blocked a capable model) or
*model* (it reasoned badly). Only harness failures are actionable here.

## Fixed during this work

- **Piped prompts became one turn per line.** A 67-line issue arrived as sixteen
  separate requests; the model began work having seen only the title. Fixed by
  `--prompt` / `--prompt-file` (`7672cdd`).
- **A failing check could report success.** `pytest | head` exited 0 and
  satisfied the verify gate. Fixed with `set -o pipefail` (`1daf532`).
- **`run_command` never showed its command.** Fixed (`1daf532`).
- **Children outlived the agent.** A command still running when Vela was
  terminated kept running detached. Fixed with an atexit/SIGTERM reaper
  (`ddab37d`).
- **Child `PATH` displaced the workspace's own interpreter.** Fixed by appending
  rather than prepending (`a2d337c`).

## Open — worth fixing

### H4. Two safety properties degrade silently on Windows
`set -o pipefail` needs bash and `os.killpg` does not exist there, so the
piped-check fix and the process-group kill both quietly stop working on the
PowerShell install path the README documents. Vela now warns at startup when
bash is missing, and the README says so, but the underlying gap is real: on
Windows without bash, a failing test piped into another command still looks
like it passed.

### H1. FIXED — a blocked network was invisible to the model
On the egress-restricted run of `psf__requests-6028` the agent spent **58 of its
90 shell commands** attempting to reach the network, and made **zero edits** in
thirty minutes. It tried `browser_fetch` and `github_get` (both disabled, both
correctly refused), then `urllib`, then `requests`, then plain HTTP to several
hosts, then "raw github http (not https) maybe proxy allows".

Nothing told it the network was unavailable as a *fact about the environment*.
Each attempt failed individually, looking like a transient error worth retrying
with a different technique. The information exists — `VELA_ENABLE_BROWSER=0` is
configuration Vela already holds — but it never reaches the model.

Fixed with both halves: `VELA_SHELL_NETWORK=0` makes the command policy deny
network-reaching commands immediately with a reason that says the environment has
no egress and to work from the repository instead, and the system prompt gains a
note stating the same thing once, up front. The eval runner sets it. Default
stays permissive — `pip install` and `git fetch` are ordinary commands and
denying them by default would protect nothing.

This was the single most expensive harness defect found: it consumed an entire
run's budget.

### H2. The agent can read its own session trace
`.vela/` lives inside the workspace, so `list_files`, `search_text` and
`read_file` all reach it. Recorded in `CLAUDE.md`; the decision is to move agent
state outside the workspace rather than filter each tool.

### H3. PARTLY FIXED — network policy was enforced only on Vela's own tools
`browser_fetch` and `github_get` respect `VELA_ENABLE_*` and the SSRF guard in
`net.py`. `run_command` bypassed all of it — the agent disabled TLS verification
and fetched upstream source directly. The policy can now deny network-reaching
shell commands (`VELA_SHELL_NETWORK=0`), which closes the case that matters.

Still open: the switch is all-or-nothing, so it cannot express "reach PyPI but
not GitHub", and the SSRF guard's per-URL reasoning still does not apply to the
shell. A denylist over command text is also weaker than a network boundary — the
egress proxy, not this, is what actually holds.

## Not harness defects (recorded so they are not re-investigated)

- The laptop freeze during the first containerless run: the journal shows no OOM
  kill, and CPU starvation began roughly two hours *after* the agent's 30-minute
  timeout would have ended it. Host-side memory pressure, not the agent.
- Contamination on the first `psf__requests-6028` run: a property of the
  evaluation setup (unrestricted egress, full clone), now fixed by the proxy and
  by destroying every ref except the base commit's ancestry.
