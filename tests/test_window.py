"""Learning the context window.

No provider reports it portably, so the window is learned — from a rejection
first (ground truth), a local-server probe second, configuration last. The tests
that matter most are the negative ones: a wrong window silently corrupts every
later budget decision, so refusing to learn beats learning something false.
"""
import json

import pytest

from coding_agent.llm import CodingAgent
from coding_agent.session import Session
from coding_agent.window import (
    WindowStore,
    looks_like_overflow,
    parse_limit,
    probe,
    resolve,
)
from tests.conftest import make_config
from tests.test_compact import hist

# Real rejection wording from the servers this targets.
OPENAI = ("Error code: 400 - {'error': {'message': \"This model's maximum context length is 8192 "
          "tokens. However, your messages resulted in 10021 tokens. Please reduce the length of "
          'the messages.", \'code\': \'context_length_exceeded\'}}')
VLLM = ("This model's maximum context length is 4096 tokens. However, you requested 5000 tokens "
        "(4000 in the messages, 1000 in the completion).")
LLAMACPP = 'the request exceeds the available context size. try increasing the context size or enable context shift'
DEEPSEEK = "Error code: 400 - {'error': {'message': 'This model's maximum context length is 65536 tokens.'}}"


# ── parsing a rejection ─────────────────────────────────────────────────────

@pytest.mark.parametrize('text,expected', [
    (OPENAI, 8192),
    (VLLM, 4096),
    (DEEPSEEK, 65536),
])
def test_limit_is_read_from_real_rejections(text, expected):
    assert parse_limit(text) == expected


def test_the_limit_is_taken_not_the_request_size():
    """OPENAI states 8192 *and* 10021; learning 10021 would raise the ceiling
    every time it is hit, which is the worst possible failure here."""
    assert parse_limit(OPENAI) == 8192


@pytest.mark.parametrize('text', [
    'Error code: 401 - invalid api key',
    'Connection refused',
    'Error code: 429 - rate limit exceeded, retry after 20 seconds',
    'Internal server error',
    '',
    None,
])
def test_unrelated_errors_teach_nothing(text):
    assert parse_limit(text) is None


def test_overflow_without_a_number_is_recognised_but_not_guessed():
    """llama.cpp says the context was exceeded without stating the size."""
    assert looks_like_overflow(LLAMACPP) is True
    assert parse_limit(LLAMACPP) is None


@pytest.mark.parametrize('text', [
    "This model's maximum context length is 12 tokens.",
    "This model's maximum context length is 999999999999 tokens.",
])
def test_implausible_limits_are_rejected(text):
    assert parse_limit(text) is None


# ── probing local servers ───────────────────────────────────────────────────

def _fake_http(monkeypatch, responses):
    """Route window._get_json by URL; a missing URL raises like a 404 would."""
    def fake(url, timeout, method='GET', body=None):
        for fragment, payload in responses.items():
            if fragment in url:
                return payload
        raise RuntimeError(f'404 {url}')
    monkeypatch.setattr('coding_agent.window._get_json', fake)


def test_vllm_max_model_len_is_read_for_the_right_model(monkeypatch):
    _fake_http(monkeypatch, {'/models': {'data': [
        {'id': 'other-model', 'max_model_len': 999},
        {'id': 'model-x', 'max_model_len': 32768},
    ]}})
    assert probe('http://localhost:8000/v1', 'model-x') == 32768


def test_llamacpp_props_n_ctx_is_read(monkeypatch):
    _fake_http(monkeypatch, {
        '/models': {'data': [{'id': 'model-x'}]},                  # no max_model_len
        '/props': {'default_generation_settings': {'n_ctx': 65536}, 'total_slots': 4},
    })
    assert probe('http://localhost:8080/v1', 'model-x') == 65536


def test_ollama_uses_num_ctx_from_the_parameters_string(monkeypatch):
    """`parameters` is newline-separated text, not an object."""
    _fake_http(monkeypatch, {
        '/models': {'data': []},
        '/api/show': {'parameters': 'temperature 0.7\nnum_ctx 16384',
                      'model_info': {'llama.context_length': 131072}},
    })
    assert probe('http://localhost:11434/v1', 'llama3') == 16384


def test_ollama_never_falls_back_to_the_model_maximum(monkeypatch):
    """model_info context_length is what the model *could* do, not what Ollama
    serves. Overestimating silently disables reduction — worse than not knowing."""
    _fake_http(monkeypatch, {
        '/models': {'data': []},
        '/api/show': {'parameters': 'temperature 0.7',
                      'model_info': {'llama.context_length': 131072}},
    })
    assert probe('http://localhost:11434/v1', 'llama3') is None


def test_probe_returns_none_when_nothing_answers(monkeypatch):
    _fake_http(monkeypatch, {})
    assert probe('http://localhost:9/v1', 'model-x') is None


def test_probe_survives_a_server_returning_nonsense(monkeypatch):
    _fake_http(monkeypatch, {'/models': {'data': [{'id': 'model-x', 'max_model_len': 'huge'}]}})
    assert probe('http://localhost:8000/v1', 'model-x') is None


def test_probe_is_skipped_without_a_base_url():
    assert probe(None, 'gpt-5') is None


# ── the cache ───────────────────────────────────────────────────────────────

def test_learned_windows_survive_across_sessions(tmp_path):
    store = WindowStore(tmp_path)
    assert store.get('http://x/v1', 'm') is None
    store.remember('http://x/v1', 'm', 8192)
    assert WindowStore(tmp_path).get('http://x/v1', 'm') == 8192


def test_cache_is_keyed_per_endpoint_and_model(tmp_path):
    store = WindowStore(tmp_path)
    store.remember('http://a/v1', 'm', 8192)
    assert store.get('http://b/v1', 'm') is None
    assert store.get('http://a/v1', 'other') is None


def test_corrupt_cache_degrades_to_not_remembering(tmp_path):
    store = WindowStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{ this is not json')
    assert store.get('http://x/v1', 'm') is None
    store.remember('http://x/v1', 'm', 4096)          # must not raise
    assert store.get('http://x/v1', 'm') == 4096


# ── resolution order ────────────────────────────────────────────────────────

def test_a_learned_value_beats_configuration(tmp_path):
    store = WindowStore(tmp_path)
    store.remember('http://localhost:9/v1', 'model-x', 8192)
    cfg = make_config(tmp_path, context_window_tokens=128000, context_window_explicit=True)
    assert resolve(cfg, store) == (8192, 'learned')


def test_explicit_configuration_skips_probing(tmp_path, monkeypatch):
    probed = []
    monkeypatch.setattr('coding_agent.window.probe',
                        lambda *a, **k: probed.append(a) or 4096)
    cfg = make_config(tmp_path, context_window_tokens=64000, context_window_explicit=True)
    assert resolve(cfg, WindowStore(tmp_path)) == (64000, 'configured')
    assert probed == [], 'a stated intention must not be probed over'


def test_probe_is_used_when_the_window_was_not_stated(tmp_path, monkeypatch):
    monkeypatch.setattr('coding_agent.window.probe', lambda *a, **k: 4096)
    cfg = make_config(tmp_path, context_window_tokens=128000, context_window_explicit=False)
    assert resolve(cfg, WindowStore(tmp_path)) == (4096, 'probed')
    assert WindowStore(tmp_path).get(cfg.base_url, cfg.model) == 4096, 'probe result cached'


def test_a_cached_probe_does_not_outrank_explicit_configuration(tmp_path, monkeypatch):
    """The regression this provenance exists for: a probe cached by an earlier
    session used to read back as a rejection, silently voiding CODER_CONTEXT_WINDOW
    from the second run onwards."""
    monkeypatch.setattr('coding_agent.window.probe', lambda *a, **k: 65536)
    loose = make_config(tmp_path, context_window_tokens=128000, context_window_explicit=False)
    assert resolve(loose, WindowStore(tmp_path)) == (65536, 'probed')   # session one caches it

    stated = make_config(tmp_path, context_window_tokens=8000, context_window_explicit=True)
    assert resolve(stated, WindowStore(tmp_path)) == (8000, 'configured')


def test_a_cached_rejection_still_outranks_explicit_configuration(tmp_path):
    store = WindowStore(tmp_path)
    store.remember('http://localhost:9/v1', 'model-x', 8192, 'learned')
    cfg = make_config(tmp_path, context_window_tokens=128000, context_window_explicit=True)
    assert resolve(cfg, store) == (8192, 'learned'), 'the server is never wrong about its ceiling'


def test_a_probe_never_overwrites_a_cached_rejection(tmp_path):
    store = WindowStore(tmp_path)
    store.remember('http://x/v1', 'm', 8192, 'learned')
    store.remember('http://x/v1', 'm', 65536, 'probed')
    assert store.entry('http://x/v1', 'm') == (8192, 'learned')


def test_legacy_bare_integer_entries_are_read_as_rejections(tmp_path):
    """Probing did not cache before provenance existed, so a bare int was a rejection."""
    store = WindowStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({store.key('http://x/v1', 'm'): 8192}))
    assert store.entry('http://x/v1', 'm') == (8192, 'learned')


def test_llamacpp_rejection_yields_the_ceiling_not_the_request_size():
    """The wording that defeated the parser in real use: the limit is the second
    number in the sentence, and the body also names it as `n_ctx`."""
    msg = ("Error code: 400 - {'error': {'code': 400, 'message': 'request (90016 tokens) "
           "exceeds the available context size (65536 tokens), try increasing it', "
           "'type': 'exceed_context_size_error', 'n_prompt_tokens': 90016, 'n_ctx': 65536}}")
    assert looks_like_overflow(msg)
    assert parse_limit(msg) == 65536


def test_the_limit_is_read_from_prose_when_no_field_is_present():
    assert parse_limit('request exceeds the available context size (65536 tokens)') == 65536


# ── the agent learns from a rejection and retries ───────────────────────────

class RejectingProvider:
    """Rejects oversized requests the way OpenAI does, then succeeds."""
    def __init__(self, limit=8192, message=OPENAI):
        self.limit = limit
        self.message = message
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(self.message)
        from types import SimpleNamespace as NS
        return NS(choices=[NS(message=NS(content='ok after learning', tool_calls=None))],
                  usage=NS(prompt_tokens=5, completion_tokens=1, total_tokens=6))


def _agent(tmp_path, provider, **over):
    agent = CodingAgent(make_config(tmp_path, **over), None, Session(tmp_path))
    agent.provider = provider
    return agent


def test_rejection_teaches_the_window_and_the_request_succeeds(tmp_path):
    p = RejectingProvider()
    agent = _agent(tmp_path, p, context_window_tokens=128000)
    assert agent.budget.window == 128000

    result = agent.run('do the thing')

    assert result.text == 'ok after learning'
    assert agent.budget.window == 8192, 'the server ceiling was adopted'
    assert agent.window_source == 'learned'
    assert p.calls == 2, 'rejected once, retried once'


def test_a_learned_window_overrides_explicit_configuration(tmp_path):
    """The server is never wrong about its own ceiling."""
    agent = _agent(tmp_path, RejectingProvider(), context_window_tokens=128000,
                   context_window_explicit=True)
    agent.run('go')
    assert agent.budget.window == 8192


def test_learning_is_cached_for_the_next_session(tmp_path):
    agent = _agent(tmp_path, RejectingProvider(), context_window_tokens=128000)
    agent.run('go')
    assert WindowStore(tmp_path).get(agent.config.base_url, agent.config.model) == 8192


def test_learning_is_journaled(tmp_path):
    agent = _agent(tmp_path, RejectingProvider(), context_window_tokens=128000)
    agent.run('go')
    events = [json.loads(x) for x in agent.session.path.read_text().splitlines()]
    learned = [e for e in events if e['kind'] == 'context_window']
    assert learned and learned[-1]['payload'] == {'tokens': 8192, 'source': 'learned',
                                                  'previous': 128000}


def test_calibration_ratio_survives_relearning_the_window(tmp_path):
    """Rebuilding the budget must not throw away what was measured about tokens."""
    agent = _agent(tmp_path, RejectingProvider(), context_window_tokens=128000)
    agent.budget.chars_per_token = 4.25
    agent.run('go')
    assert agent.budget.chars_per_token == 4.25


def test_an_unrelated_failure_is_not_treated_as_a_window_lesson(tmp_path):
    """A 401 must surface, not silently shrink the budget."""
    class Dead:
        def chat(self, **kw):
            raise RuntimeError('Error code: 401 - invalid api key')

    agent = _agent(tmp_path, Dead(), api_mode='chat', context_window_tokens=128000)
    with pytest.raises(RuntimeError, match='401'):
        agent.run('go')
    assert agent.budget.window == 128000


def test_overflow_without_a_stated_limit_reduces_and_retries(tmp_path):
    """llama.cpp reports overflow with no number; shed a block rather than loop."""
    p = RejectingProvider(message=LLAMACPP)
    agent = _agent(tmp_path, p, context_window_tokens=128000)
    agent.history = hist(4)
    before = len(agent.history)
    result = agent.run('go')
    assert result.text == 'ok after learning'
    assert len(agent.history) < before + 2, 'conversation was reduced, not grown'
    assert agent.budget.window == 128000, 'nothing was invented'
