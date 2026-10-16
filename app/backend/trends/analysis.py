# SPDX-License-Identifier: MIT
"""Trend analysis for a value time series: STL decomposition and change-point detection.

Pure, framework-free functions (no FastAPI/SQLAlchemy here) so they can be unit-tested
and reused outside the API. ``router.py`` adapts them to the ``/trends`` endpoints.

- STL (statsmodels) splits a monthly/quarterly series into trend, seasonal and remainder,
  so a classification of "going up/down" is not fooled by seasonality. Annual data (or a
  series too short for a seasonal cycle) falls back to a centred moving-average trend.
- ruptures (PELT, rbf cost) flags candidate change points: lasting shifts in the series'
  level. It finds *where* the series changed, not *why* — the caller still has to look at
  the surrounding data (or other dimensions) to explain a break.
"""
from __future__ import annotations

import re
from typing import Optional, TypedDict

import numpy as np

_MONTHLY = re.compile(r"^\d{4}-\d{2}$")
_QUARTERLY = re.compile(r"^\d{4}-Q\d$")
_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]

# Minimum points needed to say anything meaningful about a trend.
MIN_POINTS = 8

# Seasonal adjustment needs five full cycles of history (60 months, 20 quarters): with fewer, robust STL gives
# most months a remainder of exactly zero, so the "adjusted" series is the trend (measured on Luxembourg's
# trade series, see routers/insights.py ADJUST_MIN_MONTHS). The decomposition is always fitted on the whole
# history of the slice and cut to the span asked for: a span's first month is not an edge of the data.
MIN_CYCLES = 5


class TooShort(ValueError):
    """The history is too short (or too sparse) for a seasonal adjustment; the message says why, in words."""


class SeriesPoint(TypedDict):
    period: str
    value: float


def seasonal_period(periods: list[str]) -> Optional[int]:
    """The length of one seasonal cycle, guessed from the period labels (``"2024-03"`` -> 12
    monthly observations/year, ``"2024-Q1"`` -> 4). ``None`` for annual or mixed data: there is
    no cycle to remove."""
    if len(periods) >= 2 and all(_MONTHLY.match(p) for p in periods):
        return 12
    if len(periods) >= 2 and all(_QUARTERLY.match(p) for p in periods):
        return 4
    return None


def period_index(label: str) -> Optional[int]:
    """Consecutive integer of a monthly ("2024-03") or quarterly ("2024-Q1") label; None otherwise."""
    if _MONTHLY.match(label) and 1 <= int(label[5:7]) <= 12:
        return int(label[:4]) * 12 + int(label[5:7]) - 1
    if _QUARTERLY.match(label) and 1 <= int(label[-1]) <= 4:
        return int(label[:4]) * 4 + int(label[-1]) - 1
    return None


def stl_on_history(history: list[SeriesPoint], period: int) -> dict[str, tuple[float, float, float]]:
    """{period label: (trend, seasonal, remainder)} from one robust STL fit (``period``) of the whole
    ``history``; a period inside it with no row (no trade recorded) counts as zero.

    Raises TooShort when the history spans, or has non-zero values in, fewer than MIN_CYCLES cycles, and
    ValueError when the labels are not monthly or quarterly.
    """
    idx = [period_index(str(r["period"])) for r in history]
    if not history or any(i is None for i in idx):
        raise ValueError("A seasonal adjustment needs monthly or quarterly data")
    first = idx[0]
    n = idx[-1] - first + 1
    recorded = sum(1 for r in history if r["value"])
    need = MIN_CYCLES * period
    unit = "months" if period == 12 or _MONTHLY.match(str(history[0]["period"])) else "quarters"
    if min(n, recorded) < need:
        span = f"{history[0]['period']} to {history[-1]['period']}"
        what = (f"{n} {unit} of data ({span})" if n < need else
                f"trade recorded in only {recorded} of its {n} {unit} ({span})")
        raise TooShort(f"Too short to adjust: this selection has {what}; taking out the usual "
                       f"{'month-to-month' if unit == 'months' else 'quarter-to-quarter'} pattern needs at least "
                       f"{need} {unit} ({MIN_CYCLES} years).")
    values = np.zeros(n)
    for i, r in zip(idx, history):
        values[i - first] = float(r["value"])
    import warnings

    from statsmodels.tsa.seasonal import STL
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = STL(values, period=period, robust=True).fit()
    trend, seasonal, resid = (np.asarray(res.trend, float), np.asarray(res.seasonal, float), np.asarray(res.resid, float))
    return {str(r["period"]): (float(trend[i - first]), float(seasonal[i - first]), float(resid[i - first]))
            for i, r in zip(idx, history)}


def _moving_average_trend(values: np.ndarray, window: int) -> np.ndarray:
    """A centred moving average, same length as ``values`` (edges repeat the boundary value)."""
    if window <= 1 or len(values) < 2:
        return values.copy()
    kernel = np.ones(window) / window
    left = window // 2
    right = window - 1 - left
    padded = np.pad(values, (left, right), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _decompose(values: np.ndarray, period: Optional[int]) -> tuple[np.ndarray, Optional[np.ndarray], np.ndarray, str]:
    """(trend, seasonal, remainder, method). Seasonal is ``None`` when STL could not run."""
    n = len(values)
    if period and n >= 2 * period + 1:
        import pandas as pd
        from statsmodels.tsa.seasonal import STL

        res = STL(pd.Series(values), period=period, robust=True).fit()
        return res.trend.to_numpy(), res.seasonal.to_numpy(), res.resid.to_numpy(), "stl"
    window = max(3, min(5, n // 3 or 1))
    trend = _moving_average_trend(values, window)
    return trend, None, values - trend, "moving_average"


def _classify(change: float, relative_change: float, volatility: float) -> str:
    """Direction label from the trend's change between its first and last value, overridden by
    "volatile" when the remainder dwarfs the trend (the series does not move in one clear direction)."""
    if volatility > 0.35:
        return "volatile"
    if abs(relative_change) < 0.05:
        return "stable"
    return "increasing" if change > 0 else "decreasing"


def trend_summary(direction: str, shape: str) -> str:
    """The direction and shape of the trend as one plain English sentence (no figures), for prose
    that would otherwise read "the trend is increasing and decelerating"."""
    if direction == "volatile":
        return "Over the span the series moved up and down without one clear direction."
    if direction == "stable":
        return "Over the span the trend held roughly level."
    verb = "rose" if direction == "increasing" else "fell"
    how = {"accelerating": f"and it {verb} faster in the second half than in the first",
           "decelerating": f"but it {verb} more slowly in the second half than in the first",
           "reversing": "though it turned around midway",
           }.get(shape, "at a fairly steady pace")
    return f"Over the span the trend {verb}, {how}."


def _strength(resid: np.ndarray, component: np.ndarray) -> float:
    """Hyndman & Athanasopoulos' strength-of-component measure, in [0, 1]: how much of
    ``component + resid``'s variance is NOT remainder noise. 0 = the component explains
    nothing beyond noise; close to 1 = the remainder is negligible next to it.
    """
    combined_var = float(np.var(component + resid))
    if combined_var <= 1e-12:
        return 0.0
    return float(np.clip(1.0 - np.var(resid) / combined_var, 0.0, 1.0))


def _strength_label(strength: float) -> str:
    if strength >= 0.6:
        return "strong"
    if strength >= 0.3:
        return "moderate"
    return "weak"


def _shape(trend: np.ndarray) -> str:
    """How the trend's slope itself is changing: split it in half and compare the two halves'
    slopes. "reversing": they point opposite ways. Otherwise "accelerating"/"decelerating" when
    the second half moves clearly faster/slower than the first, else "steady" (roughly linear).
    """
    n = len(trend)
    if n < 8:
        return "steady"
    mid = n // 2
    x1, x2 = np.arange(mid, dtype=float), np.arange(n - mid, dtype=float)
    s1 = float(np.polyfit(x1, trend[:mid], 1)[0]) if mid > 1 else 0.0
    s2 = float(np.polyfit(x2, trend[mid:], 1)[0]) if (n - mid) > 1 else 0.0
    if abs(s1) < 1e-9 and abs(s2) < 1e-9:
        return "steady"
    if s1 * s2 < 0:
        return "reversing"
    ratio = abs(s2) / (abs(s1) or 1e-9)
    if ratio > 1.3:
        return "accelerating"
    if ratio < 0.7:
        return "decelerating"
    return "steady"


def _cycle_position(period_label: str, cycle: int) -> Optional[int]:
    """1-based position of ``period_label`` within its seasonal cycle (month 1-12, quarter 1-4)."""
    if cycle == 12 and _MONTHLY.match(period_label):
        return int(period_label[5:7])
    if cycle == 4 and _QUARTERLY.match(period_label):
        return int(period_label[-1])
    return None


def _cycle_label(position: int, cycle: int) -> str:
    if cycle == 12:
        return _MONTH_NAMES[position - 1]
    if cycle == 4:
        return f"Q{position}"
    return str(position)


def _effect_tag(pct: float) -> str:
    """A plain-language label for one point in the cycle, so a reader can scan the chart without
    doing the arithmetic themselves."""
    if pct >= 10:
        return "much higher than usual"
    if pct >= 3:
        return "higher than usual"
    if pct <= -10:
        return "much lower than usual"
    if pct <= -3:
        return "lower than usual"
    return "about typical"


def _long(point: dict) -> str:
    """A month written out in prose ("October", not "Oct"); quarters as they are."""
    label = str(point.get("label", ""))
    return _MONTH_LONG[_MONTH_NAMES.index(label)] if label in _MONTH_NAMES else label


def seasonality_summary(label: Optional[str], peak: Optional[dict], trough: Optional[dict]) -> Optional[str]:
    """One plain sentence a non-technical reader can act on, instead of reading the strength
    number and the bar chart themselves."""
    if label is None:
        return None
    if label == "weak":
        return ("No strong repeating pattern through the year for this selection — month-to-month "
                "swings look more like noise than a predictable yearly cycle.")
    word = "strong" if label == "strong" else "moderate"
    if peak and trough:
        return (f"A {word} repeating yearly pattern: {_long(peak)} is typically the highest point "
                 f"({peak['tag']}), {_long(trough)} the lowest ({trough['tag']}).")
    return f"A {word} repeating yearly pattern."


def seasonal_pattern(periods: list[str], seasonal: Optional[np.ndarray], cycle: Optional[int],
                      typical_level: float) -> list[dict]:
    """The typical shape of one cycle: the seasonal component's average effect at each position
    (e.g. "December is usually above trend"), independent of which particular year. Also
    expressed as a percentage of the series' own typical level, which reads more plainly than a
    raw euro amount ("12% below average" vs. "-158,304,941") — and a one-line tag for the same
    reason.
    """
    if seasonal is None or not cycle:
        return []
    buckets: dict[int, list[float]] = {}
    for p, s in zip(periods, seasonal):
        pos = _cycle_position(p, cycle)
        if pos is not None:
            buckets.setdefault(pos, []).append(float(s))
    base = typical_level or 1.0
    out = []
    for pos, vals in sorted(buckets.items()):
        avg = float(np.mean(vals))
        pct = 100 * avg / base
        out.append({"position": pos, "label": _cycle_label(pos, cycle), "average_effect": avg,
                     "average_effect_pct": pct, "tag": _effect_tag(pct)})
    return out


def detect_changepoints(values: np.ndarray, min_size: int = 6, penalty: float = 3.0) -> list[int]:
    """Indices (into ``values``) of candidate level shifts, via ruptures' PELT search.

    The series is standardised first so ``penalty`` behaves the same regardless of the
    unit/scale of the values (euros vs a share, millions vs units).
    """
    n = len(values)
    if n < 2 * min_size:
        return []
    std = float(values.std())
    signal = (values - values.mean()) / (std or 1.0)

    import ruptures as rpt

    algo = rpt.Pelt(model="rbf", min_size=min_size).fit(signal.reshape(-1, 1))
    bkps = algo.predict(pen=penalty * np.log(n))
    return [b for b in bkps if 0 < b < n]  # ruptures' last breakpoint is always n (end of series)


def analyze_series(rows: list[SeriesPoint], stl_period: Optional[int] = None, min_size: int = 6,
                    penalty: float = 3.0, history: Optional[list[SeriesPoint]] = None) -> dict:
    """STL decomposition (or a moving-average fallback) plus change-point detection on a series
    of ``{"period", "value"}`` rows, as returned by ``/insights/series``.

    The decomposition is fitted on ``history`` (the slice's whole history; ``rows`` when not given), of
    which ``rows`` is a span, then cut to that span; everything else (direction, shape, the seasonal
    pattern, the change points) is about the span. Under MIN_CYCLES cycles of history there is no seasonal
    adjustment: a moving-average trend, ``seasonal`` None and ``adjustment_note`` saying why.

    Change points are searched on the span's own values (standardised, not seasonally adjusted): they
    answer "did the level shift for good within the period shown", and PELT's penalty and the
    standardisation are relative to the series searched, so a search over decades would judge a shift in
    the last three years against the whole history's spread and growth.
    """
    periods = [r["period"] for r in rows]
    values = np.asarray([float(r["value"]) for r in rows], dtype=float)
    n = len(values)

    period = stl_period if stl_period is not None else seasonal_period(periods)
    history = rows if history is None else history
    note: Optional[str] = None
    fit_span: Optional[tuple[str, str]] = None
    if period and n:
        try:
            fit = stl_on_history(history, period)
            parts = np.asarray([fit[p] for p in periods], float)
            trend, seasonal, resid, method = parts[:, 0], parts[:, 1], parts[:, 2], "stl"
            fit_span = (str(history[0]["period"]), str(history[-1]["period"]))
        except TooShort as exc:
            note = str(exc)
            trend, seasonal, resid, method = _decompose(values, None)
    else:
        trend, seasonal, resid, method = _decompose(values, period)

    x = np.arange(n, dtype=float)
    slope = float(np.polyfit(x, trend, 1)[0]) if n > 1 else 0.0
    mean_level = float(np.mean(np.abs(trend))) or 1.0
    # The change of the trend itself from its first to its last value: the trend is already smoothed,
    # so its two ends are not noisy, and the figure is what a reader sees on the chart. (A linear fit's
    # change over the span divided by the mean level could print -106% for a trend that rose from
    # 5 to 74 million with a hump in between: below -100% is impossible.) The direction label is
    # derived from the same change, so the two always agree.
    trend_start, trend_end = (float(trend[0]), float(trend[-1])) if n else (0.0, 0.0)
    change = trend_end - trend_start
    relative_change: Optional[float] = change / trend_start if trend_start > 0 else None
    volatility = float(np.std(resid) / mean_level)
    direction = _classify(change, relative_change if relative_change is not None else change / mean_level, volatility)
    shape = _shape(trend)

    trend_strength = _strength(resid, trend)
    seasonal_strength = _strength(resid, seasonal) if seasonal is not None else 0.0
    typical_level = float(np.mean(np.abs(values))) or 1.0
    pattern = seasonal_pattern(periods, seasonal, period, typical_level)
    peak = max(pattern, key=lambda r: r["average_effect"]) if pattern else None
    trough = min(pattern, key=lambda r: r["average_effect"]) if pattern else None

    changepoint_idx = detect_changepoints(values, min_size=min_size, penalty=penalty)
    changepoints = []
    for idx in changepoint_idx:
        before = values[max(0, idx - min_size):idx]
        after = values[idx:idx + min_size]
        if len(before) == 0 or len(after) == 0:
            continue
        before_mean, after_mean = float(before.mean()), float(after.mean())
        changepoints.append({
            "period": periods[idx],
            "index": idx,
            "before_mean": before_mean,
            "after_mean": after_mean,
            "change": after_mean - before_mean,
            "relative_change": (after_mean - before_mean) / before_mean if before_mean else None,
        })

    # "Seasonally adjusted": the raw value with the repeating yearly pattern taken out (trend +
    # remainder), so a reader can tell a real dip from an ordinary low-season month.
    points = [
        {"period": p, "value": float(v), "trend": float(t), "resid": float(r),
         "seasonal": float(s) if seasonal is not None else None,
         "adjusted": float(v - s) if seasonal is not None else None}
        for p, v, t, r, s in zip(periods, values, trend, resid, seasonal if seasonal is not None else [None] * n)
    ]
    seasonality_label = _strength_label(seasonal_strength) if seasonal is not None else None
    return {
        "method": method,
        "seasonal_period": period,
        "fit_start": fit_span[0] if fit_span else None,
        "fit_end": fit_span[1] if fit_span else None,
        "adjustment_note": note,
        "direction": direction,
        "shape": shape,
        "slope": slope,
        "relative_change": relative_change,
        "trend_start": trend_start,
        "trend_end": trend_end,
        "trend_summary": trend_summary(direction, shape),
        "volatility": volatility,
        "trend_strength": trend_strength,
        "seasonal_strength": seasonal_strength,
        "seasonality_label": seasonality_label,
        "seasonality_summary": seasonality_summary(seasonality_label, peak, trough),
        "seasonal_pattern": pattern,
        "seasonal_peak": peak,
        "seasonal_trough": trough,
        "points": points,
        "changepoints": changepoints,
    }
