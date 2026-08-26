from __future__ import annotations
import difflib, re

def _nearest_lines(original, old, n=3, cutoff=0.4):
    """Return up to n 'line N: <text>' strings closest to old, for error hints."""
    target=' '.join(old.split())
    scored=[]
    for i,line in enumerate(original.splitlines(),1):
        score=difflib.SequenceMatcher(None,' '.join(line.split()),target).ratio()
        if score>=cutoff: scored.append((score,i,line))
    scored.sort(key=lambda t:(-t[0],t[1]))
    return [f'line {i}: {line.strip()[:80]}' for _,i,line in scored[:n]]

def exact_replace(original, old, new, occurrence=1):
    count=original.count(old)
    if count==0:
        msg='exact target text was not found'
        hints=_nearest_lines(original,old)
        if hints: msg+='; closest matches in the current file:\n'+'\n'.join(hints)
        raise ValueError(msg)
    if occurrence<1 or occurrence>count: raise ValueError(f"occurrence must be 1..{count}")
    parts=original.split(old)
    return old.join(parts[:occurrence])+new+old.join(parts[occurrence:])

def fuzzy_replace(original, old, new, threshold=.88):
    if old in original: return exact_replace(original,old,new,1)
    lines=original.splitlines(True); target=old.splitlines(True)
    if not target: raise ValueError("empty replacement target")
    best=(0.0,0)
    for i in range(0,max(1,len(lines)-len(target)+1)):
        chunk=''.join(lines[i:i+len(target)])
        score=difflib.SequenceMatcher(None,chunk,old).ratio()
        if score>best[0]:best=(score,i)
    if best[0]<threshold: raise ValueError(f'target not found; best similarity={best[0]:.3f} near line {best[1]+1}; re-read the file and retry with the exact current text or use start_line/end_line')
    return ''.join(lines[:best[1]])+new+''.join(lines[best[1]+len(target):])

def unified_apply(original, patch):
    src=original.splitlines(True); pl=patch.splitlines(True)
    hunks=[i for i,x in enumerate(pl) if x.startswith('@@')]
    if not hunks: raise ValueError('patch must contain a unified-diff hunk')
    out=src[:]; offset=0
    for n,hi in enumerate(hunks):
        m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',pl[hi].strip())
        if not m: raise ValueError(f"unsupported hunk header: {pl[hi].strip()}; expected format '@@ -<oldStart>,<len> +<newStart>,<len> @@', e.g. '@@ -1,3 +1,4 @@'. If the patch keeps failing, switch to replace_text.")
        pos=int(m.group(1))-1+offset; consumed=0; repl=[]
        end=hunks[n+1] if n+1<len(hunks) else len(pl)
        for line in pl[hi+1:end]:
            if line.startswith(('---','+++')): continue
            if not line: continue
            mark,body=line[0],line[1:]
            if mark==' ':
                if pos+consumed>=len(out) or out[pos+consumed]!=body: raise ValueError('patch context does not match')
                repl.append(body); consumed+=1
            elif mark=='-':
                if pos+consumed>=len(out) or out[pos+consumed]!=body: raise ValueError('patch deletion does not match')
                consumed+=1
            elif mark=='+': repl.append(body)
            elif mark=='\\': continue
            else: raise ValueError(f'unsupported patch line: {line!r}')
        out[pos:pos+consumed]=repl; offset += len(repl)-consumed
    return ''.join(out)

def replace_lines(original,start_line,end_line,new):
    """Replace 1-based inclusive line range [start_line,end_line] verbatim with new."""
    lines=original.splitlines(True)
    if start_line<1 or end_line<start_line or end_line>len(lines):
        raise ValueError(f'line range {start_line}-{end_line} outside file ({len(lines)} lines); re-read the file for current line numbers')
    return ''.join(lines[:start_line-1])+new+''.join(lines[end_line:])

def patch_or_replace(original,path,patch=None,old=None,new=None,occurrence=1,fuzzy=False):
    if patch is not None:return unified_apply(original,patch)
    if old is None or new is None:raise ValueError('provide patch or old/new')
    return fuzzy_replace(original,old,new) if fuzzy else exact_replace(original,old,new,occurrence)
