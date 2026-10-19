import pytest

from vibetrading.feed_recovery import EngineCursor, FeedMode, FeedRecovery, RecoveryError, RecoveryFrame


def frame(sequence, payload=None, incarnation="run1"):
    return RecoveryFrame(EngineCursor("collector", incarnation, sequence), payload or str(sequence).encode())


def recovery(**kwargs):
    applied = []
    instance = FeedRecovery(lambda value,mode: applied.append((value.cursor.sequence,mode)), max_items=4,max_bytes=16,max_frame_bytes=8,**kwargs)
    instance.begin_recovery(frame(0).cursor, frame(3).cursor)
    return instance, applied


def test_checkpoint_high_water_overlap_applies_once_and_never_marks_replay_live():
    feed, applied = recovery()
    feed.accept_live(frame(3))
    feed.accept_live(frame(4))
    feed.replay_range([frame(1),frame(2),frame(2),frame(3)])
    assert feed.mode == FeedMode.WARMING
    assert feed.handoff_live(ready=True,lease_ns=10**9).sequence == 4
    assert applied == [(1,FeedMode.REPLAY),(2,FeedMode.REPLAY),(3,FeedMode.REPLAY),(4,FeedMode.WARMING)]
    assert not feed.accept_live(frame(4))
    feed.accept_live(frame(5))
    assert applied[-1] == (5,FeedMode.LIVE)


@pytest.mark.parametrize("fault", ["gap", "conflict", "incarnation", "overflow", "oversize", "unready"])
def test_faults_invalidate_instead_of_promoting_readiness(fault):
    feed, applied = recovery()
    with pytest.raises(RecoveryError):
        if fault == "gap":
            feed.replay_range([frame(2)])
        elif fault == "conflict":
            feed.replay_range([frame(1)])
            feed.accept_live(frame(1,b"changed"))
        elif fault == "incarnation":
            feed.accept_live(frame(4,incarnation="run2"))
        elif fault == "overflow":
            for i in range(4,9):
                feed.accept_live(frame(i))
        elif fault == "oversize":
            feed.accept_live(frame(4,b"123456789"))
        else:
            feed.replay_range([frame(1),frame(2),frame(3)])
            feed.handoff_live(ready=True,lease_ns=1)
    assert feed.mode == FeedMode.INVALID
    assert feed.stats['buffer_bytes'] == feed.stats['buffer_items'] == 0


def test_quiet_feed_lease_expires_with_virtual_monotonic_clock():
    now = [100]
    feed, _ = recovery(clock=lambda:now[0])
    feed.accept_live(frame(4))
    feed.replay_range([frame(1),frame(2),frame(3)])
    feed.handoff_live(ready=True,lease_ns=10)
    now[0] = 111
    assert not feed.validate_ready(ready=True,lease_ns=10)
    assert feed.mode == FeedMode.INVALID


def test_apply_failure_keeps_feed_invalid_with_no_later_replay():
    feed, _ = recovery()
    def fail(*args):
        raise RuntimeError("injected")
    feed.apply = fail
    with pytest.raises(RuntimeError,match="injected"):
        feed.replay_range([frame(1)])
    assert feed.mode == FeedMode.INVALID
    with pytest.raises(RecoveryError):
        feed.replay_range([frame(1)])


@pytest.mark.parametrize("value", [True, False, 1.0, float("inf"), float("nan"), 0, -1])
@pytest.mark.parametrize("field", ["max_items", "max_bytes", "max_frame_bytes"])
def test_recovery_bounds_require_finite_positive_integers(field, value):
    bounds = dict(max_items=4,max_bytes=16,max_frame_bytes=8)
    bounds[field] = value
    with pytest.raises(ValueError):
        FeedRecovery(lambda *args:None, **bounds)


def test_handoff_rechecks_feed_lease_after_state_application():
    now = [100]
    feed, _ = recovery(clock=lambda:now[0])
    feed.accept_live(frame(4))
    feed.replay_range([frame(1),frame(2),frame(3)])
    feed.apply = lambda *args: now.__setitem__(0,111)
    with pytest.raises(RecoveryError,match="expired_during_handoff"):
        feed.handoff_live(ready=True,lease_ns=10)
    assert feed.mode == FeedMode.INVALID
