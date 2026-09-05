# Vela trial — hard-task result and defects found

## The task
A planted-bug `expr` package (tokenizer / shunting-yard parser / evaluator /
188-line function registry) plus a `SPEC.md` of six behaviour rules the code
violates. Chosen to be genuinely hard, not a one-liner hunt:

- `parser.py` shipped **two divergent copies** of the shunting-yard loop, and the
  sub-expression copy was strictly weaker. Rule 5 (unary minus inside call
  arguments) is unsatisfiable without unifying them — a patch-in-place fix cannot
  pass.
- Unary operators had to be added to a shunting-yard parser with correct binding
  (`-2^2 == -4` but `2^-1 == 0.5`), which is the classic place people get it wrong.
- One-line semantic bug buried in a 188-line generated registry (`hypot` returning
  `a + b`).
- Error-position plumbing (`ExprError.position` must be the operator's offset).

Grading: 8 visible tests (green at baseline, so regressions show) + a **hidden**
17-test suite the agent never saw. Baseline hidden score: **4/17**.

## Result: 17/17 hidden, 8/8 visible, in one pass, no human turns

Three edits, all correct, no test-gaming:

| Edit | Tool chosen | Change |
|---|---|---|
| `expr/functions.py` | `replace_text` | `hypot` → `math.hypot(a, b)` (2 lines in 188) |
| `expr/evaluator.py` | `replace_text` | `ExprError("division by zero", node.position)` for `/` and `%` |
| `expr/parser.py` | `write_file` | 148 lines: deduplicated the two loops, added `UNARY_PRECEDENCE` |

The parser rewrite is the real result. It did the restructure rather than
patching around it: `_sub`/`_parse_tokens` now delegate to the single `parse`
implementation, and associativity is the textbook condition
`top_prec > prec or (top_prec == prec and not right_assoc)`. Tool selection was
sensible — surgical `replace_text` for the two localised fixes, whole-file
`write_file` only for the restructure that touched most of the file.

Auto-checkpointing worked: three `auto: <tool> <path>` commits, one per edit,
`.vela/` correctly excluded. `git diff HEAD` therefore shows nothing — the edits
are *in* the history, which surprised me and is worth a line in the docs.

## Defects

### D1 — The verify gate accepts any exit-0 command, and pipelines defeat it
`llm.py:286`: `if payload.get('returncode')==0: self._edited_since_check=False`.
"A passing check" is defined as *any* `run_command` returning 0.

Two consequences, both confirmed by probe against a deliberately failing suite:

```
rc=1   holds gate     python -m pytest -q
rc=0   CLEARS GATE    python -m pytest -q 2>&1 | head -n 100
rc=0   CLEARS GATE    echo hi
rc=1   holds gate     python -m pytest -q > out.txt
```

- `echo hi` satisfies the gate. Nothing requires the command to be a test or build.
- A pipeline reports the **last stage's** status, so `| head` turns a failing suite
  into a pass. This is not hypothetical: in this very run the model chose
  `python -m pytest -q 2>&1 | head -n 100` unprompted, to bound output. Truncating
  long test output is exactly what a well-behaved agent does, and doing so silently
  disarms the gate.

This matters because the gate is a headline feature: the README sells it as the
thing that stopped the model declaring itself done without running anything. As
written it can be cleared by a command that ran nothing, or by a test run that
failed. Suggested fix: track the returncode of the *pipeline* (`set -o pipefail`
for the sh invocation) and require the cleared-by command to have been a check,
not merely exit 0.

### D2 — The terminal never shows what `run_command` ran
The streaming line prints `• tool arguments: run_command` with no body, and the
result panel carries `status / returncode / stdout / stderr / policy` — no command
string. So in `approval_mode=auto` you watch a shell tool execute without ever
seeing the command. The session trace *does* record it in full
(`payload.arguments_raw`), so this is a display gap, not a data loss — but it is
the one tool where the live view matters most.

### D3 — `run_command` is not confined to the workspace, and that asymmetry is undocumented
`head -1 /etc/passwd` and `ls /etc/hostname` both succeed from a run rooted in a
`/tmp` workspace. File tools are workspace-relative and fail closed; the shell tool
is not path-constrained at all — the command policy is the only control. That may
well be the intended design (it is what makes the agent useful), but the README's
safety section details edit fail-closed behaviour and secret-scrubbed environments
without saying that shell reach is unrestricted. Worth one explicit sentence, since
a reviewer will otherwise assume the file-tool confinement extends to the shell.

### D4 (minor) — child processes do not inherit Vela's own interpreter
Vela runs from `.venv/bin/python` but spawns subprocesses via `/bin/sh` without
putting that venv's `bin` on the child `PATH`. Whether `python -m pytest` works
therefore depends on ambient PATH rather than on the venv Vela was launched from.
In one launch context here `python` did not resolve at all and the agent spent
five tool calls hunting for a usable interpreter before it could test anything.
Putting `Path(sys.executable).parent` on the child PATH would make the README's own
`python -m pytest` instruction reliable.

## Retracted during testing
An earlier reading of the log showed `returncode: 0` alongside `python: not found`
and looked like exit statuses being swallowed. A direct probe disproved it —
`exit 7` → 7, `false` → 1. The zeros were the model's own compound/piped commands,
which is what led to D1 instead.
