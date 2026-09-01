import json

def _extract_object(s: str):
    start = s.find('{')
    if start < 0:
        return None
    depth = 0; in_string = False; escaped = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escaped: escaped = False
            elif ch == '\\': escaped = True
            elif ch == '"': in_string = False
            continue
        if ch == '"': in_string = True
        elif ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0: return s[start:i+1]
    return None

def parse_tool_arguments(raw):
    if isinstance(raw, dict): return raw, None, False
    if raw is None: return None, 'tool arguments are null', False
    raw = str(raw).strip()
    candidates = [raw]
    extracted = _extract_object(raw)
    if extracted and extracted != raw: candidates.append(extracted)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict): return value, None, candidate != raw
        except json.JSONDecodeError: pass
    s = candidates[-1]; out=[]; in_string=False; escaped=False
    for ch in s:
        if in_string:
            if escaped: out.append(ch); escaped=False
            elif ch == '\\': out.append(ch); escaped=True
            elif ch == '"': out.append(ch); in_string=False
            elif ch == '\n': out.append('\\n')
            elif ch == '\r': out.append('\\r')
            elif ch == '\t': out.append('\\t')
            elif ord(ch) < 32: out.append('\\u%04x' % ord(ch))
            else: out.append(ch)
        else:
            out.append(ch)
            if ch == '"': in_string=True
    try:
        value=json.loads(''.join(out))
        if isinstance(value,dict): return value,None,True
    except json.JSONDecodeError as exc:
        return None,f'malformed tool JSON after safe repair: {exc}',True
    return None,'malformed tool JSON',False
