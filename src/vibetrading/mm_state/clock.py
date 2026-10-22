"""Live feed source freshness. Offline replay uses recorded receipt clocks instead."""
import time


def require_source_fresh(source_ns, wall_ns=None, max_age=3., future_tolerance=1.):
    if not source_ns:return
    age=((time.time_ns() if wall_ns is None else wall_ns)-source_ns)/1e9
    if not -future_tolerance<=age<=max_age:
        raise RuntimeError('Stale source timestamp or unsynchronized exchange/local clock')
