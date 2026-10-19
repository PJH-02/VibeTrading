import json,sys
from pathlib import Path
import numpy as np,pandas as pd
sys.path.insert(0,str(Path(__file__).parent))
from diagnose_fv_prediction import bh
root=Path('docs/evidence/fv_research_20261004');parts=[root/x for x in (sys.argv[1:] or ['pilot_final','production'])]
for name in ('indicator_metrics.csv','response_curves.csv','coverage.csv','unavailable.csv'):
 frames=[pd.read_csv(p/name) for p in parts if (p/name).exists()]
 frame=pd.concat(frames,ignore_index=True)
 if name=='indicator_metrics.csv':
  r=frame.to_dict('records')
  for row in r:
   row['p_loss_gain_block_centered']=float(row['p_loss_gain_block_centered']) if pd.notna(row.get('p_loss_gain_block_centered')) else None
   row.pop('q_loss_gain_bh_exploratory',None)
  bh(r);frame=pd.DataFrame(r)
 frame.to_csv(root/name,index=False,encoding='utf-8-sig')
summaries=[json.loads((p/'summary.json').read_text(encoding='utf-8')) for p in parts]
merged=summaries[0].copy()
for name in ('metrics_rows','response_rows'):merged[name]=sum(x[name] for x in summaries)
for name in ('coverage','unavailable'):merged[name]=sum([x[name] for x in summaries],[])
(root/'summary.json').write_text(json.dumps(merged,ensure_ascii=False,indent=2),encoding='utf-8')
models=sum([json.loads((p/'calibrated_models.json').read_text(encoding='utf-8')) for p in parts],[])
(root/'calibrated_models.json').write_text(json.dumps(models,separators=(',',':')),encoding='utf-8')
print(json.dumps({'merged_rows':merged['metrics_rows'],'models':len(models)}))
