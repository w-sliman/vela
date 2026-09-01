"""Transports are the only place a wire format exists.

The payoff test is `test_fallback_preserves_the_conversation`: because history holds
canonical items, a transport downgrade re-encodes the same conversation instead of
discarding it. That was impossible while history stored one provider's wire format.
"""
import json
from types import SimpleNamespace as NS

import pytest

from tests.conftest import make_config
from coding_agent.conversation import AssistantMsg, ToolCall, ToolResult, UserMsg
from coding_agent.llm import CodingAgent
from coding_agent.session import Session
from coding_agent.transports import ChatTransport, ResponsesTransport, build

HISTORY = [
    UserMsg(text='fix the parser'),
    AssistantMsg(text='looking', tool_calls=[ToolCall(id='c1', name='read_file',
                                                      arguments='{"path":"p.py"}')]),
    ToolResult(call_id='c1', output='{"content":"..."}', name='read_file'),
    AssistantMsg(text='done'),
]


def cfg(tmp_path, mode='auto', **over):
    over.setdefault('stream_chat', False)
    over.setdefault('request_retries', 0)   # keep the fallback tests fast
    return make_config(tmp_path, api_mode=mode, **over)


# ── encoding: the same conversation, two wire formats ───────────────────────

def test_chat_encoding_uses_tool_calls_and_tool_role():
    msgs = ChatTransport(None, 'm', 'SYS').encode(HISTORY)
    assert msgs[0] == {'role': 'system', 'content': 'SYS'}
    assistant = next(m for m in msgs if m['role'] == 'assistant' and m.get('tool_calls'))
    assert assistant['tool_calls'][0]['id'] == 'c1'
    assert assistant['tool_calls'][0]['function']['name'] == 'read_file'
    answer = next(m for m in msgs if m['role'] == 'tool')
    assert answer['tool_call_id'] == 'c1'


def test_responses_encoding_uses_function_call_items_and_no_system_message():
    items = ResponsesTransport(None, 'm', 'SYS').encode(HISTORY)
    assert all(i.get('role') != 'system' for i in items)   # travels as `instructions`
    call = next(i for i in items if i.get('type') == 'function_call')
    assert call['call_id'] == 'c1' and call['name'] == 'read_file'
    out = next(i for i in items if i.get('type') == 'function_call_output')
    assert out['call_id'] == 'c1'


def test_responses_role_items_declare_their_type():
    """A bare role item is rejected by strict Responses servers ("Cannot determine
    type of 'item'"), which silently costs the richer transport for a whole session."""
    items = ResponsesTransport(None, 'm', 'SYS').encode(HISTORY)
    roles = [i for i in items if 'role' in i]
    assert roles and all(i.get('type') == 'message' for i in roles)


def test_both_encodings_carry_the_same_conversation():
    """Neither format may drop a turn — that is what makes a swap lossless."""
    chat = ChatTransport(None, 'm', 'SYS').encode(HISTORY)
    responses = ResponsesTransport(None, 'm', 'SYS').encode(HISTORY)
    for blob in (json.dumps(chat), json.dumps(responses)):
        assert 'fix the parser' in blob and 'p.py' in blob and 'done' in blob


def test_advisory_blocks_are_appended_but_not_in_history():
    msgs = ChatTransport(None, 'm', 'SYS').encode(HISTORY, advisory=['[project memory] x'])
    assert msgs[-1] == {'role': 'user', 'content': '[project memory] x'}
    assert len(HISTORY) == 4, 'encoding must not mutate history'


# ── fallback order ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('mode,names', [
    ('auto', ['responses', 'chat']),
    ('responses', ['responses']),
    ('chat', ['chat']),
])
def test_build_returns_the_configured_fallback_chain(mode, names):
    assert [t.name for t in build(mode, None, 'm', 'SYS', stream=False)] == names


def test_streaming_selected_only_when_configured():
    assert build('chat', None, 'm', 'SYS', stream=True)[0].name == 'chat+stream'
    assert build('chat', None, 'm', 'SYS', stream=False)[0].name == 'chat'


# ── the payoff: a downgrade keeps the conversation ──────────────────────────

class FlakyProvider:
    """Responses always fails; chat succeeds and records what it received."""
    def __init__(self):
        self.chat_payloads = []

    def responses(self, **kw):
        raise RuntimeError('server rejected the tool call')

    def chat(self, **kw):
        self.chat_payloads.append(kw['messages'])
        return NS(choices=[NS(message=NS(content='recovered', tool_calls=None))],
                  usage=NS(prompt_tokens=5, completion_tokens=1, total_tokens=6))


def test_fallback_preserves_the_conversation(tmp_path):
    p = FlakyProvider()
    agent = CodingAgent(cfg(tmp_path), None, Session(tmp_path))
    agent.provider = p
    agent.history = list(HISTORY)

    result = agent.run('and now finish it')

    assert result.text == 'recovered'
    assert agent.transport.name == 'chat'
    # The earlier conversation reached the new transport rather than being wiped.
    sent = json.dumps(p.chat_payloads[-1])
    assert 'fix the parser' in sent and 'p.py' in sent
    assert 'and now finish it' in sent
    assert any(isinstance(h, ToolResult) for h in agent.history), 'tool history kept'


def test_fallback_is_journaled_and_announced(tmp_path):
    p = FlakyProvider()
    agent = CodingAgent(cfg(tmp_path), None, Session(tmp_path))
    events = []
    agent.events.callback = lambda e: events.append((e.kind, e.message))
    agent.provider = p
    agent.run('go')
    kinds = [json.loads(x)['kind'] for x in agent.session.path.read_text().splitlines()]
    assert 'transport_fallback' in kinds
    assert any('transport:' in msg for _, msg in events)


def test_exhausted_transports_raise_instead_of_looping(tmp_path):
    """With no transport left, the error surfaces rather than being swallowed."""
    class Dead:
        def chat(self, **kw):
            raise RuntimeError('auth failed')

    agent = CodingAgent(cfg(tmp_path, mode='chat'), None, Session(tmp_path))
    agent.provider = Dead()
    with pytest.raises(RuntimeError, match='auth failed'):
        agent.run('go')


def test_clear_restores_the_preferred_transport(tmp_path):
    """A downgrade is scoped to a conversation, not to the process."""
    agent = CodingAgent(cfg(tmp_path), None, Session(tmp_path))
    agent.provider = FlakyProvider()
    agent.run('go')
    assert agent.transport.name == 'chat'
    agent.clear()
    assert agent.transport.name == 'responses'
    assert agent.mode == 'responses'
