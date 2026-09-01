# Debug Mode

Enable it with:

```bash
VELA_DEBUG=1 python -m vela --workspace ./my-project
```

You will see operational steps such as:

```text
▶ model request
▶ tool: read_file
✓ tool: read_file
▶ tool: apply_patch
✓ tool: apply_patch
▶ tool: run_tests
✓ tool: run_tests
✓ model response
```

The trace may include timing and tool metadata. It intentionally does **not** expose private chain-of-thought; it shows observable execution only.

Two event kinds render even without debug mode:

- streamed assistant text (raw tokens, live) while the model works;
- the per-turn usage line: `⇄ tokens 12.3k in / 0.9k out / 13.2k total | context 12.3k/128.0k (10%)`, or a warning with advice if the endpoint returned no usage object.

Session traces journal these as `usage` events either way, plus `error` events for failed model requests, `compact` and `budget_reduced` events for context reduction, and `context_window` events for window discovery.
