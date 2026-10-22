"""Explicit reconstructed executions and reversible exclusion of delayed orders."""
from __future__ import annotations

import json
import time
from decimal import Decimal


def ensure_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS recovery_runs(
          day TEXT PRIMARY KEY,plan_sha256 TEXT NOT NULL,payload TEXT NOT NULL,applied_ns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS strategy_order_exclusions(
          intent_id TEXT PRIMARY KEY,reason TEXT NOT NULL,mirror INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS reconstructed_orders(
          id TEXT PRIMARY KEY,day TEXT NOT NULL,strategy TEXT NOT NULL,symbol TEXT NOT NULL,
          side TEXT NOT NULL,quantity INTEGER NOT NULL,price TEXT NOT NULL,
          submitted_ns INTEGER NOT NULL,filled_ns INTEGER NOT NULL,recorded_ns INTEGER NOT NULL,
          price_basis TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL);
    """)


def active(db, strategy, day):
    row = db.execute('SELECT payload FROM recovery_runs WHERE day=?', (day,)).fetchone()
    return bool(row and strategy in json.loads(row[0])['strategies'])


def excluded_orders(db, strategy=None, day=None):
    sql = ('SELECT o.*,x.mirror FROM orders o JOIN strategy_order_exclusions x '
           'ON o.intent_id=x.intent_id WHERE o.filled>0')
    values = []
    if strategy:
        sql += ' AND o.strategy=?'; values.append(strategy)
    if day:
        sql += " AND replace(o.day,'-','')=?"; values.append(day.replace('-', ''))
    return [dict(r) for r in db.execute(sql, values)]


def undo_excluded_book(db, strategy, book):
    for row in excluded_orders(db, strategy):
        sign = 1 if row['side'] == 'buy' else -1
        cost = Decimal(row['notional'])
        book['cash'] += sign*cost + cost*Decimal('.00015')
        symbol = row['symbol']
        book['positions'][symbol] = book['positions'].get(symbol, 0)-sign*row['filled']
    return book


def exclude(journal, intent_id, reason, *, mirror=False):
    row = journal.db.execute('SELECT * FROM orders WHERE intent_id=?', (intent_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown physical order')
    journal.db.execute('INSERT OR IGNORE INTO strategy_order_exclusions VALUES(?,?,?)',
                       (intent_id, reason, int(mirror)))
    if row['order_number']:
        journal.db.execute('INSERT OR IGNORE INTO report_exclusions VALUES(?,?,?,?,?,?)',
            (row['account_scope'], row['day'].replace('-', ''), row['order_number'],
             row['symbol'], reason, time.time_ns()))


def record_fill(journal, identity, strategy, symbol, side, quantity, price, day,
                event_ns, *, basis, source, fee_rate=Decimal('.000148'),
                submitted_ns=None, notify=True):
    if journal.db.execute('SELECT 1 FROM reconstructed_orders WHERE id=?', (identity,)).fetchone():
        return False
    reason = 'User-authorized outage reconstruction; '+basis
    journal.assume_fill(identity, strategy, symbol, side, quantity, price, day,
                        event_ns, reason, fee_rate, notify=False)
    journal.db.execute('INSERT INTO reconstructed_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (identity, day, strategy, symbol, side, int(quantity), str(price),
         submitted_ns if submitted_ns is not None else event_ns, event_ns,
         time.time_ns(), basis, source,
         'broker_confirmed' if source == 'KIS_confirmed' else 'assumed_filled'))
    if notify:
        journal.notify(identity+':reconstructed', 'mock_reconstructed',
            {'strategy':strategy,'symbol':symbol,'side':side,'quantity':quantity,
             'price':str(price),'event_ns':event_ns,'price_basis':basis,
             'source':source,'reason':reason})
    return True


def mirror_status_fill(journal, row, filled, delta, cost):
    policy = journal.db.execute('SELECT mirror FROM strategy_order_exclusions WHERE intent_id=?',
                                (row['intent_id'],)).fetchone()
    if not delta or not policy or not policy[0]:
        return
    now = time.time_ns()
    day = row['day']
    day = day if '-' in day else day[:4]+'-'+day[4:6]+'-'+day[6:]
    record_fill(journal, 'broker-recovery:'+row['intent_id']+':'+str(filled),
                row['strategy'], row['symbol'], row['side'], delta, cost/delta,
                day, now, submitted_ns=row['created_ns'],
                basis='KIS confirmed live fill; commission estimated at 0.015%',
                source='KIS_confirmed', fee_rate=Decimal('.00015'), notify=False)


def filter_account_report(journal, data, marks):
    """Remove superseded physical exposure once; reconstructed fills stay separate."""
    day = data['day']
    run = journal.db.execute('SELECT payload FROM recovery_runs WHERE day=?', (day,)).fetchone()
    if not run or data.get('recovery'):
        return data
    rows = excluded_orders(journal.db, day=day)
    cash = Decimal(0); positions = {}; excluded_notional = {}
    scope_cash = {}; scope_positions = {}
    excluded_keys = set()
    for row in rows:
        sign = 1 if row['side'] == 'buy' else -1
        cash -= sign*Decimal(row['notional'])
        positions[row['symbol']] = positions.get(row['symbol'],0)+sign*row['filled']
        scope = row['account_scope']
        scope_cash[scope]=scope_cash.get(scope,Decimal(0))-sign*Decimal(row['notional'])
        group=scope_positions.setdefault(scope,{})
        group[row['symbol']]=group.get(row['symbol'],0)+sign*row['filled']
        excluded_notional[scope] = excluded_notional.get(scope,Decimal(0))+Decimal(row['notional'])
        excluded_keys.add((scope, str(row['order_number']).lstrip('0') or '0'))
    fees = Decimal(0); fee_basis = {}; exact_scope_net = {}
    for scope, notional in excluded_notional.items():
        trades = list(journal.db.execute('SELECT order_number,notional FROM broker_trades WHERE account_scope=? AND day=?',
                                        (scope,day)))
        total = sum((Decimal(r['notional']) for r in trades),Decimal(0))
        account_cost = data.get('accounts',{}).get(scope,{}).get('fees')
        all_excluded = bool(trades) and all(
            (scope,str(r['order_number']).lstrip('0') or '0') in excluded_keys for r in trades)
        if account_cost is not None and all_excluded:
            fees += Decimal(str(account_cost)); fee_basis[scope] = 'all account fills excluded; exact account cost'
            actual_positions={k.split(':',1)[1]:int(v)for k,v in data.get('positions',{}).items()
                              if k.startswith(scope+':')and int(v)}
            owned={s:q for s,q in scope_positions[scope].items()if q}
            if actual_positions==owned and data.get('accounts',{}).get(scope,{}).get('net_pnl')is not None:
                exact_scope_net[scope]=Decimal(data['accounts'][scope]['net_pnl'])
        elif account_cost is not None and total:
            fees += Decimal(str(account_cost))*min(Decimal(1),notional/total)
            fee_basis[scope] = 'account cost allocated by notional'
        else:
            fees += notional*Decimal('.00015'); fee_basis[scope] = 'estimated 0.015%'
    for symbol, quantity in positions.items():
        if quantity:
            if symbol not in marks:
                raise RuntimeError('Missing valuation for excluded physical position '+symbol)
            cash += quantity*Decimal(str(marks[symbol]))
    removed = cash-fees
    for scope,exact in exact_scope_net.items():
        estimate=scope_cash[scope]-Decimal(str(data['accounts'][scope]['fees']))
        estimate+=sum((q*Decimal(str(marks[s]))for s,q in scope_positions[scope].items()if q),Decimal(0))
        removed+=exact-estimate
    raw_net, raw_fees = Decimal(data['net_pnl']), data.get('fees')
    data['raw_actual_net_pnl'], data['raw_actual_fees'] = str(raw_net), raw_fees
    data['net_pnl'] = str(raw_net-removed)
    data['adjusted_net_pnl'] = str(raw_net-removed+Decimal(data['assumed']['net_pnl']))
    if raw_fees is not None:
        data['fees'] = str(Decimal(raw_fees)-fees)
        data['adjusted_fees'] = str(Decimal(raw_fees)-fees+Decimal(data['assumed']['fees']))
    data['recovery'] = {'removed_physical_net_pnl':str(removed),'removed_physical_fees':str(fees),
                        'fee_basis':fee_basis,'positions':positions,
                        'plan_sha256':json.loads(run[0])['plan_sha256']}
    data['accounts'] = {}
    return data
