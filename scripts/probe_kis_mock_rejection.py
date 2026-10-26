"""Read-only mock diagnostics. No POST orders and no credentials in output."""
import asyncio
import json
import sqlite3
import sys
from decimal import Decimal
sys.path.insert(0,'/app')
from execution.kis_execution import KisCashBroker
from vibetrading.settings import get_settings


async def main():
    broker=KisCashBroker(get_settings(),mode='mock')
    assert broker.client.endpoints.use_mock
    await broker.connect()
    state=await broker.account_state()
    db=sqlite3.connect('file:/data/state/etf-mock/journal.sqlite3?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    orders=[dict(r) for r in db.execute('SELECT strategy,symbol,quantity,price,status,filled,order_number FROM orders')]
    db.close()
    results={}
    for symbol in ['069500','122630','229200','251340','005930']:
        row={}
        try:
            result=await broker.client.get('/uapi/domestic-stock/v1/quotations/inquire-price',tr_id='FHKST01010100',
                params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol})
            output=result['body']['output']
            row['quote']={k:output.get(k) for k in ['stck_shrn_iscd','stck_prpr','rprs_mrkt_kor_name','iscd_stat_cls_code',
                'temp_stop_yn','short_over_yn','sltr_yn','mang_issu_cls_code','mrkt_warn_cls_code','etf_larg_cls_code']}
            price=Decimal(str(output['stck_prpr']))
            await asyncio.sleep(.7)
            power=await broker.buying_power(symbol,price)
            row['power']={k:power.get(k) for k in ['ord_psbl_cash','max_buy_qty','nrcvb_buy_qty','psbl_qty_calc_unpr']}
        except Exception as exc:
            row['error']=str(exc)[:300]
        results[symbol]=row
        await asyncio.sleep(.7)
    print(json.dumps({'mock':True,'product_code':broker.account_params()['ACNT_PRDT_CD'],
        'balance':{k:state['summary'].get(k) for k in ['dnca_tot_amt','tot_evlu_amt','thdt_tlex_amt']},
        'positions_count':len(state['positions']),'orders':orders,'symbols':results},ensure_ascii=False))

asyncio.run(main())
