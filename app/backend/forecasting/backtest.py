# SPDX-License-Identifier: MIT
"""Rolling-origin backtest: how far off each method was, on this very series, before we trust it.

At every origin (the end of a past quarter) each method is fitted on the months up to the origin
only, and forecasts the next 12 months; the forecasts are compared with what happened. Scores:

- MASE: the mean absolute error divided by the in-sample mean absolute error of the seasonal
  naive forecast (the same month a year earlier), so 1.0 is "as good as the seasonal naive" and
  0.8 is 20% better. Comparable across series of any size.
- MAPE: the mean absolute percentage error (a fraction: 0.05 is 5%).
- coverage: the share of actual values that fell inside the interval, where the interval at each
  origin was calibrated only on the errors already known at that origin (split conformal, run
  forward in time), so it is an honest out-of-sample number.
- annual MAPE: the calendar year forecast from the December before it.

The intervals of the live forecast are calibrated the same way, on every backtest error
(conformal prediction): an 80% band is where 80% of the method's past errors at that horizon
fell. No distributional assumption, and the same for every method.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from . import models, pool

SEASON = models.SEASON
HORIZONS = (1, 3, 6, 12)
ORIGINS = 20          # the last 20 quarter ends (five years) with at least one month after them
MIN_TRAIN = 60        # months a model is fitted on, at least
MIN_CALIBRATION = 6   # errors needed before an interval is scored (coverage)
GBM_REFIT = 12        # months between two trainings of the gradient boosting model


def schedule(periods: list[str], extra: tuple[int, ...] = ()) -> list[int]:
    """Origins (indices into ``periods``): the last ``ORIGINS`` quarter ends with at least one
    observed month after them, plus ``extra`` (e.g. the December before a year to test)."""
    n = len(periods)
    ends = [i for i in range(MIN_TRAIN - 1, n - 1) if int(periods[i][5:7]) % 3 == 0]
    return sorted(set(ends[-ORIGINS:]) | {o for o in extra if MIN_TRAIN - 1 <= o < n - 1})


def run(z: np.ndarray, start: str, method: str, origins: list[int],
        known: Optional[dict] = None) -> dict[int, np.ndarray]:
    """{origin: forecast of the next 12 months on the model's scale} for one method.

    ``known`` holds the forecasts of the other methods already run, for the ensemble.
    """
    known = known or {}
    out: dict[int, np.ndarray] = {}
    if method == "ensemble":
        for o in origins:
            parts = [known[m][o] if m in known and o in known[m] else run(z, start, m, [o])[o] for m in models.ENSEMBLE]
            out[o] = np.mean(np.vstack(parts), axis=0)
        return out
    if method == "gbm":
        s0 = models.month_index(start)
        local, fitted_at = None, None
        batches: dict[int, tuple[object, list[int]]] = {}  # one predict call per model: it is the slow part
        for o in origins:
            model = pool.model(pool.cutoff_for(models.period_of(s0 + o)))
            if model is None:                           # no pool: train on the series, once a year
                if local is None or o - fitted_at >= GBM_REFIT:
                    local, fitted_at = models.gbm_fit(z[:o + 1], start), o
                model = local
            if model is None or o + 1 < 2 * SEASON:
                out[o] = models.snaive(z[:o + 1], SEASON)
                continue
            batches.setdefault(id(model), (model, []))[1].append(o)
        ks = np.arange(1, SEASON + 1)
        for model, os_ in batches.values():
            cleaned = [models.clean_breaks(z[:o + 1], start) for o in os_]
            X = np.vstack([models.features(c, s0, np.full(SEASON, len(c) - 1), ks) for c in cleaned])
            pred = model.predict(X).reshape(len(os_), SEASON)
            for o, c, p in zip(os_, cleaned, pred):
                out[o] = c[len(c) - 1 + ks - SEASON] + p
        return out
    for o in origins:
        out[o] = np.asarray(models.METHODS[method](z[:o + 1], SEASON, start), float)
    return out


def _quantile(xs: list[float], prob: float) -> Optional[float]:
    """The conformal quantile: the ceil((n + 1) prob)-th smallest error (finite-sample valid)."""
    if not xs:
        return None
    xs = sorted(xs)
    k = int(np.ceil((len(xs) + 1) * prob)) - 1
    return float(xs[min(k, len(xs) - 1)])


def errors(values: np.ndarray, z: np.ndarray, log: bool, fc: dict[int, np.ndarray]) -> dict:
    """Errors of one method: per horizon (origin, |error| on the model's scale) and per window of
    the next k months (origin, |error| of the sum, on the log scale when the series is logged)."""
    n = len(values)
    by_h: dict[int, list] = {h: [] for h in range(1, SEASON + 1)}
    by_k: dict[int, list] = {k: [] for k in range(1, SEASON + 1)}
    for o, f in sorted(fc.items()):
        levels = models.untransform(f, log)
        for h in range(1, SEASON + 1):
            if o + h >= n:
                break
            by_h[h].append((o, abs(float(z[o + h] - f[h - 1]))))
            actual, guess = float(values[o + 1:o + h + 1].sum()), float(levels[:h].sum())
            if log and actual > 0 and guess > 0:
                by_k[h].append((o, abs(np.log(actual) - np.log(guess))))
            elif not log:
                by_k[h].append((o, abs(actual - guess)))
    return {"h": by_h, "k": by_k}


def half_widths(err: dict, prob: float) -> tuple[list[Optional[float]], list[Optional[float]]]:
    """Half-widths of the ``prob`` band for months 1..12 ahead and for sums of the next 1..12
    months, never narrower further ahead."""
    def run_max(xs):
        out, best = [], None
        for x in xs:
            best = x if best is None else (best if x is None else max(best, x))
            out.append(best)
        return out
    h = run_max([_quantile([e for _, e in err["h"][k]], prob) for k in range(1, SEASON + 1)])
    k = run_max([_quantile([e for _, e in err["k"][j]], prob) for j in range(1, SEASON + 1)])
    return h, k


def score(values: np.ndarray, periods: list[str], z: np.ndarray, log: bool, fc: dict[int, np.ndarray],
          prob: float) -> dict:
    """The scores of one method over its backtest forecasts ``fc``."""
    n = len(values)
    ape: dict[int, list] = {h: [] for h in range(1, SEASON + 1)}
    ase: dict[int, list] = {h: [] for h in range(1, SEASON + 1)}
    hits: list[bool] = []
    seen: list[tuple[int, int, float]] = []          # (origin, h, |error|) in order of origin
    annual: list[tuple[str, float]] = []
    for o, f in sorted(fc.items()):
        levels = models.untransform(f, log)
        scale = float(np.mean(np.abs(values[SEASON:o + 1] - values[:o + 1 - SEASON]))) or 1.0
        for h in range(1, SEASON + 1):
            if o + h >= n:
                break
            y = float(values[o + h])
            if y:
                ape[h].append(abs(float(levels[h - 1]) - y) / abs(y))
            ase[h].append(abs(float(levels[h - 1]) - y) / scale)
            e = abs(float(z[o + h] - f[h - 1]))
            past = [x for (oo, hh, x) in seen if hh == h and oo + hh <= o]
            if len(past) >= MIN_CALIBRATION:
                hits.append(e <= _quantile(past, prob))
            seen.append((o, h, e))
        if periods[o].endswith("-12") and o + SEASON < n:
            actual = float(values[o + 1:o + SEASON + 1].sum())
            if actual:
                annual.append((periods[o + 1][:4], abs(float(levels[:SEASON].sum()) - actual) / abs(actual)))

    def mean(xs):
        return float(np.mean(xs)) if xs else None
    out = {f"mase_h{h}": mean(ase[h]) for h in HORIZONS}
    out.update({f"mape_h{h}": mean(ape[h]) for h in HORIZONS})
    out.update({f"n_h{h}": len(ase[h]) for h in HORIZONS})   # origins scored at each horizon
    out["mase"] = mean([mean(ase[h]) for h in range(1, SEASON + 1) if ase[h]])
    out["mape"] = mean([mean(ape[h]) for h in range(1, SEASON + 1) if ape[h]])
    out["coverage"] = mean(hits)
    out["coverage_n"] = len(hits)
    out["annual_mape"] = mean([e for _, e in annual])
    out["annual"] = [{"period": p, "ape": e} for p, e in annual]
    return out


def naive_year(values: np.ndarray, periods: list[str], year: str) -> Optional[float]:
    """The error of "the same as last year" on a calendar year, as a fraction."""
    index = {p: i for i, p in enumerate(periods)}
    if f"{year}-01" not in index or f"{year}-12" not in index or f"{int(year) - 1}-01" not in index:
        return None
    o = index[f"{year}-01"] - 1
    actual = float(values[o + 1:o + 13].sum())
    return abs(float(values[o - 11:o + 1].sum()) - actual) / abs(actual) if actual else None
