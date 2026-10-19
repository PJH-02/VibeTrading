import sys,json,cProfile,pstats
from pathlib import Path
import pyarrow.parquet as pq
sys.path.insert(0,'/research')
from extract import Series
from reduce import float_book_table
root=Path('/research');cat=json.loads((root/'catalogue.json').read_text())
m=min([r for r in cat if r['venue']=='binance' and r['event_type']=='order_book'],key=lambda r:r['min_ts_ns'])
path=root/'binance_native'/(m['file_id']+'.parquet')
def trial():
 for _ in range(20):
  s={}
  for r in float_book_table(pq.ParquetFile(path).read()).to_pylist():
   a=r['instrument_std_symbol'];s.setdefault(a,Series('binance',a,lambda r:None)).book(r)
p=cProfile.Profile();p.runcall(trial);pstats.Stats(p).sort_stats('cumtime').print_stats(18)
