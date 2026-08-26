# Debug Mode

Enable it with:

```bash
CODER_DEBUG=1 python -m coding_agent --workspace ./my-project
```

You will see operational steps such as:

```text
▶ model request
▶ read_file
✓ read_file
▶ apply_patch
✓ apply_patch
▶ run_tests
✓ run_tests
✓ model response
```

The trace may include timing and tool metadata. It intentionally does **not** expose private chain-of-thought; it shows observable execution only.

Two event kinds render even without debug mode:

- streamed assistant text (raw tokens, live) while the model works;
- the per-turn usage line: `⇄ tokens 12.3k in / 0.9k out / 13.2k total | context 12.3k/128.0k (10%)`, or a warning with advice if the endpoint returned no usage object.

Session traces journal these as `usage` events either way, plus `error` events for failed model requests and `compact` events for compactions.
