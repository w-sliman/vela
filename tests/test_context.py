from coding_agent.context import ContextManager, _blocks, _orphaned
from coding_agent.conversation import AssistantMsg, ToolCall, ToolResult, UserMsg


def _call(i, results=1):
    """One assistant tool call plus the results answering it."""
    out = [AssistantMsg(tool_calls=[ToolCall(id=f'c{i}', name='read_file', arguments='{}')])]
    out += [ToolResult(call_id=f'c{i}', output='r' * 200) for _ in range(results)]
    return out


def test_trim_keeps_tool_pairs():
    hist = [UserMsg(text='u1')]
    for i in range(20):
        hist += _call(i)
    out = ContextManager(500).trim(hist)
    assert not _orphaned(out)
    assert isinstance(out[0], AssistantMsg)


def test_trim_keeps_multi_result_calls_together():
    """One call answered by several results is still a single atomic block."""
    hist = [UserMsg(text='u')]
    for i in range(15):
        hist += _call(i, results=3)
    out = ContextManager(400).trim(hist)
    assert not _orphaned(out)
    assert isinstance(out[0], AssistantMsg)


def test_blocks_never_split_pairs():
    hist = [UserMsg(text='u'),
            AssistantMsg(tool_calls=[ToolCall(id='a', name='n', arguments='{}')]),
            ToolResult(call_id='a', output='r')]
    assert [len(b) for b in _blocks(hist)] == [1, 2]


def test_blocks_leaves_plain_turns_alone():
    hist = [UserMsg(text='u'), AssistantMsg(text='answer'), UserMsg(text='u2')]
    assert [len(b) for b in _blocks(hist)] == [1, 1, 1]


def test_item_budget_also_drops_whole_blocks():
    hist = [UserMsg(text='u')]
    for i in range(10):
        hist += _call(i)
    out = ContextManager(10_000_000, max_history_items=5).trim(hist)
    assert len(out) <= 5
    assert not _orphaned(out)


def test_small_history_untouched():
    hist = [UserMsg(text='hi')]
    assert ContextManager(10).trim(hist) == hist


def test_orphan_detector_catches_a_broken_history():
    """Guards the guard: _orphaned must actually report an orphan."""
    assert _orphaned([ToolResult(call_id='missing', output='r')]) is True
