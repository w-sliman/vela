from coding_agent.context import ContextManager, _blocks


def _orphaned(history):
    """Return True if any tool message lost its assistant tool_calls parent."""
    seen_calls = set()
    for m in history:
        if m.get('role') == 'assistant' and m.get('tool_calls'):
            seen_calls.update(c['id'] for c in m['tool_calls'])
        elif m.get('role') == 'tool':
            if m.get('tool_call_id') not in seen_calls:
                return True
    return False


def test_trim_keeps_chat_tool_pairs():
    hist = [{'role': 'user', 'content': 'u1'}]
    for i in range(20):
        hist.append({'role': 'assistant', 'content': '',
                     'tool_calls': [{'id': f'c{i}'}]})
        hist.append({'role': 'tool', 'tool_call_id': f'c{i}', 'content': 'r' * 200})
    out = ContextManager(500).trim(hist)
    assert not _orphaned(out)
    assert out[0]['role'] == 'assistant'


def test_trim_keeps_responses_call_output_pairs():
    hist = []
    for i in range(20):
        hist.append({'type': 'function_call', 'call_id': f'x{i}'})
        hist.append({'type': 'function_call_output', 'call_id': f'x{i}', 'output': 'o' * 200})
    out = ContextManager(300).trim(hist)
    calls = {m['call_id'] for m in out if m['type'] == 'function_call'}
    outputs = {m['call_id'] for m in out if m['type'] == 'function_call_output'}
    assert calls == outputs


def test_blocks_never_split_pairs():
    hist = [{'role': 'user', 'content': 'u'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'a'}]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'r'}]
    blocks = _blocks(hist)
    assert [len(b) for b in blocks] == [1, 2]


def test_small_history_untouched():
    hist = [{'role': 'user', 'content': 'hi'}]
    assert ContextManager(10).trim(hist) == hist
