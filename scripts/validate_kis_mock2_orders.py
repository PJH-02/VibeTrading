"""Explicitly authorized one-share mock2 round trips; persist before POST."""
import asyncio
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import nats
from vibetrading.kis_execution import KisCashBroker
from core.models import AssetClass, InstrumentId, OrderIntent, OrderDestination, Side, Venue
from vibetrading.settings import get_settings

async def main():
    settings=get_settings(); broker=KisCashBroker(settings,mode='mock2')
    assert broker.client.endpoints.use_mock
    await broker.connect(); initial=await broker.account_state()
    path=settings.state_dir/'etf-mock/mock2-validation.json'
    if path.exists(): raise RuntimeError('Existing validation record requires review; do not resubmit')
    record={'created_ns':time.time_ns(),'mock':True,'orders':[],
        'initial_equity':initial['summary']['tot_evlu_amt'],'initial_positions':{r['pdno']:r['hldg_qty'] for r in initial['positions']}}
    def save():
        temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(record,indent=2)); temp.replace(path)
    save()
    nc=await nats.connect(settings.nats.url,max_reconnect_attempts=2,connect_timeout=3)
    quotes={}
    async def receive(msg):
        data=json.loads(msg.data); symbol=data['instrument']['raw_symbol']
        if symbol in {'122630','251340'} and data.get('bids') and data.get('asks'):
            quotes[symbol]=(max(Decimal(r['price']) for r in data['bids']),min(Decimal(r['price']) for r in data['asks']),time.time_ns())
    sub=await nc.subscribe('live.md.order_book.kiwoom.etf.>',cb=receive)
    async def execute(symbol,side,market=False):
        deadline=time.monotonic()+15
        while symbol not in quotes or time.time_ns()-quotes[symbol][2]>10_000_000_000:
            if time.monotonic()>deadline: raise RuntimeError('Fresh quote unavailable')
            await asyncio.sleep(.1)
        bid,ask,_=quotes[symbol]
        identity=hashlib.sha256(f'validation:{symbol}:{side}:{market}:{time.time_ns()}'.encode()).hexdigest()
        intent=OrderIntent(intent_id=identity,client_order_id=identity[:32],job_id='MOCK2_VALIDATION',signal_id=identity,
            destination=OrderDestination.REAL,instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol=symbol,std_symbol=symbol),
            side=side,quantity=Decimal(1),limit_price=None if market else ask+5 if side==Side.BUY else bid-5,
            metadata={'account_mode':'mock2','validation':True})
        entry={'symbol':symbol,'side':side.value,'quantity':1,'market':market,'status':'submitting','created_ns':time.time_ns()}
        record['orders'].append(entry); save()
        try: output=await broker.submit_order(intent)
        except BaseException as exc:
            entry.update(status='submission_error_requires_review',error=str(exc)); save(); raise
        entry.update(status='accepted',order_number=output.get('ODNO',output.get('odno')),
            organization=output.get('KRX_FWDG_ORD_ORGNO',output.get('krx_fwdg_ord_orgno',''))); save()
        accepted=time.monotonic(); cancelled=False
        for _ in range(25):
            await asyncio.sleep(.7)
            status=await broker.order_status(entry['order_number'],symbol=symbol)
            entry.update(filled=int(Decimal(status.get('tot_ccld_qty','0'))),notional=status.get('tot_ccld_amt'),
                remaining=status.get('rmn_qty')); save()
            if entry['filled']==1:
                entry['status']='filled'; save(); return
            if not market and not cancelled and time.monotonic()-accepted>=5:
                entry['status']='cancel_requested'; save()
                try: await broker.cancel_order(entry['order_number'],entry['organization'])
                except Exception: pass
                cancelled=True
            if cancelled and (status.get('cncl_yn')=='Y' or int(Decimal(status.get('rmn_qty','1')))==0):
                entry['status']='cancelled_unfilled'; save()
                return await execute(symbol,side,True)
        raise RuntimeError('Broker order remains uncertain; retain record and reconcile')
    try:
        for symbol in ['122630','251340']:
            await execute(symbol,Side.BUY)
            await execute(symbol,Side.SELL)
        final=await broker.account_state()
        end={r['pdno']:r['hldg_qty'] for r in final['positions'] if Decimal(r['hldg_qty'])>0}
        start={s:q for s,q in record['initial_positions'].items() if Decimal(q)>0}
        assert end==start,'Validation positions must return to initial holdings'
        record.update(complete=True,final_equity=final['summary']['tot_evlu_amt'],fees=final['summary'].get('thdt_tlex_amt'),
            validation_net_pnl=str(Decimal(final['summary']['tot_evlu_amt'])-Decimal(record['initial_equity'])))
        save(); print(json.dumps(record))
    finally:
        await sub.unsubscribe(); await nc.close()

asyncio.run(main())
