import json
import time
from decimal import Decimal

from test_telegram_status import _controller
from portfolio.etf_journal import EtfJournal
from operations.telegram import TelegramController
from operations.telegram_trade import trade_report
from operations.trade_reporting import adjusted_report, daily_report_view


def fixture(tmp_path):
    bot=_controller(tmp_path)
    journal=EtfJournal(tmp_path/'state/etf-mock/journal.sqlite3')
    data={'day':'2026-10-14','net_pnl':'-36','fees':'41','adjusted_net_pnl':'-536','adjusted_fees':'49',
        'accounts':{'mock':{'net_pnl':'99','fees':'1'},'mock2':{'net_pnl':'-135','fees':'40'}},
        'broker_fills_complete':True,'validation':{'net_pnl':'-20','fees':'20'}}
    journal.notify('daily:2026-10-14','mock_daily',data)
    for scope,number,symbol,side,amount in [('mock','1','069500','buy','1000'),('mock','2','069500','sell','1100'),
            ('mock2','000003','122630','buy','1000'),('mock2','000004','122630','sell','905')]:
        journal.db.execute('INSERT INTO broker_trades VALUES(?,?,?,?,?,?,?)',(scope,'2026-10-14',number,symbol,side,1,amount))
    for number in ['3','4']:
        journal.db.execute('INSERT INTO report_exclusions VALUES(?,?,?,?,?,?)',('mock2','2026-10-14',number,'122630','tests',time.time_ns()))
    journal.db.execute('INSERT INTO report_adjustments VALUES(?,?,?,?,?)',('2026-10-14','-135','40','verified',time.time_ns()))
    journal.db.execute('INSERT INTO assumed_fills VALUES(?,?,?,?,?,?,?,?,?,?)',('A','KP','122630','buy',3,'100','1','2026-10-14',time.time_ns(),'permission'))
    return bot,journal,data


def test_all_non_test_tickers_are_listed_and_test_fills_and_costs_are_removed(tmp_path):
    bot,journal,data=fixture(tmp_path)
    text=trade_report(bot.settings,'2026-10-14'); lines=text.splitlines()
    assert lines[1].startswith('069500 ') and '실제 매수 1주' in lines[1]
    assert lines[2].startswith('122630 ') and '가정 매수 3주' in lines[2]
    assert '실제 매수' not in lines[2] and '실제 매도' not in lines[2]
    for line in lines[-2:]:
        assert '-401.00원' in line and '[실제 +99원]' in line and '9.00원' in line
    assert journal.db.execute('SELECT COUNT(*) FROM broker_trades').fetchone()[0]==4
    assert json.loads(journal.db.execute("SELECT payload FROM notifications").fetchone()[0])==data
    journal.db.close()


def test_exclusion_does_not_hide_same_ticker_or_order_number_on_a_later_day(tmp_path):
    bot,journal,data=fixture(tmp_path)
    data=dict(data,day='2026-10-15',net_pnl='10',fees='1',adjusted_net_pnl='10',adjusted_fees='1')
    journal.notify('daily:2026-10-15','mock_daily',data)
    journal.db.execute('INSERT INTO broker_trades VALUES(?,?,?,?,?,?,?)',('mock2','2026-10-15','3','122630','buy',2,'2000'))
    text=trade_report(bot.settings,'2026-10-15')
    assert '실제 매수 2주' in text and '+10.00원' in text.splitlines()[-2]
    journal.db.close()


def test_order_number_is_scoped_to_account(tmp_path):
    bot,journal,_=fixture(tmp_path)
    journal.db.execute('INSERT INTO broker_trades VALUES(?,?,?,?,?,?,?)',('mock','2026-10-14','3','229200','buy',1,'100'))
    assert '229200 KODEX 코스닥150 | 실제 매수 1주' in trade_report(bot.settings,'2026-10-14')
    journal.db.close()


def test_exclusions_apply_to_daily_notification_and_are_idempotent(tmp_path):
    bot,journal,data=fixture(tmp_path)
    view=daily_report_view(bot.settings,data)
    assert Decimal(view['net_pnl'])==99 and Decimal(view['fees'])==1
    assert not view['accounts'] and view['validation'] is None
    assert adjusted_report(view,{'net_pnl':'-135','fees':'40'})==view
    assert data['net_pnl']=='-36' and data['fees']=='41'
    journal.db.close()


def test_signal_notification_names_both_reference_and_execution_tickers():
    text=TelegramController._format_event('signal',{'strategy_id':'KP','action':'enter_long',
        'instrument':{'std_symbol':'KIWOOM:KRX:069500'},'metadata':{'weights':{'069500':0,'122630':1}}})
    assert '체결 아님' in text and '기준 종목: KIWOOM:KRX:069500' in text
    assert '주문 대상: 122630 (100%)' in text and '069500 (0%)' not in text
