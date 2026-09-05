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

### H1. A blocked network is invisible to the model, and it will not stop trying
On the egress-restricted run of `psf__requests-6028` the agent spent **58 of its
90 shell commands** attempting to reach the network, and made **zero edits** in
thirty minutes. It tried `browser_fetch` and `github_get` (both disabled, both
correctly refused), then `urllib`, then `requests`, then plain HTTP to several
hosts, then "raw github http (not https) maybe proxy allows".

Nothing told it the network was unavailable as a *fact about the environment*.
Each attempt failed individually, looking like a transient error worth retrying
with a different technique. The information exists — `VELA_ENABLE_BROWSER=0` is
configuration Vela already holds — but it never reaches the model.

Two candidate fixes, not exclusive: state network availability in the system
prompt when the network tools are disabled, and have the command policy fail
network-reaching shell commands fast with a message that says the environment
has no egress rather than surfacing a raw DNS error.

This is the single most expensive harness defect found so far: it consumed an
entire run's budget.

### H2. The agent can read its own session trace
`.vela/` lives inside the workspace, so `list_files`, `search_text` and
`read_file` all reach it. Recorded in `CLAUDE.md`; the decision is to move agent
state outside the workspace rather than filter each tool.

### H3. Network policy is enforced only on Vela's own tools
`browser_fetch` and `github_get` respect `VELA_ENABLE_*` and the SSRF guard in
`net.py`. `run_command` bypasses all of it — the agent disabled TLS verification
and fetched upstream source directly. Related to the documented fact that the
shell tool has no path confinement either: the policy layer covers the tools
that are easy to cover, not the one that matters.

## Not harness defects (recorded so they are not re-investigated)

- The laptop freeze during the first containerless run: the journal shows no OOM
  kill, and CPU starvation began roughly two hours *after* the agent's 30-minute
  timeout would have ended it. Host-side memory pressure, not the agent.
- Contamination on the first `psf__requests-6028` run: a property of the
  evaluation setup (unrestricted egress, full clone), now fixed by the proxy and
  by destroying every ref except the base commit's ancestry.
