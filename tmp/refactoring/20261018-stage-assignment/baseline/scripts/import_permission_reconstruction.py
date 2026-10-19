"""Import audited historical permission failures without changing real fills."""
import json
from pathlib import Path
from vibetrading.etf_journal import EtfJournal
from vibetrading.settings import get_settings

root=get_settings().state_dir/'etf-mock'
records=json.loads((root/'permission-reconstruction.json').read_text())
journal=EtfJournal(root/'journal.sqlite3')
for record in records:
    row=dict(journal.db.execute('SELECT * FROM orders WHERE intent_id=?',(record['intent_id'],)).fetchone())
    assert row['status']=='rejected' and row['filled']==0 and not row['order_number']
    payload=json.loads(row['payload']); payload['metadata']['intent_quote']=record['entry_quote']
    with journal.transaction():
        journal.db.execute('UPDATE orders SET payload=?,permission_failure=1,rejection_reason=? WHERE intent_id=?',
            (json.dumps(payload),record['reason'],record['intent_id']))
journal.recover_assumptions()
for record in records:
    if record['exit']:
        quote=record['exit']['quote']
        with journal.transaction():
            journal.assume_fill(record['intent_id']+':historical-exit',record['strategy'],record['symbol'],'sell',
                record['quantity'],quote['bid'],record['day'],quote['intent_ns'],
                'Recorded strategy '+record['exit']['action']+' after permission-rejected assumed entry')
print(json.dumps({'imported':len(records),'actual_pending':len(journal.pending()),'assumed_positions':journal.assumed_positions(),
    'assumed_report':journal.assumed_report('2026-10-14') if not journal.assumed_positions() else 'open until native strategy exit'}))
journal.db.close()
