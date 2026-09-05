# SWE-bench runs

Evidence from running Vela against real SWE-bench Verified instances.

**This is not a benchmark score and must not be quoted as one.** The sample is
every `psf/requests` instance in SWE-bench Verified — eight of them, one
repository — chosen because their environments were the cheapest to build. Two
could not be validated and were not run. `results.jsonl` holds one row per run,
including the disqualified ones.

## What happened

| | |
|---|---|
| Instances attempted | 8 (all `psf/requests` in Verified) |
| Not runnable — environment could not be validated | 2 (`1724`, `2317`) |
| Runnable and screened clean | 6 |
| Resolved | 6 of 6 |
| Runs disqualified for looking the answer up | 1 (`6028`, before egress was restricted; superseded by a clean re-run) |

Six for six looks like a strong result and should not be read as one. **These
fixes are public commits that predate the model's training data**, so recall,
not reasoning, may be doing the work — blocking the network stops the agent
fetching the answer, it does not stop the model remembering it. Two observations
cut mildly against pure recall: on `5414` the agent produced a 15-line fix
across two files where gold changes one line in one file, and on `6028` its
clean-run fix was 7 lines against the 15+/12- it produced when it *could* read
upstream. Neither is proof.

The honest reading is that the harness works end to end on real instances, not
that the model is good. Separating the two needs an ablation: the same model and
instances against a one-shot no-tools baseline, where the delta is attributable
to the harness. That has not been run.

Two instances also carry caveats recorded in their `EXCLUSIONS.md`: `1921`, where
5 of 6 target tests already pass at base so only one discriminates, and `1142`,
which has just 5 regression tests and so offers little protection against a fix
that breaks something else.

## What a run involves

1. Fetch the instance (HF datasets-server `/filter`; no `datasets` dependency).
2. Build a container that holds the repository at `base_commit`, its own
   virtualenv, and Vela itself. The agent runs *inside* it — `run_command` has
   no path confinement, so the container is the only real boundary.
3. Validate the instance both ways before trusting anything: base must grade
   `NOT RESOLVED`, gold must grade `RESOLVED`. An instance that fails this is an
   environment bug, not a result.
4. Run the agent on the issue text alone, with `test_patch` withheld.
5. Grade: apply `test_patch`, confirm the agent did not edit `tests/`, then run
   `grade.py`, then `check_integrity.py`.

## Known traps

- The `FAIL_TO_PASS` test does not exist at `base_commit`; it arrives with
  `test_patch`, which is applied only after the agent finishes.
- A repo's `pytest.ini` `addopts` can break explicit node-id matching, so the
  grader passes `-o addopts=`.
- Some dataset ids are truncated because they contain spaces, and some embed
  absolute paths from the image the dataset was built in. `grade.py` parses
  per-test outcomes rather than passing ids, and takes an exclusion list that is
  only ever derived from a gold run.

## The open problem: egress

The model API needs network access, so the agent has it too — and a SWE-bench
instance is a public commit whose fix is a `curl` away. On `psf__requests-6028`
the agent did exactly that. `check_integrity.py` screens every trace and marks
such a run `CONTAMINATED`, but screening is a detector, not a control. Until
egress is restricted to the API endpoint (a filtering proxy on an internal
network), no run here is a clean measurement of problem-solving.

Note that this is a constraint of *evaluating* Vela. It says nothing about
running it, which needs no Docker at all.
