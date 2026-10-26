"""Original Future_Trading feature formulas, vendored by prepare_etf_reference.py."""

import numpy as np
import pandas as pd

FIELDS = ["open", "high", "low", "close", "volume"]

LOOKBACK, VOL_TARGET = 14, .02

TIMES = pd.date_range("2000-01-01 09:30", "2000-01-01 15:00", freq="30min").strftime("%H:%M").tolist()

MARKETS = {"KOSPI200": {"signal": "069500", "long": ("069500", "122630"), "short": (), "ceiling": 2.}, "KOSDAQ150": {"signal": "229200", "long": ("229200",), "short": ("251340",), "ceiling": 1.}}

def validate(x):
    if x.empty:
        return
    if x.timestamp.isna().any() or not np.isfinite(x[FIELDS].to_numpy(dtype=float)).all():
        raise ValueError("Invalid timestamps/nonfinite OHLCV")
    if (x[["open", "high", "low", "close"]] <= 0).any().any() or (x.volume < 0).any():
        raise ValueError("Nonpositive prices or negative volume")
    if (x.high < x[["open", "low", "close"]].max(axis=1)).any() or (x.low > x[["open", "close"]].min(axis=1)).any():
        raise ValueError("Invalid OHLC range")

def unique_bars(x):
    if x.empty:
        return x
    if x.groupby("timestamp")[FIELDS].nunique().gt(1).any().any():
        raise ValueError("Conflicting overlapping bars within a provider")
    return x.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

def aggregate_30m(minutes):
    """Exact 09:00..15:00 windows; no fill, no auction, no partial windows."""
    if minutes.empty:
        return minutes.copy()
    x = unique_bars(minutes)
    x = x[x.timestamp.dt.strftime("%H:%M").between("09:01", "15:00")].copy()
    x["endpoint"] = x.timestamp.dt.ceil("30min")
    rows = []
    for end, g in x.groupby("endpoint", sort=True):
        expected = pd.date_range(end - pd.Timedelta(minutes=29), end, freq="1min")
        if len(g) != 30 or not g.timestamp.reset_index(drop=True).equals(pd.Series(expected)):
            continue
        rows.append({"timestamp": end, "date": end.strftime("%Y-%m-%d"), "open": g.open.iloc[0], "high": g.high.max(), "low": g.low.min(), "close": g.close.iloc[-1], "volume": g.volume.sum(), "trading_value": g.trading_value.sum() if g.trading_value.notna().all() else np.nan, "symbol": g.symbol.iloc[0], "source": g.source.iloc[0], "bar_origin": "aggregated_1m", "timestamp_semantics": "bar_end"})
    return pd.DataFrame(rows, columns=["timestamp", "date", *FIELDS, "trading_value", "symbol", "source", "bar_origin", "timestamp_semantics"])

def make_features(minutes, daily):
    """Original 30m Noise/VWAP from complete Toss minute windows; no look-ahead."""
    dd = daily.set_index("date").sort_index()
    bars = aggregate_30m(minutes)
    bars["time"] = bars.timestamp.dt.strftime("%H:%M")
    bars["slot"] = bars.timestamp.dt.hour * 60 + bars.timestamp.dt.minute - 541
    grid = bars.pivot(index="date", columns="time", values="close").reindex(index=dd.index, columns=TIMES)
    noise = grid.div(dd.open, axis=0).sub(1).abs().shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).mean()
    vol = dd.close.pct_change(fill_method=None).rolling(LOOKBACK, min_periods=LOOKBACK).std(ddof=1).shift(1)
    x = bars[bars.date.isin(dd.index)].copy()
    valid = x.trading_value.notna() & np.isfinite(x.trading_value) & x.trading_value.ge(0)
    implied = x.trading_value / x.volume.replace(0, np.nan)
    if (valid & x.volume.gt(0) & ((implied < x.low - 1e-6) | (implied > x.high + 1e-6))).any():
        raise ValueError("Minute trading_value units/range invalid")
    denom = x.volume.groupby(x.date).cumsum().replace(0, np.nan)
    proxy = (((x.high + x.low + x.close) / 3) * x.volume).groupby(x.date).cumsum() / denom
    actual = valid.groupby(x.date).cummin()
    real = x.trading_value.fillna(0).groupby(x.date).cumsum() / denom
    x["vwap_proxy"], x["vwap_actual"] = proxy, real.where(actual)
    x["vwap_used"] = real.where(actual, proxy)
    x["vwap_kind"] = np.where(actual, "actual", "30m_HLC3_proxy")
    x = x[x.time.isin(TIMES)].copy()
    x["noise"] = noise.to_numpy()[dd.index.get_indexer(x.date), pd.Index(TIMES).get_indexer(x.time)]
    x["open_day"], x["previous_close"], x["vol14"] = x.date.map(dd.open), x.date.map(dd.close.shift(1)), x.date.map(vol)
    highanchor = x[["open_day", "previous_close"]].max(axis=1)
    lowanchor = x[["open_day", "previous_close"]].min(axis=1)
    x["upper_band"], x["lower_band"] = highanchor * (1 + x.noise), lowanchor * (1 - x.noise)
    x["usable"] = np.isfinite(x[["noise", "vol14", "previous_close", "vwap_used"]]).all(axis=1) & x.noise.gt(0) & x.vol14.gt(0)
    x["price"] = x.close
    x["entry_signal"] = np.where(x.usable & x.close.gt(x.upper_band), 1, np.where(x.usable & x.close.lt(x.lower_band), -1, 0))
    x["signal_strength"] = np.where(x.entry_signal.eq(1), (x.close / highanchor - 1) / x.noise, np.where(x.entry_signal.eq(-1), (1 - x.close / lowanchor) / x.noise, np.nan))
    x["strength_bucket"] = np.where(x.entry_signal.eq(0), "none", np.where(x.signal_strength < 1.25, "1.00-1.25", np.where(x.signal_strength < 1.5, "1.25-1.50", "1.50+")))
    # Research controls: strictly prior daily information and completed opening range.
    prior_ma = dd.close.rolling(20, min_periods=20).mean().shift(1)
    prior_close = dd.close.shift(1)
    trend = np.sign(prior_close - prior_ma).where(prior_ma.notna())
    median_range = ((dd.high-dd.low)/dd.open).shift(1).rolling(20, min_periods=20).median()
    opening = minutes[minutes.time.between("09:01", "09:10")].groupby("date")
    opening_high, opening_low = opening.high.max(), opening.low.min()
    complete_opening = opening.size().eq(10)
    x["prior_ma20_direction"] = x.date.map(trend)
    x["prior_median_range20"] = x.date.map(median_range)
    x["or10_high"] = x.date.map(opening_high.where(complete_opening))
    x["or10_low"] = x.date.map(opening_low.where(complete_opening))
    return x.reset_index(drop=True)

def weights(direction, leverage, market):
    vehicles = MARKETS[market]["long" if direction == 1 else "short"]
    if leverage <= 1:
        return {vehicles[0]: leverage}
    if len(vehicles) != 2:
        raise AssertionError("KOSDAQ leverage not allowed")
    return {vehicles[0]: 2 - leverage, vehicles[1]: leverage - 1}

def rolling_z1(g, x, win=250, minr=200):
    """단일 회귀변수 닫힌 해. T 제외 직전 win 행 중 g·x 유효 행 n ≥ minr 이면 OLS, s = sqrt(SSR/(n−2)), z = 잔차(T)/s."""
    G, X = g.to_numpy(float), x.to_numpy(float)
    ok = ~np.isnan(G) & ~np.isnan(X)
    g0, x0, o = np.where(ok, G, 0.0), np.where(ok, X, 0.0), ok.astype(float)
    cs = [np.concatenate([[0.0], np.cumsum(v)]) for v in (o, x0, g0, x0 * x0, x0 * g0, g0 * g0)]
    i = np.arange(len(G))
    lo = np.maximum(0, i - win)
    n, sx, sy, sxx, sxy, syy = [c[i] - c[lo] for c in cs]
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, my = sx / n, sy / n
        vxx, vxy, vyy = sxx - n * mx * mx, sxy - n * mx * my, syy - n * my * my
        b = vxy / vxx
        a = my - b * mx
        z = (G - a - b * X) / np.sqrt((vyy - b * vxy) / (n - 2))
    z[~ok | (n < minr)] = np.nan
    return pd.Series(z, index=g.index)
