"""Read-only diagnosis of all broker fills and independently stored intents."""
import asyncio
import json
import sqlite3
from contextlib import closing

from execution.kis_execution import KisCashBroker
from runtime.settings import get_settings

DAY='2026-10-14'

async def main():
    settings=get_settings()
    result={'day':DAY,'accounts':{}}
    for scope in ['mock','mock2']:
        broker=KisCashBroker(settings,mode=scope)
        params=broker.account_params() | {'INQR_STRT_DT':DAY.replace('-',''),'INQR_END_DT':DAY.replace('-',''),
            'SLL_BUY_DVSN_CD':'00','PDNO':'','CCLD_DVSN':'00','INQR_DVSN':'00','INQR_DVSN_3':'00',
            'ORD_GNO_BRNO':'','ODNO':'','INQR_DVSN_1':'','CTX_AREA_FK100':'','CTX_AREA_NK100':'','EXCG_ID_DVSN_CD':'KRX'}
        pages=[]
        for page in range(10):
            response=await broker.request('GET','inquire-daily-ccld',broker.tr('0081R'),params,tr_cont='N' if page else '')
            body=response['body']; headers={k.lower():v for k,v in response['headers'].items()}
            rows=[{k:r.get(k) for k in ['ord_dt','ord_tmd','odno','pdno','sll_buy_dvsn_cd','ord_qty','tot_ccld_qty','tot_ccld_amt','avg_prvs','rmn_qty','cncl_yn']} for r in body.get('output1') or []]
            pages.append({'continuation':headers.get('tr_cont'),'rows':rows,'summary':body.get('output2'),
                'context_present':bool(body.get('ctx_area_fk100') or body.get('ctx_area_nk100'))})
            if headers.get('tr_cont','') not in {'M','F'}: break
            params['CTX_AREA_FK100']=body.get('ctx_area_fk100',''); params['CTX_AREA_NK100']=body.get('ctx_area_nk100','')
            await asyncio.sleep(1.1)
        result['accounts'][scope]=pages
        await asyncio.sleep(1.1)
    for label,path in [('strategy',settings.state_dir/'etf-mock/journal.sqlite3'),('runtime',settings.state_dir/'vibetrading.sqlite3')]:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
            db.row_factory=sqlite3.Row
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            result[label]={'tables':[t for t in tables if any(k in t for k in ['order','fill','trade'])]}
            if label=='strategy':
                result[label]['orders']=[dict(r) for r in db.execute('SELECT strategy,symbol,side,quantity,filled,status,day,order_number,account_scope FROM orders')]
                result[label]['assumed']=[dict(r) for r in db.execute('SELECT strategy,symbol,side,quantity,price,fee,day FROM assumed_fills')]
                result[label]['decisions']=[dict(r) for r in db.execute("SELECT id,strategy,payload FROM decisions WHERE strategy='C3'")]
            elif 'orders' in tables:
                result[label]['order_schema']=[r[1] for r in db.execute('PRAGMA table_info(orders)')]
    print(json.dumps(result,ensure_ascii=False))

asyncio.run(main())
