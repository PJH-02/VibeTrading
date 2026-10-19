"""Apply the user's exact test-trade exclusion; preserve all raw broker evidence."""
import json
import sqlite3
import time
from decimal import Decimal

from vibetrading.etf_journal import EtfJournal
from vibetrading.etf_reference import weights
from vibetrading.settings import get_settings
from vibetrading.trade_reporting import order_key

day='2026-10-14'
expected={'40004','40009','42621','42625','42631','42633'}
root=get_settings().state_dir/'etf-mock'
journal=EtfJournal(root/'journal.sqlite3')
rows=[dict(r) for r in journal.db.execute('SELECT * FROM broker_trades WHERE day=? AND account_scope=?',(day,'mock2'))]
assert {str(r['order_number']).lstrip('0') for r in rows}==expected, 'Unexpected account trades; cannot exclude the whole account result'
assert all(r['symbol'] in {'122630','251340'} and r['quantity']==1 for r in rows)
positions={}; gross=Decimal(0)
for row in rows:
    sign=1 if row['side']=='buy' else -1
    positions[row['symbol']]=positions.get(row['symbol'],0)+sign*row['quantity']
    gross-=sign*Decimal(row['notional'])
assert not any(positions.values()), 'Test portfolio was not flat'
closed=json.loads(journal.db.execute("SELECT payload FROM notifications WHERE id=?",('daily:'+day,)).fetchone()[0])
account=closed['accounts']['mock2']
cost=Decimal(account['fees']); net=Decimal(account['net_pnl'])
assert net==gross-cost, 'Broker costs do not reconcile with test round trips'
assert not any(Decimal(str(q)) for key,q in closed['positions'].items() if key.startswith('mock2:'))
backup=root/'journal-before-test-exclusion-20261014.sqlite3'
if not backup.exists():
    with sqlite3.connect(backup) as target: journal.db.backup(target)
evidence={'day':day,'account_scope':'mock2','orders':rows,'gross_pnl':str(gross),'fees':str(cost),'net_pnl':str(net),
    'basis':'All six closed broker trades in mock2 today are user-designated tests; fees from the recorded broker close.'}
with journal.transaction():
    for row in rows:
        journal.db.execute('INSERT OR IGNORE INTO report_exclusions VALUES(?,?,?,?,?,?)',
            ('mock2',day,row['order_number'],row['symbol'],'User: unintended test execution',time.time_ns()))
    journal.db.execute('INSERT INTO report_adjustments VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET net_pnl=excluded.net_pnl,fees=excluded.fees,evidence=excluded.evidence',
        (day,str(net),str(cost),json.dumps(evidence),time.time_ns()))
decisions=[json.loads(r[0]) for r in journal.db.execute("SELECT payload FROM decisions WHERE strategy='KP' AND id LIKE '%:entry'")]
evidence['kp_entry_weights']=[weights(r['direction'],r['leverage'],'KOSPI200') for r in decisions]
evidence['raw_broker_trade_count']=journal.db.execute('SELECT COUNT(*) FROM broker_trades WHERE day=?',(day,)).fetchone()[0]
evidence['excluded_count']=journal.db.execute('SELECT COUNT(*) FROM report_exclusions WHERE day=?',(day,)).fetchone()[0]
(root/'report-test-exclusion-20261014.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps(evidence))
journal.db.close()
