"""Read-only trade summaries. Assumptions never masquerade as broker fills."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .trade_reporting import adjusted_report, order_key, report_policy

KST = ZoneInfo('Asia/Seoul')
NAMES = {'069500':'KODEX 200', '122630':'KODEX 레버리지',
         '229200':'KODEX 코스닥150', '251340':'KODEX 코스닥150선물인버스'}


def trade_report(settings, day=None):
    day = day or datetime.now(KST).strftime('%Y-%m-%d')
    root = settings.state_dir / 'etf-mock'
    path = root / 'journal.sqlite3'
    if not path.exists():
        return (f'거래 내역 · {day} KST\n거래 기록 없음\n'
                '당일 순수익·수수료: 조회 자료 없음\n전체기간 순수익·수수료: 조회 자료 없음')
    trades = defaultdict(lambda: defaultdict(lambda: [0, Decimal(0)]))
    reconstruction_times=defaultdict(set)
    reports = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        excluded, adjustments = report_policy(db)
        if 'notifications' in tables:
            for row in db.execute("SELECT payload,created_ns FROM notifications WHERE kind='mock_daily' ORDER BY created_ns"):
                data = json.loads(row['payload'])
                data['asof_ns'] = row['created_ns']
                if data['day'] <= day:
                    reports[data['day']] = data
        if 'account_reports' in tables:
            for row in db.execute('SELECT day,payload,asof_ns FROM account_reports WHERE day<=?', (day,)):
                data = json.loads(row['payload']); data['asof_ns'] = row['asof_ns']
                if row['day'] not in reports:
                    reports[row['day']] = data
                elif data.get('broker_fills_complete'):
                    # A nightly KIS balance reset must never replace the
                    # recorded close with a spurious zero daily P&L/cost.
                    reports[row['day']]['broker_fills_complete'] = True
        broker_complete = reports.get(day, {}).get('broker_fills_complete', False)
        if broker_complete and 'broker_trades' in tables:
            for row in db.execute('SELECT * FROM broker_trades WHERE day=?', (day,)):
                if order_key(row['account_scope'],day,row['order_number']) in excluded:
                    continue
                add_trade(trades, row['symbol'], '실제', row['side'], row['quantity'], Decimal(row['notional']))
        elif 'orders' in tables:
            # Incremental durable fill events carry the actual fill date, unlike
            # orders.day when an order fills across the midnight boundary.
            fills = list(db.execute("SELECT payload,created_ns FROM notifications WHERE kind='mock_fill'")) if 'notifications' in tables else []
            if fills:
                for row in fills:
                    if datetime.fromtimestamp(row['created_ns']/1e9, KST).strftime('%Y-%m-%d') != day:
                        continue
                    data = json.loads(row['payload']); qty = int(data['filled_quantity'])
                    if order_key(data.get('account_scope','mock'),day,data.get('order_number')) in excluded:
                        continue
                    add_trade(trades, data['symbol'], '실제', data['side'], qty, Decimal(data['fill_price'])*qty)
            else:
                for row in db.execute('SELECT * FROM orders WHERE replace(day,\'-\',\'\')=? AND filled>0', (day.replace('-', ''),)):
                    if order_key(row['account_scope'],day,row['order_number']) in excluded:
                        continue
                    add_trade(trades, row['symbol'], '실제', row['side'], row['filled'], Decimal(row['notional']))
        if 'assumed_fills' in tables:
            reconstructed={r['id']:dict(r)for r in db.execute('SELECT * FROM reconstructed_orders WHERE day=?',(day,))}if 'reconstructed_orders'in tables else{}
            for row in db.execute('SELECT * FROM assumed_fills WHERE day=?', (day,)):
                record=reconstructed.get(row['id'])
                basis=('실제·복구장부'if record['status']=='broker_confirmed'else'복구 가정')if record else'가정'
                add_trade(trades, row['symbol'], basis, row['side'], row['quantity'], Decimal(row['price'])*row['quantity'])
                if record:
                    reconstruction_times[row['symbol'],basis,row['side']].add(
                        datetime.fromtimestamp(record['submitted_ns']/1e9,KST).strftime('%H:%M'))
        trade_days = set()
        if 'orders' in tables:
            trade_days.update(row[0] for row in db.execute('SELECT DISTINCT day FROM orders WHERE filled>0'))
        if 'assumed_fills' in tables:
            trade_days.update(row[0] for row in db.execute('SELECT DISTINCT day FROM assumed_fills'))
    validation = root/'mock2-validation.json'
    if validation.exists():
        data = json.loads(validation.read_text(encoding='utf-8'))
        validation_day = datetime.fromtimestamp(data['created_ns']/1e9, KST).strftime('%Y-%m-%d')
        if data.get('complete') and validation_day == day and not broker_complete:
            for row in data['orders']:
                qty = int(row.get('filled', 0))
                if qty:
                    if order_key('mock2',day,row.get('order_number')) in excluded:
                        continue
                    add_trade(trades, row['symbol'], '실제 검증', row['side'], qty, Decimal(str(row['notional'])))
    reports = {date:adjusted_report(data, adjustments.get(date)) for date,data in reports.items()}
    suffix = ' · 테스트 거래 제외' if adjustments else ''
    lines = [f'거래 내역 · {day} KST (실제 / 가정 구분){suffix}']
    for symbol, groups in sorted(trades.items()):
        entries = []
        for (basis, side), (qty, amount) in sorted(groups.items()):
            times=','.join(sorted(reconstruction_times.get((symbol,basis,side),[])))
            entries.append(f'{basis} {"매수" if side=="buy" else "매도"} {qty:,}주 @{amount/qty:,.2f}원'+(f' ({times})'if times else''))
        lines.append(f'{symbol} {NAMES.get(symbol, "")} | ' + ' · '.join(entries))
    if not trades:
        lines.append('당일 체결 기록 없음')
    current = reports.get(day)
    lines.append(summary('당일', [current] if current else []))
    historical = [reports[key] for key in sorted(reports)]
    missing = {normalize_day(d) for d in trade_days if normalize_day(d) <= day} - reports.keys()
    span = f'{min(reports)}~{max(reports)}' if reports else '기록 없음'
    if missing:
        span += '; 일부 일자 평가자료 누락'
    if day not in reports:
        span += '; 당일 평가 미포함'
    lines.append(summary(f'전체 기록기간({span})', historical))
    return '\n'.join(lines)


def normalize_day(day):
    return day if '-' in day else day[:4]+'-'+day[4:6]+'-'+day[6:]


def add_trade(trades, symbol, basis, side, qty, amount):
    group = trades[symbol][basis, side]
    group[0] += qty; group[1] += amount


def summary(label, reports):
    if not reports:
        return f'{label} 순수익·수수료: 조회 자료 없음'
    net = sum((Decimal(r.get('adjusted_net_pnl', r['net_pnl'])) for r in reports), Decimal(0))
    actual = sum((Decimal(r['net_pnl']) for r in reports), Decimal(0))
    costs = [r.get('adjusted_fees', r.get('fees')) for r in reports]
    fees = f'{sum((Decimal(c) for c in costs), Decimal(0)):,.2f}원' if all(c is not None for c in costs) else '조회 불가'
    stamp = datetime.fromtimestamp(max(r['asof_ns'] for r in reports)/1e9, KST).strftime('%m/%d %H:%M')
    return (f'{label} 순수익(실제+가정) {net:+,.2f}원 [실제 {actual:+,.0f}원] | '
            f'수수료·제비용(가정 추정 포함) {fees} · {stamp} 기준')
