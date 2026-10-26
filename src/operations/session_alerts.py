from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

KST = timezone(timedelta(hours=9))

NEW_YORK = ZoneInfo("America/New_York")

# KRX day-session hours. The calendar determines the trading date, including
# Korean exchange holidays.
SESSIONS = {
    "KR_EQUITY_ETF": (time(9, 0), time(15, 30)),
    "KR_DERIVATIVES": (time(8, 45), time(15, 45)),
    "US_EQUITY_ETF": (time(9, 30), time(16, 0)),
    "US_FUTURES": (time(18, 0), time(17, 0)),
}


@lru_cache(maxsize=3)
def _calendar(name: str):
    # Import only for sessions that need a calendar. Crypto collection and
    # storage processes should not carry pandas/calendars in their RSS.
    import exchange_calendars as calendars

    return calendars.get_calendar(name)


def session_state(group: str, now: datetime | None = None) -> tuple[bool, str]:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("session_state requires a timezone-aware datetime")
    instant = instant.astimezone(timezone.utc)
    if group.startswith("KR_"):
        local = instant.astimezone(KST)
        day = local.date()
        start, end = SESSIONS[group]
        active = _calendar("XKRX").is_session(str(day)) and start <= local.time() < end
        return bool(active), day.isoformat()
    if group == "US_EQUITY_ETF":
        local = instant.astimezone(NEW_YORK)
        # XNYS accounts for holidays, daylight saving and early closes.
        active = _calendar("XNYS").is_open_on_minute(instant, ignore_breaks=False)
        return bool(active), local.date().isoformat()
    if group == "US_FUTURES":
        local = instant.astimezone(NEW_YORK)
        # CMES supplies CME session dates; the generic calendar omits its
        # daily 17:00-18:00 ET maintenance break.
        in_break = time(17, 0) <= local.time() < time(18, 0)
        active = not in_break and _calendar("CMES").is_open_on_minute(instant, ignore_breaks=False)
        session_day = local.date() + (timedelta(days=1) if local.time() >= time(18, 0) else timedelta())
        return bool(active), session_day.isoformat()
    raise KeyError(group)
