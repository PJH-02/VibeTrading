"""R05 parity against the frozen pre-extraction calculation oracle."""
import copy
from pathlib import Path
import runpy

import numpy as np
import pandas as pd
import pytest

import vibetrading.etf_history as etf_history_module
from vibetrading.etf_history import EtfHistory
from vibetrading.etf_trading import intraday_decision as legacy_intraday_decision
from vibetrading.strategies.kr import kd, kp
from vibetrading.strategies.kr._intraday import intraday_decision as pure_intraday_decision
from vibetrading.strategies.kr.c3 import open_decision


ORACLE = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / 'docs' / 'tickets' / 'R05-before-oracle.py')
)
ORACLE_INTRADAY = ORACLE['intraday_decision']
ORACLE_C3 = ORACLE['c3_open_decision']


def feature(**changes):
    base = {
        'usable': True,
        'entry_signal': 1,
        'vol14': .01,
        'signal_strength': 1.2,
        'upper_band': 102.,
        'lower_band': 98.,
        'vwap_used': 99.,
        'price': 100.,
    }
    base.update(changes)
    return base


def book(direction=0, positions=None, entries=0):
    return {'direction': direction, 'positions': {} if positions is None else positions, 'entries': entries}


def _outcome(call):
    try:
        return 'return', repr(call())
    except BaseException as exc:  # Compare exact exception type and message.
        return 'raise', type(exc), str(exc)


def _assert_intraday_parity(strategy, item_feature, item_book):
    frozen_feature, frozen_book = copy.deepcopy(item_feature), copy.deepcopy(item_book)
    expected = ORACLE_INTRADAY(strategy, item_feature, item_book)
    assert pure_intraday_decision(strategy, item_feature, item_book) == expected
    if strategy == 'KP':
        assert kp.decide(item_feature, item_book) == expected
    elif strategy == 'KD':
        assert kd.decide(item_feature, item_book) == expected
    assert legacy_intraday_decision(strategy, item_feature, item_book) == expected
    assert (item_feature, item_book) == (frozen_feature, frozen_book)


INTRADAY_CASES = [
    ('KP', feature(entry_signal=1), book(), 'kp-flat-long-entry'),
    ('KP', feature(entry_signal=-1), book(), 'kp-flat-short-signal-no-entry'),
    ('KD', feature(entry_signal=-1), book(), 'kd-flat-short-entry'),
    ('KD', feature(entry_signal=1), book(), 'kd-flat-long-entry'),
    ('C3', feature(entry_signal=-1), book(), 'other-flat-short-entry'),
    ('KP', feature(entry_signal=1, vol14=.02, signal_strength=.8), book(), 'kp-sizing-below-cap'),
    ('KP', feature(entry_signal=1, vol14=.001, signal_strength=2.), book(), 'kp-sizing-at-cap'),
    ('KD', feature(entry_signal=1, vol14=.001, signal_strength=2.), book(), 'kd-sizing-at-cap'),
    ('C3', feature(entry_signal=1, vol14=.005, signal_strength=1.5), book(), 'other-sizing-at-cap'),
    ('KP', feature(entry_signal=0), book(), 'signal-zero-no-entry'),
    ('KD', feature(entry_signal=0), book(), 'kd-signal-zero-no-entry'),
    ('KP', feature(usable=False), book(), 'unusable-no-entry'),
    ('KD', feature(entry_signal=-1, usable=False), book(), 'kd-unusable-no-entry'),
    ('KP', feature(entry_signal=1), book(entries=1), 'already-entered-no-entry'),
    ('KD', feature(entry_signal=-1), book(entries=1), 'kd-already-entered-no-entry'),
    ('KP', feature(price=102.), book(1, {'069500': 1}), 'kp-long-band-boundary-stop'),
    ('KP', feature(price=101.999), book(1, {'069500': 1}), 'kp-long-below-stop'),
    ('KP', feature(price=102.001), book(1, {'069500': 1}), 'kp-long-hold'),
    ('KD', feature(price=98.), book(-1, {'251340': 1}), 'kd-short-band-boundary-stop'),
    ('KD', feature(price=98.001), book(-1, {'251340': 1}), 'kd-short-above-stop'),
    ('KD', feature(price=97.999), book(-1, {'251340': 1}), 'kd-short-hold'),
    ('C3', feature(upper_band=95., vwap_used=102., price=102.),
     book(1, {'069500': 1}), 'other-long-vwap-boundary-stop'),
    ('KP', feature(lower_band=105., vwap_used=98., price=98.),
     book(-1, {'069500': 1}), 'kp-short-vwap-boundary-stop'),
    ('KD', feature(entry_signal=-1), book(0, {'069500': 1}), 'positions-with-zero-direction'),
]


@pytest.mark.parametrize('strategy,item_feature,item_book', [(s, f, b) for s, f, b, _ in INTRADAY_CASES],
                         ids=[case[3] for case in INTRADAY_CASES])
def test_intraday_decisions_match_frozen_oracle(strategy, item_feature, item_book):
    _assert_intraday_parity(strategy, item_feature, item_book)


@pytest.mark.parametrize('strategy', ['KP', 'KD', 'C3'])
@pytest.mark.parametrize('missing,state', [
    ('entry_signal', 'flat'),
    ('vol14', 'flat'),
    ('signal_strength', 'flat'),
    ('entries', 'flat-book'),
    ('upper_band', 'long'),
    ('vwap_used', 'long'),
    ('price', 'long'),
    ('lower_band', 'short'),
    ('direction', 'missing-book-direction'),
    ('positions', 'missing-book-positions'),
])
def test_intraday_missing_keys_match_frozen_oracle(strategy, missing, state):
    item_feature = feature()
    item_book = book()
    if state == 'long':
        item_book = book(1, {'069500': 1})
    elif state == 'short':
        item_book = book(-1, {'069500': 1})
    elif state == 'missing-book-direction':
        del item_book['direction']
    elif state == 'missing-book-positions':
        del item_book['positions']
    if missing in {'direction', 'positions'}:
        pass
    elif missing == 'entries':
        del item_book['entries']
    else:
        del item_feature[missing]
    assert _outcome(lambda: pure_intraday_decision(strategy, item_feature, item_book)) == \
        _outcome(lambda: ORACLE_INTRADAY(strategy, item_feature, item_book))
    assert _outcome(lambda: legacy_intraday_decision(strategy, item_feature, item_book)) == \
        _outcome(lambda: ORACLE_INTRADAY(strategy, item_feature, item_book))


@pytest.mark.parametrize('strategy', ['KP', 'KD', 'C3'])
@pytest.mark.parametrize('vol14,raises', [
    (0., True),
    (-.01, False),
    (None, True),
    ('bad', True),
])
def test_intraday_invalid_volatility_matches_frozen_oracle(strategy, vol14, raises):
    item_feature, item_book = feature(vol14=vol14), book()
    expected = _outcome(lambda: ORACLE_INTRADAY(strategy, item_feature, item_book))
    assert _outcome(lambda: pure_intraday_decision(strategy, item_feature, item_book)) == expected
    assert _outcome(lambda: legacy_intraday_decision(strategy, item_feature, item_book)) == expected
    assert (expected[0] == 'raise') is raises


@pytest.mark.parametrize('strategy', ['KP', 'KD', 'C3'])
def test_intraday_nonfinite_volatility_matches_frozen_oracle(strategy):
    item_feature, item_book = feature(vol14=float('nan')), book()
    expected = _outcome(lambda: ORACLE_INTRADAY(strategy, item_feature, item_book))
    assert _outcome(lambda: pure_intraday_decision(strategy, item_feature, item_book)) == expected
    assert _outcome(lambda: legacy_intraday_decision(strategy, item_feature, item_book)) == expected


C3_THRESHOLD_CASES = [
    (z, mis, sigma)
    for z in (.999999, 1., 1.000001, 2.)
    for mis in (-1e-9, 0., 1e-9)
    for sigma in (.1, .15, .1500001, .3, 1.)
]


@pytest.mark.parametrize('z,mis,sigma', C3_THRESHOLD_CASES)
def test_c3_threshold_sizing_and_cap_match_frozen_oracle(z, mis, sigma):
    expected = ORACLE_C3(z, mis, sigma)
    actual = open_decision(z, mis, sigma)
    assert actual == expected
    assert type(actual) is tuple and type(actual[0]) is float
    assert list(actual[1]) == ['z', 'mis', 'sigma']
    assert actual[1] == {'z': z, 'mis': mis, 'sigma': sigma}


@pytest.mark.parametrize('z,mis,sigma', [
    (np.nan, 0., .2),
    (np.inf, 0., .2),
    (-np.inf, 0., .2),
    (1., np.nan, .2),
    (1., np.inf, .2),
    (1., 0., np.nan),
    (1., 0., np.inf),
    (1., 0., 0.),
    (1., 0., -0.1),
])
def test_c3_guard_failures_match_frozen_oracle(z, mis, sigma):
    with pytest.raises(RuntimeError) as frozen:
        ORACLE_C3(z, mis, sigma)
    with pytest.raises(RuntimeError) as actual:
        open_decision(z, mis, sigma)
    assert type(actual.value) is type(frozen.value)
    assert str(actual.value) == str(frozen.value)


def _c3_history():
    etf_dates = pd.bdate_range(end='2026-10-12', periods=260)
    us_dates = pd.bdate_range(end='2026-10-12', periods=280)
    etf = pd.DataFrame({
        'timestamp': etf_dates,
        'open': 100. + np.sin(np.arange(len(etf_dates)) / 3.),
        'close': 101. + np.cos(np.arange(len(etf_dates)) / 7.),
    })
    index = pd.DataFrame({
        'timestamp': etf_dates,
        'open': 200. + np.sin(np.arange(len(etf_dates)) / 5.),
        'close': 201. + np.cos(np.arange(len(etf_dates)) / 11.),
    })
    history = EtfHistory.__new__(EtfHistory)
    history.daily = {'069500': etf}
    history.index = index
    history.us = pd.Series(100. + np.sin(np.arange(len(us_dates)) / 4.), index=us_dates)
    history.day = '2026-10-13'
    return history


def test_etf_history_c3_delegates_exact_inputs_to_pure_open_decision(monkeypatch):
    history = _c3_history()
    captured = {}
    real_open_decision = etf_history_module.open_decision

    def spy(z, mis, sigma):
        captured.update(z=z, mis=mis, sigma=sigma)
        return real_open_decision(z, mis, sigma)

    monkeypatch.setattr(etf_history_module, 'open_decision', spy)
    monkeypatch.setattr('vibetrading.etf_csv_sync.latest_completed_session',
                        lambda name, now, grace_minutes=10: str(history.us.index.max().date()))
    leverage, detail = history.c3(100., 200.)
    assert set(captured) == {'z', 'mis', 'sigma'}
    assert all(np.isfinite(list(captured.values())))
    assert captured['sigma'] > 0
    expected = ORACLE_C3(captured['z'], captured['mis'], captured['sigma'])
    assert (leverage, detail) == expected
    assert (leverage, detail) == open_decision(captured['z'], captured['mis'], captured['sigma'])
