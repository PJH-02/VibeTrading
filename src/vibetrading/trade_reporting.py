"""Presentation-only exclusions. Broker and strategy evidence stays immutable."""
from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from decimal import Decimal


def order_key(scope, day, number):
    return (scope, day.replace('-', ''), str(number).lstrip('0') or '0') if number else None


def report_policy(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    excluded = set()
    adjustments = {}
    if 'report_exclusions' in tables:
        excluded = {order_key(*row) for row in db.execute('SELECT account_scope,day,order_number FROM report_exclusions')}
    if 'report_adjustments' in tables:
        adjustments = {row[0]: {'net_pnl':row[1], 'fees':row[2]} for row in db.execute('SELECT day,net_pnl,fees FROM report_adjustments')}
    return excluded, adjustments


def adjusted_report(data, adjustment):
    """Subtract excluded results once from a fresh copy of the raw report."""
    result = copy.deepcopy(data)
    if not adjustment:
        return result
    if result.get('test_trades_excluded'):
        return result
    net, fees = Decimal(adjustment['net_pnl']), Decimal(adjustment['fees'])
    for key in ['net_pnl', 'adjusted_net_pnl']:
        if result.get(key) is not None:
            result[key] = str(Decimal(result[key])-net)
    for key in ['fees', 'adjusted_fees']:
        if result.get(key) is not None:
            result[key] = str(Decimal(result[key])-fees)
    # Raw per-account figures and validation totals include tests; showing
    # them alongside filtered totals would contradict the displayed report.
    result['accounts'] = {}
    result['validation'] = None
    result['test_trades_excluded'] = True
    return result


def daily_report_view(settings, data):
    path = settings.state_dir/'etf-mock/journal.sqlite3'
    if not path.exists():
        return data
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        _, adjustments = report_policy(db)
    return adjusted_report(data, adjustments.get(data['day']))
