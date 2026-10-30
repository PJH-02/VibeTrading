from datetime import datetime

from operations.session_alerts import KST, session_state


def _kst(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


def test_us_equity_session_tracks_daylight_saving():
    assert session_state("US_EQUITY_ETF", _kst(2026, 3, 6, 23)) == (False, "2026-03-06")
    assert session_state("US_EQUITY_ETF", _kst(2026, 3, 13, 23)) == (True, "2026-03-13")


def test_krx_holiday_blocks_derivatives_session():
    assert session_state("KR_DERIVATIVES", _kst(2026, 9, 25, 10)) == (False, "2026-09-25")
    assert session_state("KR_DERIVATIVES", _kst(2026, 9, 28, 10)) == (True, "2026-09-28")


def test_cme_daily_break_is_closed():
    assert session_state("US_FUTURES", _kst(2026, 9, 28, 6, 30))[0] is False
    assert session_state("US_FUTURES", _kst(2026, 9, 28, 7, 30))[0] is True
