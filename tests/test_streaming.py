from types import SimpleNamespace as NS

from coding_agent.config import Config
from coding_agent.conversation import AssistantMsg
from coding_agent.llm import CodingAgent
from coding_agent.session import Session


def make_agent(tmp_path, streams, **overrides):
    """streams: list of chunk-lists; each agent.run consumes the next one."""
    c = Config('test-key', 'http://localhost:9/v1', 'model-x', 'chat', tmp_path,
               'prompt', 5000, 30000, 10, 10, 20, 100, 10000,
               False, False, False, True, False, 0.0, 0.0, 128000, 2, True, 80,
               True, True)
    agent = CodingAgent(c, None, Session(tmp_path))
    events = []
    agent.events.callback = lambda e: events.append((e.kind, dict(e.data or {})))
    provider = NS(_i=0, _streams=streams)
    def chat_stream(**kw):
        i = provider._i; provider._i += 1
        return iter(streams[min(i, len(streams) - 1)])
    provider.chat_stream = chat_stream
    agent.provider = provider
    return agent, events


def chunk(delta=None, usage=None):
    return NS(choices=[NS(delta=delta)] if delta is not None else [], usage=usage)


def text_chunk(t):
    return chunk(NS(content=t))


def end_usage():
    return chunk(usage=NS(prompt_tokens=5, completion_tokens=2, total_tokens=7))


def test_text_streaming_emits_tokens_and_returns_full_text(tmp_path):
    agent, events = make_agent(tmp_path, [
        [text_chunk('Hel'), text_chunk('lo'), end_usage()],
        [text_chunk('done'), end_usage()],
    ])
    result = agent.run('hi')
    assert result.streamed is True
    kinds = [k for k, _ in events]
    assert kinds.count('token') >= 3                      # two pieces + end marker
    streamed = ''.join(d.get('text', '') for k, d in events if k == 'token' and 'text' in d)
    assert streamed == 'Hello'
    assert any(k == 'usage' and d.get('total') == 7 for k, d in events)
    assert agent.history[-1].text == 'Hello'              # history intact for next call


def test_tool_call_fragments_assemble_and_dispatch(tmp_path):
    import coding_agent.llm as llm_mod
    calls_seen = []
    orig = llm_mod.dispatch
    llm_mod.dispatch = lambda ctx, name, args: calls_seen.append((name, args)) or '{}'
    try:
        agent, events = make_agent(tmp_path, [
            [chunk(NS(tool_calls=[NS(index=0, id='c1', function=NS(name='read_file', arguments='{"pa'))])),
             chunk(NS(tool_calls=[NS(index=0, id=None, function=NS(name=None, arguments='th":"x.py"}'))])),
             end_usage()],
            [end_usage()],                                # final turn: no more calls
        ])
        result = agent.run('read it')
    finally:
        llm_mod.dispatch = orig
    assert result.text == '(no textual response)'
    assert calls_seen == [('read_file', {'path': 'x.py'})]
    assistant = [m for m in agent.history if isinstance(m, AssistantMsg) and m.tool_calls]
    assert assistant and assistant[0].tool_calls[0].id == 'c1'
    assert assistant[0].tool_calls[0].name == 'read_file'


def test_stream_disabled_uses_buffered_path(tmp_path):
    c = Config('test-key', None, 'm', 'chat', tmp_path, 'prompt', 5000, 30000,
               10, 10, 20, 100, 10000, False, False, False, True, False,
               0.0, 0.0, 128000, 2, True, 80, False, True)
    agent = CodingAgent(c, None, Session(tmp_path))
    agent.provider = NS(chat=lambda **kw: NS(choices=[NS(message=NS(content='plain', tool_calls=None))],
                                             usage=None))
    result = agent.run('hi')
    assert result.text == 'plain' and result.streamed is False


def test_stream_without_usage_counts_missing():
    from coding_agent.telemetry import Metrics
    m = Metrics()
    m.add(None, 1.0)
    assert m.missing_usage == 1
