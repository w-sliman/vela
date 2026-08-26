from types import SimpleNamespace as NS

from coding_agent.telemetry import Metrics, USAGE_ADVICE, extract_usage


def test_responses_shape():
    u = extract_usage(NS(input_tokens=10, output_tokens=5, total_tokens=15))
    assert u == {'input': 10, 'output': 5, 'total': 15}


def test_chat_shape():
    u = extract_usage(NS(prompt_tokens=7, completion_tokens=3, total_tokens=10))
    assert u == {'input': 7, 'output': 3, 'total': 10}


def test_dict_shape_tolerated():
    u = extract_usage({'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3})
    assert u == {'input': 1, 'output': 2, 'total': 3}


def test_total_derived_when_absent():
    u = extract_usage(NS(input_tokens=4, output_tokens=6))
    assert u['total'] == 10


def test_reasoning_captured():
    details = NS(reasoning_tokens=9)
    u = extract_usage(NS(input_tokens=1, output_tokens=2, total_tokens=3,
                         output_tokens_details=details))
    assert u['reasoning'] == 9


def test_absent_is_none_never_zeros():
    assert extract_usage(None) is None
    assert extract_usage(NS()) is None
    assert extract_usage({}) is None
    assert extract_usage(NS(prompt_tokens=0, completion_tokens=0)) is None


def test_metrics_counts_missing_and_sums_real():
    m = Metrics()
    m.add(None, 1.0)
    assert m.calls == 1 and m.missing_usage == 1 and m.input_tokens == 0
    m.add(NS(prompt_tokens=4, completion_tokens=6, total_tokens=10), 2.0)
    assert (m.input_tokens, m.output_tokens, m.calls, m.missing_usage) == (4, 6, 2, 1)
    d = m.as_dict()
    assert 'missing_usage' in d


def test_metrics_tracks_last_prompt_size():
    m = Metrics()
    assert m.last_input_tokens == 0
    m.add(NS(input_tokens=100, output_tokens=5, total_tokens=105), 1.0)
    m.add(NS(input_tokens=250, output_tokens=8, total_tokens=258), 1.0)
    assert m.last_input_tokens == 250
    assert (m.input_tokens, m.output_tokens) == (350, 13)


def test_advice_mentions_stream_option():
    assert 'include_usage' in USAGE_ADVICE
