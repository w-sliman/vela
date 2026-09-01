import json
from datetime import datetime, timezone as tz

from tests.conftest import make_config
from vela.llm import CodingAgent
from vela.resume import build_digest, list_sessions, resolve_session
from vela.conversation import UserMsg
from vela.session import Session


def ev(kind, payload=None, ts=None):
    return {'timestamp': ts or datetime.now(tz.utc).isoformat(), 'kind': kind,
            'payload': payload if payload is not None else {}}


def write_trace(tmp, name, events):
    d = tmp / '.vela' / 'sessions'; d.mkdir(parents=True, exist_ok=True)
    p = d / f'{name}.jsonl'
    p.write_text(''.join(json.dumps(e) + '\n' for e in events))
    return p


def sample_events():
    return [
        ev('user', {'text': 'refactor llm.py retry logic'}),
        ev('tool_call', {'name': 'read_file', 'arguments_raw': '{"path": "vela/llm.py"}'}),
        ev('tool_call', {'name': 'replace_text',
                         'arguments_raw': '{"paths": ["vela/llm.py", "tests/test_telemetry.py"], "new": "x"}'}),
        ev('tool_call', {'name': 'read_file', 'arguments_raw': '{"path": "vela/llm.py"}'}),  # dup path
        ev('error', {'message': '500 upsteam'}),
        ev('compact', {'compacted': True}),
        ev('assistant', {'text': 'Refactor complete; all 89 tests pass.'}),
        ev('user', {'text': 'now update the changelog'}),
    ]


# --- listing ---

def test_list_sessions_newest_first_excludes_active_and_counts(tmp_path):
    import os, time
    a = write_trace(tmp_path, '20260101-000000-000000', [ev('user', {'text': 'older task'})])
    write_trace(tmp_path, '20260202-000000-000000', sample_events())
    os.utime(a, (time.time() - 3600,) * 2)                       # force older mtime
    active = write_trace(tmp_path, '20260303-000000-000000', [])
    rows = list_sessions(tmp_path, exclude=active)
    assert [r['id'][:8] for r in rows] == ['20260202', '20260101']
    assert rows[0]['turns'] == 2 and rows[0]['first_user'].startswith('refactor')
    assert rows[1]['turns'] == 1

def test_list_sessions_empty_workspace(tmp_path):
    assert list_sessions(tmp_path) == []


# --- resolution ---

def test_resolve_defaults_to_newest_and_supports_index(tmp_path):
    a = write_trace(tmp_path, 'aaa-old', [ev('user', {'text': 'old'})])
    write_trace(tmp_path, 'bbb-new', [ev('user', {'text': 'new'})])
    import os, time; os.utime(a, (time.time() - 9999,) * 2)
    hit, err = resolve_session(tmp_path, None); assert err is None and hit['id'] == 'bbb-new'
    hit, _ = resolve_session(tmp_path, 'last'); assert hit['id'] == 'bbb-new'
    hit, _ = resolve_session(tmp_path, '2'); assert hit['id'] == 'aaa-old'
    hit, _ = resolve_session(tmp_path, '#1'); assert hit['id'] == 'bbb-new'   # explicit index
    hit, err = resolve_session(tmp_path, '#9'); assert hit is None and 'out of range' in err
    hit, err = resolve_session(tmp_path, '5'); assert hit is None and 'out of range' in err

def test_resolve_prefix_unique_ambiguous_missing(tmp_path):
    write_trace(tmp_path, '20260101-abc', [ev('user', {'text': 'x'})])
    write_trace(tmp_path, '20260202-def', [ev('user', {'text': 'y'})])
    hit, _ = resolve_session(tmp_path, '20260202'); assert hit and hit['id'] == '20260202-def'
    hit, err = resolve_session(tmp_path, 'zzz'); assert hit is None and 'no session id' in err
    hit, err = resolve_session(tmp_path, '2026'); assert hit is None and 'ambiguous' in err

def test_resolve_no_sessions(tmp_path):
    hit, err = resolve_session(tmp_path); assert hit is None and 'no resumable sessions' in err

def test_resolve_skips_zero_request_traces(tmp_path):
    import os, time
    empty = write_trace(tmp_path, '20260505-empty', [])
    write_trace(tmp_path, '20260404-good', [ev('user', {'text': 'real work'})])
    os.utime(empty, (time.time() + 10,) * 2)                     # empty but NEWEST
    hit, err = resolve_session(tmp_path, None)
    assert err is None and hit['id'] == '20260404-good'          # empty trace skipped
    hit, _ = resolve_session(tmp_path, '#1'); assert hit['id'] == '20260404-good'
    rows = list_sessions(tmp_path)
    assert [r['turns'] for r in rows] == [0, 1]                  # listing stays honest


# --- digest ---

def test_digest_sections_files_dedup_and_counts(tmp_path):
    p = write_trace(tmp_path, '20260404-000000-000000', sample_events())
    d = build_digest(p, max_chars=6000)
    t = d['text']
    assert t.startswith('[Resumed session 20260404-000000-000000]')
    assert 'verify current file contents before editing' in t
    assert '1. refactor llm.py retry logic' in t and '2. now update the changelog' in t
    assert 'vela/llm.py' in t and 'tests/test_telemetry.py' in t
    assert 'Files touched:' in t
    assert d['files'].count('vela/llm.py') == 1          # duplicate tool calls deduped
    assert t.count('vela/llm.py') == 1                   # rendered once overall
    assert 'Recorded transport errors: 1' in t
    assert 'Compactions already applied: 1' in t
    assert 'Last assistant message (tail): Refactor complete' in t
    assert d['requests'] == 2

def test_digest_drops_oldest_requests_to_fit_budget(tmp_path):
    events = [ev('user', {'text': f'request number {i} with padding text'}) for i in range(50)]
    p = write_trace(tmp_path, 'cap-case', events)
    d = build_digest(p, max_chars=900)
    assert len(d['text']) <= 900
    assert '(…' in d['text'] and 'earlier request(s) omitted' in d['text']
    assert 'request number 49' in d['text']                      # newest kept
    assert 'request number 1 with' not in d['text']              # oldest dropped
    assert '[Resumed session cap-case]' in d['text']             # header always intact

def test_digest_empty_trace_still_renders(tmp_path):
    p = write_trace(tmp_path, 'empty-trace', [])
    d = build_digest(p)
    assert 'No user requests were recorded.' in d['text']


# --- agent wiring ---

def make_agent(tmp_path):
    c = make_config(tmp_path, price_input_per_million=0.0, price_output_per_million=0.0)
    agent = CodingAgent(c, None, Session(tmp_path))
    agent.history = [UserMsg(text='stale context')]
    return agent

def test_start_resumed_replaces_context_and_journals_lineage(tmp_path):
    agent = make_agent(tmp_path)
    agent.start_resumed('[Resumed session abc] prior state…', '20260101-000000-000000')
    assert len(agent.history) == 1
    assert isinstance(agent.history[0], UserMsg)
    assert agent.history[0].text.startswith('[Resumed session abc]')
    lines = agent.session.path.read_text().splitlines()
    kinds = [json.loads(l)['kind'] for l in lines]
    assert 'resumed_from' in kinds
    evt = [json.loads(l)['payload'] for l in lines if json.loads(l)['kind'] == 'resumed_from'][-1]
    assert evt['session'] == '20260101-000000-000000' and evt['chars'] > 0
