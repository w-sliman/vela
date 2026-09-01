from vela.json_repair import parse_tool_arguments

def test_literal_newline_in_json_string():
    value,error,repaired=parse_tool_arguments('{"content":"line one\nline two"}')
    assert error is None
    assert value['content']=='line one\nline two'
    assert repaired

def test_valid_json():
    value,error,repaired=parse_tool_arguments('{"path":"x.py"}')
    assert value=={'path':'x.py'}
    assert error is None
    assert repaired is False
