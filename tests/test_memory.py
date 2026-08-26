import json
from datetime import datetime, timedelta, timezone as tz
from types import SimpleNamespace as NS

from coding_agent.config import Config
from coding_agent.llm import CodingAgent, _recent_paths
from coding_agent.memory import ProjectMemory, select_records, tokenize, render_record
from coding_agent.session import Session

from tests.test_tools import context, dispatch


def rec(id='r1', kind='fact', text='tests live in tests/, run pytest -q', tags=None, paths=None,
        hits=0, seen=None):
    now = seen or datetime.now(tz.utc)
    return {'id': id, 'kind': kind, 'text': text, 'tags': tags or [], 'paths': paths or [],
            'created': now.isoformat(), 'last_seen': now.isoformat(), 'hits': hits}


def cfg(tmp_path, **over):
    return Config('test-key', 'http://localhost:9/v1', 'model-x', 'chat', tmp_path,
                  'prompt', 5000, 30000, 10, 10, 20, 100, 10000, False, False, False,
                  True, False, 0.0, 0.0, 128000, **over)


def make_agent(tmp_path, provider, **over):
    over.setdefault('stream_chat', False)
    agent = CodingAgent(cfg(tmp_path, **over), None, Session(tmp_path))
    agent.provider = provider
    return agent


class FakeProvider:
    def __init__(self, content='ok'):
        self.content = content
        self.calls = []

    def chat(self, **kw):
        self.calls.append(kw)
        msg = NS(content=self.content, tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=10, completion_tokens=5, total_tokens=15))


# --- tokenizer ---

def test_tokenize_keeps_paths_whole_and_split():
    t = tokenize('Fix flaky retries in coding_agent/llm.py')
    assert 'coding_agent/llm.py' in t and {'coding', 'agent', 'llm'} <= set(t)
    assert 'in' not in t and 'fix' in t            # stopwords dropped, lowercased

def test_tokenize_drops_short_and_stop_tokens():
    assert tokenize('The a an of to') == []


# --- store: migration / add / forget / touch ---

def test_legacy_buckets_migrate_to_records(tmp_path):
    p = tmp_path / '.coder-agent' / 'memory.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'facts': [{'text': 'uses pytest', 'timestamp': '2026-01-01T00:00:00+00:00'}],
                             'preferences': ['dense code style']}))
    m = ProjectMemory(tmp_path)
    recs = m.records()
    assert [r['id'] for r in recs] == ['r1', 'r2']
    assert recs[0]['kind'] == 'facts' and recs[0]['text'] == 'uses pytest'
    assert recs[1]['text'] == 'dense code style' and recs[1]['hits'] == 0
    d = json.loads(p.read_text())
    assert d['version'] == 2 and len(d['records']) == 2     # migration persisted

def test_add_assigns_ids_and_dedupes_exact_duplicates(tmp_path):
    m = ProjectMemory(tmp_path)
    a = m.add('preference', 'dense style')
    b = m.add('fact', 'uses pytest')
    assert (a, b) == ('r1', 'r2')
    dup = m.add('preference', 'dense  style')               # whitespace-normalized match
    assert dup == 'r1' and len(m.records()) == 2

def test_forget_removes_by_prefix_and_persists(tmp_path):
    m = ProjectMemory(tmp_path)
    m.add('fact', 'one'); m.add('fact', 'two')
    assert m.forget('r1') == 1 and m.forget('r1') == 0
    assert [r['id'] for r in m.records()] == ['r2']

def test_touch_bumps_hits_and_last_seen(tmp_path):
    m = ProjectMemory(tmp_path); rid = m.add('fact', 'uses pytest')
    before = {r['id']: r for r in m.records()}[rid]
    m.touch([rid]); m.touch([rid])
    after = {r['id']: r for r in m.records()}[rid]
    assert after['hits'] == 2 and after['last_seen'] > before['last_seen']


# --- lexical scoring / selection ---

def test_tag_and_active_path_boost_rank_results():
    now = datetime.now(tz.utc)
    plain = rec('r1', text='the workspace root is ./workspace')
    tagged = rec('r2', text='how we run the suite', tags=['testing'])
    bypath = rec('r3', text='retry logic notes', paths=['coding_agent/llm.py'])
    from coding_agent.memory import score_record, _idf
    records = [plain, tagged, bypath]
    idf = _idf(records)
    qp = {'coding_agent/llm.py', 'coding', 'agent', 'llm', 'py'}
    q = set(tokenize('testing retry logic'))
    scores = {r['id']: score_record(r, q, qp, idf, now) for r in records}
    assert scores['r1'] == 0.0                                # no overlap -> zero
    assert scores['r2'] > 0
    assert scores['r3'] > scores['r2']                        # active-path bonus + text hits

def test_recency_decay_prefers_fresh_records():
    now = datetime.now(tz.utc)
    fresh = rec('r1', text='pytest markers', seen=now)
    stale = rec('r2', text='pytest markers', seen=now - timedelta(days=365))
    from coding_agent.memory import score_record, _idf
    records = [fresh, stale]; idf = _idf(records)
    q = set(tokenize('pytest markers'))
    assert score_record(fresh, q, set(), idf, now) > score_record(stale, q, set(), idf, now)

def test_select_ranks_thresholds_excludes_and_caps():
    now = datetime.now(tz.utc)
    records = [
        rec('r1', text='unrelated storage layout'),
        rec('r2', text='retry backoff notes'),
        rec('r4', text='retry handling for streams'),
        rec('r3', text='our retry handling policy', tags=['retry']),
    ]
    sel = select_records(records, 'retry handling', top_k=3, min_score=0.5, now=now)
    assert [r['id'] for _, r in sel] == ['r3', 'r4', 'r2']   # tag boost > plain > weak single hit
    assert select_records(records, 'quantum chromatography', now=now) == []   # nothing relevant
    top2 = select_records(records, 'retry handling', top_k=2, now=now)
    assert [r['id'] for _, r in top2] == ['r3', 'r4']
    excl = select_records(records, 'retry handling', exclude={'r3'}, now=now)
    assert [r['id'] for _, r in excl] == ['r4', 'r2']

def test_select_char_budget_skips_oversized_records():
    now = datetime.now(tz.utc)
    tiny = [
        rec('r1', text='retry note ' * 120),                  # huge yet relevant
        rec('r2', text='small retry note', tags=['retry']),   # higher score, fits budget
    ]
    sel = select_records(tiny, 'retry', max_chars=100, now=now)
    assert [r['id'] for _, r in sel] == ['r2']

def test_select_ties_break_deterministically_by_id():
    now = datetime.now(tz.utc)
    records = [rec('rb', text='same text'), rec('ra', text='same text')]
    sel = select_records(records, 'same', top_k=2, now=now)
    assert [r['id'] for _, r in sel] == ['ra', 'rb']


# --- config knobs ---

def test_memory_config_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'k'); monkeypatch.setenv('OPENAI_MODEL', 'm')
    c = Config.from_env(str(tmp_path))
    assert (c.memory_inject, c.memory_top_k, c.memory_max_chars, c.memory_min_score) == (True, 4, 1500, 0.5)

def test_memory_config_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'k'); monkeypatch.setenv('OPENAI_MODEL', 'm')
    monkeypatch.setenv('CODER_MEMORY_INJECT', '0'); monkeypatch.setenv('CODER_MEMORY_TOPK', '7')
    monkeypatch.setenv('CODER_MEMORY_MAX_CHARS', '800'); monkeypatch.setenv('CODER_MEMORY_MIN_SCORE', '2.5')
    c = Config.from_env(str(tmp_path))
    assert (c.memory_inject, c.memory_top_k, c.memory_max_chars, c.memory_min_score) == (False, 7, 800, 2.5)


# --- tools ---

def test_remember_and_forget_tools_roundtrip(tmp_path):
    c = context(tmp_path)
    out = dispatch(c, 'remember', {'kind': 'decision', 'text': 'keep PRs small',
                                   'tags': ['workflow'], 'paths': ['docs/']})
    assert 'r1' in out
    assert any(r['tags'] == ['workflow'] and r['paths'] == ['docs/'] for r in ProjectMemory(tmp_path).records())
    assert dispatch(c, 'recall_memory', {}) .count('[r1/decision]') == 1
    removed = json.loads(dispatch(c, 'forget_memory', {'id': 'r1'}))
    assert removed['removed'] == 1 and ProjectMemory(tmp_path).records() == []


# --- per-turn injection ---

def _seed(tmp_path):
    ProjectMemory(tmp_path).add('decision', 'fix failing tests by running pytest -q before finishing any edit')

def test_run_injects_memory_block_into_payload_not_history(tmp_path):
    _seed(tmp_path)
    p = FakeProvider()
    agent = make_agent(tmp_path, p)
    res = agent.run('please fix the failing tests')
    assert isinstance(res.text, str) and len(p.calls) == 1
    msgs = p.calls[0]['messages']
    assert msgs[-1]['role'] == 'user' and '[project memory]' in msgs[-1]['content']
    assert 'pytest -q' in msgs[-1]['content']
    assert all('[project memory]' not in str(m.get('content', '')) for m in agent.history)

def test_injection_records_event_and_bumps_hits(tmp_path):
    _seed(tmp_path)
    agent = make_agent(tmp_path, FakeProvider())
    agent.run('please fix the failing tests')
    traces = list((tmp_path / '.coder-agent' / 'sessions').glob('*.jsonl'))
    events = [json.loads(l) for l in traces[-1].read_text().splitlines()]
    kinds = [e['kind'] for e in events]
    assert 'memory_injected' in kinds
    assert ProjectMemory(tmp_path).records()[0]['hits'] == 1

def test_no_match_injects_nothing(tmp_path):
    ProjectMemory(tmp_path).add('fact', 'deployment uses docker compose on staging')
    p = FakeProvider()
    make_agent(tmp_path, p).run('write a haiku about the sea')
    msgs = p.calls[0]['messages']
    assert all('[project memory]' not in str(m.get('content')) for m in msgs)

def test_injection_disabled_via_config(tmp_path):
    _seed(tmp_path)
    p = FakeProvider()
    make_agent(tmp_path, p, memory_inject=False).run('fix the failing tests')
    msgs = p.calls[0]['messages']
    assert msgs[-1]['role'] == 'user' and '[project memory]' not in msgs[-1]['content']

def test_recent_paths_harvested_from_history_tool_args():
    history = [
        {'role': 'assistant', 'tool_calls': [{'function': {'name': 'read_file',
         'arguments': '{"path": "coding_agent/llm.py"}'}}]},
        {'role': 'assistant', 'tool_calls': [{'function': {'name': 'replace_text',
         'arguments': '{"path":"a/b.py","new":"x"}'}}]},
        {'type': 'function_call', 'call_id': 'c9', 'output': 'ok'},
    ]
    assert _recent_paths(history) == ['coding_agent/llm.py', 'a/b.py']

def test_memory_failure_never_breaks_request(tmp_path, monkeypatch):
    _seed(tmp_path)
    p = FakeProvider()
    agent = make_agent(tmp_path, p)
    monkeypatch.setattr('coding_agent.memory.ProjectMemory.records',
                        lambda self: (_ for _ in ()).throw(RuntimeError('disk gone')))
    agent.run('fix the failing tests')                     # must not raise
    assert len(p.calls) == 1
    assert all('[project memory]' not in str(m.get('content')) for m in p.calls[0]['messages'])


# --- compact-time distillation ---

def test_compact_distills_valid_memories_and_drops_junk(tmp_path):
    from tests.test_compact import hist
    payload = json.dumps({'summary': 's', 'keep_last_turns': 2, 'memories': [
        {'kind': 'decision', 'text': 'ship small PRs', 'tags': ['workflow'], 'paths': ['docs/']},
        {'bad': 'shape'}, {'kind': 'decision'}, 'not-a-dict']})
    p = FakeProvider(payload)
    agent = make_agent(tmp_path, p); agent.history = hist(4)
    info = agent.compact()
    assert info['compacted'] and info['memories_saved'] == 1
    assert len(agent.history) == 7   # summary + last 2 turns x 3 items each
    recs = ProjectMemory(tmp_path).records()
    assert len(recs) == 1 and recs[0]['kind'] == 'decision' and recs[0]['tags'] == ['workflow']
    trace = (tmp_path / '.coder-agent' / 'sessions').glob('*.jsonl')
    assert any(json.loads(l)['kind'] == 'memory_distilled' for f in trace for l in f.read_text().splitlines())

def test_compact_distill_disabled_keeps_memory_untouched(tmp_path):
    from tests.test_compact import hist
    payload = json.dumps({'summary': 's', 'keep_last_turns': 1, 'memories': [{'kind': 'fact', 'text': 'x'}]})
    agent = make_agent(tmp_path, FakeProvider(payload), memory_distill=False); agent.history = hist(4)
    info = agent.compact()
    assert info['memories_saved'] == 0 and ProjectMemory(tmp_path).records() == []

def test_compact_prose_summary_distills_nothing(tmp_path):
    from tests.test_compact import hist
    agent = make_agent(tmp_path, FakeProvider('plain prose summary')); agent.history = hist(4)
    info = agent.compact()
    assert info['compacted'] and info['memories_saved'] == 0 and ProjectMemory(tmp_path).records() == []

def test_compact_distill_dedupes_across_runs(tmp_path):
    from tests.test_compact import hist
    payload = json.dumps({'summary': 's', 'keep_last_turns': 1, 'memories': [{'kind': 'fact', 'text': 'same fact'}]})
    a = make_agent(tmp_path, FakeProvider(payload)); a.history = hist(4); a.compact()
    b = make_agent(tmp_path, FakeProvider(payload)); b.history = hist(4); b.compact()
    recs = ProjectMemory(tmp_path).records()
    assert len(recs) == 1 and recs[0]['text'] == 'same fact'

def test_clean_memories_caps_and_defaults():
    from coding_agent.llm import _clean_memories
    out = _clean_memories([{'text': 't' * 600}, {'kind': 'p', 'text': 'ok'}, {}, None])
    assert [o[1] for o in out] == ['ok'] and out[0][0] == 'p'
    assert _clean_memories('nope') == []


# --- rendering ---

def test_render_record_shape_and_empty_dump():
    r = rec('r7', kind='preference', text='dense style', tags=['style'])
    out = render_record(r)
    assert out.startswith('[r7/preference] dense style') and '(tags: style)' in out

