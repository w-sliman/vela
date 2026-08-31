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
    def __init__(self, content='ok', error=None):
        self.content = content
        self.error = error
        self.calls = []

    def chat(self, **kw):
        if self.error:
            raise self.error
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
    assert (c.memory_distill, c.memory_max_records, c.memory_ttl_days) == (True, 200, 0)

def test_memory_config_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'k'); monkeypatch.setenv('OPENAI_MODEL', 'm')
    monkeypatch.setenv('CODER_MEMORY_INJECT', '0'); monkeypatch.setenv('CODER_MEMORY_TOPK', '7')
    monkeypatch.setenv('CODER_MEMORY_MAX_CHARS', '800'); monkeypatch.setenv('CODER_MEMORY_MIN_SCORE', '2.5')
    monkeypatch.setenv('CODER_MEMORY_MAX_RECORDS', '25'); monkeypatch.setenv('CODER_MEMORY_TTL_DAYS', '30')
    c = Config.from_env(str(tmp_path))
    assert (c.memory_inject, c.memory_top_k, c.memory_max_chars, c.memory_min_score) == (False, 7, 800, 2.5)
    assert (c.memory_max_records, c.memory_ttl_days) == (25, 30)


# --- pruning ---

def test_prune_cap_drops_lowest_hits_then_oldest(tmp_path):
    m = ProjectMemory(tmp_path)
    ids = [m.add('fact', f'note {i}') for i in range(4)]
    m.touch([ids[3]]); m.touch([ids[3]])                      # r4 becomes hot
    removed = m.prune(max_records=2)
    assert removed == [ids[0], ids[1]]                        # coldest two dropped
    assert {r['id'] for r in m.records()} == {ids[2], ids[3]}
    assert m.prune(max_records=10) == []                      # under cap -> no-op

def test_prune_ttl_drops_stale_and_respects_off(tmp_path):
    m = ProjectMemory(tmp_path)
    ancient = datetime.now(tz.utc) - timedelta(days=400)
    m.add('fact', 'ancient note')
    mf = tmp_path / '.coder-agent' / 'memory.json'
    d = json.loads(mf.read_text())
    d['records'][0]['created'] = d['records'][0]['last_seen'] = ancient.isoformat()
    mf.write_text(json.dumps(d))
    fresh = m.add('fact', 'fresh note')
    assert m.prune(ttl_days=0) == []                          # TTL off -> never expires
    assert m.prune(ttl_days=90) == ['r1']
    assert [r['id'] for r in m.records()] == [fresh]

# --- consolidation ---

def test_consolidate_merges_group_keeps_primary_identity(tmp_path):
    m = ProjectMemory(tmp_path)
    a = m.add('fact', 'tests run with pytest', tags=['testing'])
    b = m.add('preference', 'pytest is the runner here', paths=['pyproject.toml'])
    m.touch([a])
    merged, removed = m.consolidate([{'ids': [a, b], 'kind': 'decision',
                                      'text': 'The project tests everything through pytest.'}])
    assert merged == [a] and removed == [b]
    recs = m.records(); assert len(recs) == 1 and recs[0]['id'] == a
    assert recs[0]['text'] == 'The project tests everything through pytest.'
    assert recs[0]['tags'] == ['testing'] and recs[0]['paths'] == ['pyproject.toml']
    assert recs[0]['hits'] == 1                               # summed across members

def test_consolidate_ignores_singletons_unknowns_and_supplies_union_defaults(tmp_path):
    m = ProjectMemory(tmp_path)
    a = m.add('fact', 'x one', tags=['t1']); b = m.add('fact', 'x two', tags=['t2'])
    merged, removed = m.consolidate([
        {'ids': [a], 'text': 'singleton ignored'},
        {'ids': ['r99', 'r98'], 'text': 'unknown ids ignored'},
        {'ids': [a, b], 'text': ''},                           # empty canonical text ignored
        {'ids': [a, b], 'text': 'merged x', 'tags': None, 'paths': None},
    ])
    assert merged == [a] and removed == [b]
    r = m.records()[0]
    assert r['text'] == 'merged x' and sorted(r['tags']) == ['t1', 't2']   # union default

def test_clean_groups_validation():
    from coding_agent.llm import _clean_groups
    out = _clean_groups([
        {'ids': ['r1', 'r2'], 'text': 'ok merge'},
        {'ids': ['r1'], 'text': 'too few'},                    # singleton
        {'ids': ['rx', 'ry'], 'text': 'unknown'},              # unknown ids
        {'ids': ['r1', 'r2'], 'text': ''},                     # empty text
        {'ids': ['r2', 'r1', 'r1'], 'text': 'dedup'},          # dup ids collapse
        'junk',
    ], valid_ids={'r1', 'r2'})
    assert [g['text'] for g in out] == ['ok merge', 'dedup']
    assert out[1]['ids'] == ['r2', 'r1']


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

def test_remember_tool_prunes_to_configured_cap(tmp_path):
    from coding_agent.browser import Browser
    from coding_agent.config import Config
    from coding_agent.github import GitHub
    from coding_agent.sandbox import DockerSandbox
    from coding_agent.git import Git
    from coding_agent.shell import Shell
    from coding_agent.tools import ToolContext
    from coding_agent.workspace import Workspace
    cfg = Config(None, None, None, 'auto', tmp_path, 'prompt', 5000, 30000, 10, 10,
                 20, 100, 10000, False, False, False, True, False, memory_max_records=2)
    c = ToolContext(cfg, Workspace(tmp_path), Shell(cfg), lambda *_: True,
                    Git(tmp_path), Browser(), GitHub(), DockerSandbox(tmp_path))
    pm = ProjectMemory(tmp_path)
    keep = pm.add('fact', 'hot note'); pm.touch([keep])
    pm.add('fact', 'cold stale note')
    dispatch(c, 'remember', {'kind': 'fact', 'text': 'brand new note'})
    texts = [r['text'] for r in ProjectMemory(tmp_path).records()]
    assert len(texts) == 2 and 'hot note' in texts and 'cold stale note' not in texts


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

def test_consolidate_memory_merges_paraphrases_and_journals(tmp_path):
    pm = ProjectMemory(tmp_path)
    pm.add('fact', 'tests run with pytest')
    pm.add('fact', 'the project runs its tests using pytest')
    p = FakeProvider(json.dumps({'groups': [{'ids': ['r1', 'r2'], 'kind': 'fact',
                                             'text': 'Tests run via pytest -q.', 'tags': ['testing']}]}))
    agent = make_agent(tmp_path, p)
    info = agent.consolidate_memory()
    assert info['merged'] == 1 and info['removed'] == 1 and info['after'] == 1
    recs = pm.records()
    assert recs[0]['text'] == 'Tests run via pytest -q.' and recs[0]['tags'] == ['testing']
    lines = agent.session.path.read_text().splitlines()
    assert any(json.loads(l)['kind'] == 'memory_consolidated' for l in lines)

def test_consolidate_memory_prose_reply_is_a_safe_noop(tmp_path):
    pm = ProjectMemory(tmp_path)
    pm.add('fact', 'one'); pm.add('fact', 'two')
    agent = make_agent(tmp_path, FakeProvider('I would probably merge them.'))
    info = agent.consolidate_memory()
    assert info['merged'] == 0 and info['removed'] == 0 and len(pm.records()) == 2

def test_consolidate_memory_needs_two_records_and_propagates_transport_errors(tmp_path):
    pm = ProjectMemory(tmp_path); pm.add('fact', 'lonely')
    agent = make_agent(tmp_path, FakeProvider('{}'))
    assert agent.consolidate_memory()['reason'] == 'fewer than 2 records'
    agent2 = make_agent(tmp_path, FakeProvider(error=RuntimeError('down')))
    pm.add('fact', 'second'); 
    import pytest
    with pytest.raises(RuntimeError):
        agent2.consolidate_memory()

def test_last_memory_ids_exposed_for_cli_line(tmp_path):
    _seed(tmp_path)
    agent = make_agent(tmp_path, FakeProvider())
    assert agent.last_memory_ids == []
    agent.run('fix the failing tests')
    assert agent.last_memory_ids == ['r1']
    off = make_agent(tmp_path, FakeProvider(), memory_inject=False)
    off.run('fix the failing tests')
    assert off.last_memory_ids == []


# --- rendering ---

def test_render_record_shape_and_empty_dump():
    r = rec('r7', kind='preference', text='dense style', tags=['style'])
    out = render_record(r)
    assert out.startswith('[r7/preference] dense style') and '(tags: style)' in out



# ── prune selects by position, not by value ─────────────────────────────────

def test_prune_keeps_one_of_two_identical_records(tmp_path):
    """Two records may legitimately hold the same text; dropping by value kills both."""
    m = ProjectMemory(tmp_path)
    # add() dedupes exact kind+text, so write the duplicate pair directly
    now = datetime.now(tz.utc).isoformat()
    twin = {'kind': 'fact', 'text': 'same text', 'tags': [], 'paths': [],
            'created': now, 'last_seen': now, 'hits': 0}
    m._write({'version': 2, 'records': [
        dict(twin, id='r1'),
        dict(twin, id='r2'),
        dict(twin, id='r3', text='distinct', hits=9),
    ]})
    removed = m.prune(max_records=2)
    assert len(removed) == 1
    remaining = {r['id'] for r in m.records()}
    assert len(remaining) == 2
    assert 'r3' in remaining, 'the hottest record must survive'


def test_prune_returns_empty_when_nothing_to_drop(tmp_path):
    m = ProjectMemory(tmp_path)
    m.add('fact', 'only one')
    assert m.prune(max_records=10, ttl_days=0) == []
    assert len(m.records()) == 1


def test_prune_ttl_and_cap_compose(tmp_path):
    m = ProjectMemory(tmp_path)
    old = (datetime.now(tz.utc) - timedelta(days=400)).isoformat()
    now = datetime.now(tz.utc).isoformat()
    m._write({'version': 2, 'records': [
        {'id': 'r1', 'kind': 'fact', 'text': 'stale', 'tags': [], 'paths': [],
         'created': old, 'last_seen': old, 'hits': 50},
        {'id': 'r2', 'kind': 'fact', 'text': 'cold', 'tags': [], 'paths': [],
         'created': now, 'last_seen': now, 'hits': 0},
        {'id': 'r3', 'kind': 'fact', 'text': 'hot', 'tags': [], 'paths': [],
         'created': now, 'last_seen': now, 'hits': 7},
    ]})
    removed = m.prune(max_records=1, ttl_days=90)
    assert set(removed) == {'r1', 'r2'}          # TTL drops r1 even though it is hot
    assert [r['id'] for r in m.records()] == ['r3']


# ── consolidate must not collapse the date span ─────────────────────────────

def test_consolidate_ignores_members_missing_timestamps(tmp_path):
    m = ProjectMemory(tmp_path)
    m._write({'version': 2, 'records': [
        {'id': 'r1', 'kind': 'fact', 'text': 'a', 'tags': [], 'paths': [],
         'created': '2026-01-01T00:00:00+00:00', 'last_seen': '2026-02-01T00:00:00+00:00',
         'hits': 2},
        {'id': 'r2', 'kind': 'fact', 'text': 'a again', 'tags': [], 'paths': [], 'hits': 3},
    ]})
    merged, removed = m.consolidate([{'ids': ['r1', 'r2'], 'kind': 'fact', 'text': 'a merged'}])
    assert merged == ['r1'] and removed == ['r2']
    primary = m.records()[0]
    assert primary['created'] == '2026-01-01T00:00:00+00:00', 'span must not collapse to ""'
    assert primary['last_seen'] == '2026-02-01T00:00:00+00:00'
    assert primary['hits'] == 5
