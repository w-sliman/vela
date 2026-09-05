# SWE-bench runs

Evidence from running Vela against real SWE-bench Verified instances. This is
not a benchmark score and must not be quoted as one: the sample is two
instances from a single repository, chosen because their environments were the
cheapest to build, and one of the two is disqualified.

`results.jsonl` holds one row per run, failures and disqualifications included.

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
