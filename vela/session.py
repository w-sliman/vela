from __future__ import annotations
import json
from datetime import datetime, timezone
class Session:
    def __init__(self,workspace):
        d=workspace/'.vela'/'sessions';d.mkdir(parents=True,exist_ok=True)
        self.path=d/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')+'.jsonl')
    def record(self,kind,payload):
        with self.path.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'kind':kind,'payload':payload},ensure_ascii=False,default=str)+'\n')
    def recent(self,limit=30):
        if not self.path.exists(): return []
        return [json.loads(x) for x in self.path.read_text(encoding='utf-8').splitlines()[-limit:] if x.strip()]
