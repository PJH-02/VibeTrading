import json,pathlib,subprocess
image='sha256:c8689b8f46e9e79cf8c43c4460f5465dc724095b0c5223d2554eedb682f011f2'
code="import ast,pathlib; p=pathlib.Path('/app/vibetrading/runtime.py'); s=p.read_text(); t=ast.parse(s); print(next(ast.get_source_segment(s,n) for n in ast.walk(t) if isinstance(n,ast.AsyncFunctionDef) and n.name=='confirm_recovered'))"
s=subprocess.check_output(['docker','run','--rm','--network=none','--memory=64m',image,'python','-c',code],text=True)
r={'image':image,'source':s};pathlib.Path('/srv/vibetrading-capacity/incident-20261006/old-market-readiness.json').write_text(json.dumps(r,indent=2));print(s)
