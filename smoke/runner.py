import json,subprocess,time
from pathlib import Path
def main():
    out=[]; root=Path(__file__).parents[1]
    for p in sorted((Path(__file__).parent/'tasks').glob('*.json')):
        t=json.loads(p.read_text());started=time.perf_counter();checks=[]
        for c in t.get('checks',[]):
            r=subprocess.run(c,shell=True,cwd=root,text=True,capture_output=True);checks.append({'command':c,'passed':r.returncode==0,'stdout':r.stdout[-2000:],'stderr':r.stderr[-2000:]})
        out.append({'task':t['name'],'elapsed_ms':(time.perf_counter()-started)*1000,'passed':all(x['passed'] for x in checks),'checks':checks})
    print(json.dumps(out,indent=2));return 0 if all(x['passed'] for x in out) else 1
if __name__=='__main__':raise SystemExit(main())
