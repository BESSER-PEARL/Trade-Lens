# SPDX-License-Identifier: MIT
"""The model card of a forecast: what the page's "Model card" shows next to a forecast chart.

Two tests, never mixed:

- ``study``: the offline rolling-origin backtest that chose the default method (66 quarter ends,
  December 2009 to March 2026, 12 months ahead, the totals of exports and imports). Its figures are
  the tables of docs/forecasting.md and docs/forecasting_nn.md, copied here so the card and the
  docs say the same thing; ``scripts/backtest_nn.py`` reruns it (minutes).
- ``live``: the backtest every request runs on the series being forecast (the last 20 quarter
  ends, ``backtest.py``), from the response's own ``backtest``.

Everything else (the members, the features, the training span, the band) is read from the code
(``models``, ``pool``, ``backtest``), so the card follows the models when they change.
"""
from __future__ import annotations

from typing import Optional

from . import backtest, models, nn, pool

# --------------------------------------------------------------------------- the study (offline)
STUDY = {
    "origins": 66, "first_origin": "2009-12", "last_origin": "2026-03", "step": "quarter", "horizon": 12,
    "series": "the monthly totals of exports and of imports, against the world",
    "forecasts": "about 1,500 forecasts per method",
    "score": "MASE: the mean absolute error divided by the in-sample mean absolute error of the seasonal naive "
             "forecast (1.0 = as good as repeating the same month a year earlier)",
}
# (key, name, MASE at 1, 3, 6, 12 months, MASE averaged over the 12 horizons, run, note)
# run "main": docs/forecasting.md; run "nn": docs/forecasting_nn.md (same protocol, rerun with the networks)
_ROWS = (
    ("ensemble", "Holt-Winters + gradient boosting (the default)", (0.67, 0.69, 0.77, 0.93), 0.76, "main", None),
    ("ets", "Holt-Winters alone", (0.69, 0.72, 0.79, 0.96), 0.79, "main", None),
    ("theta", "Theta method", (0.71, 0.73, 0.79, 0.95), 0.80, "main", None),
    ("gbm", "gradient boosting alone (pooled)", (0.73, 0.74, 0.81, 0.98), 0.81, "main", None),
    ("airline", "SARIMA(0,1,1)(0,1,1)12, the airline model", (0.73, 0.74, 0.80, 1.01), 0.83, "main", None),
    ("stl_ets", "STL then damped ETS (the first version)", (0.85, 0.85, 0.92, 0.97), 0.87, "main", None),
    ("snaive", "seasonal naive", (1.03, 0.96, 0.96, 0.97), 0.96, "main", None),
    ("nn_mlp", "BESSER neural network, MLP, pooled", (0.80, 0.80, 0.86, 1.02), 0.85, "nn", None),
    ("nn_gru", "BESSER neural network, GRU, pooled", (0.86, 0.81, 0.87, 0.98), 0.89, "nn", None),
    ("nn_mlp_local", "BESSER MLP trained on one series", (0.83, 0.99, 1.11, 1.39), 1.11, "nn", None),
    ("ensemble_nn", "Holt-Winters + gradient boosting + BESSER MLP", (0.70, 0.70, 0.79, 0.95), 0.77, "nn", None),
    ("snaive_nn", "seasonal naive (the networks' run)", (1.03, 0.95, 0.96, 0.96), 0.96, "nn", None),
)
# Tried, averaged MASE only (docs/forecasting.md, "What we tried that did not win")
_TRIED = (
    ("gbm_local", "gradient boosting trained on the one series being forecast", 0.86),
    ("select", "picking the best method per series from its own past errors", 0.82),
    ("median4", "the median of four models", 0.79),
    ("mean4", "the mean of four models", 0.78),
)


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")


def _month_words(period: str) -> str:
    """'2025-09' -> 'September 2025'; anything else as it is."""
    s = str(period or "")
    if len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit() and 1 <= int(s[5:]) <= 12:
        return f"{_MONTHS[int(s[5:]) - 1]} {s[:4]}"
    return s


def study() -> dict:
    rows = [{"key": k, "name": name, **{f"mase_h{h}": v for h, v in zip(backtest.HORIZONS, hs)}, "mase": avg,
             "run": run} for k, name, hs, avg, run, _ in _ROWS]
    by = {r["key"]: r for r in rows}
    ens, naive = by["ensemble"], by["snaive"]
    skill = [{"h": h, "mase": ens[f"mase_h{h}"], "naive_mase": naive[f"mase_h{h}"],
              "skill": 1 - ens[f"mase_h{h}"] / naive[f"mase_h{h}"]} for h in backtest.HORIZONS]
    return {**STUDY, "skill": skill, "methods": rows,
            "tried": [{"key": k, "name": name, "mase": m} for k, name, m in _TRIED],
            "band_coverage": 0.82, "band_level": 0.8}


# --------------------------------------------------------------------------- the models (from the code)
FEATURE_WORDS = {
    "horizon": "the horizon, 1 to 12 months ahead (one direct multi-horizon model)",
    "target month": "the calendar month being forecast (categorical)",
    "growth t": "the year-on-year growth of the last month",
    "growth t-1": "the same, one month earlier",
    "growth t-2": "the same, two months earlier",
    "growth 3m": "the average year-on-year growth of the last 3 months",
    "growth 6m": "the same over 6 months",
    "growth 12m": "the same over 12 months",
    "last month vs last 12": "the last month against the average of the last 12",
    "base month vs last 12": "the target's month a year earlier against the average of the last 12",
    "working days vs a year earlier": "working days (Monday to Friday) in the target month against a year earlier",
}

MEMBERS = {
    "ets": ("Holt-Winters exponential smoothing",
            "damped additive trend and additive seasonality, fitted on the logs of this series (so the season is "
            "multiplicative in euros); smoothing parameters by least squares on the one-step errors (statsmodels)"),
    "gbm": ("gradient boosting",
            "a histogram gradient boosting regressor (scikit-learn) that predicts the year-on-year log growth of the "
            "target month: the seasonal naive forecast plus a learned correction"),
    "theta": ("Theta method", "the Theta method on the seasonally adjusted logs"),
    "airline": ("SARIMA(0,1,1)(0,1,1)12", "the airline model of Box and Jenkins, on the logs"),
    "stl_ets": ("STL then damped ETS", "robust STL seasonal adjustment, then exponential smoothing with a damped trend"),
    "snaive": ("seasonal naive", "the same month a year earlier"),
    "nn": ("BESSER neural network",
           "a network modelled in BESSER (B-UML neural-network metamodel) and generated into PyTorch code; it reads "
           "the last 24 months of standardised year-on-year growth and the calendar month, and outputs the next 12"),
}


def _pool_span(cutoff: str) -> tuple[int, int]:
    """(series, training examples) of the pooled gradient boosting model trained up to ``cutoff``."""
    p = pool.current()
    if p is None:
        return 0, 0
    c = models.month_index(cutoff)
    n_series = n_rows = 0
    for start, _z in p[1].values():
        n = c - models.month_index(start) + 1          # months up to the cutoff
        if n < pool.MIN_MONTHS:
            continue
        n_series += 1
        t0 = max(2 * models.SEASON - 1, n - 1 - models.GBM_WINDOW)
        n_rows += sum(min(models.SEASON, n - 1 - t) for t in range(t0, n - 1))
    return n_series, n_rows


def _gbm_params() -> dict:
    p = models.GBM_PARAMS
    return {"trees": p["max_iter"], "learning_rate": p["learning_rate"], "max_depth": p["max_depth"],
            "min_samples_leaf": p["min_samples_leaf"], "l2_regularization": p["l2_regularization"],
            "loss": "absolute error"}


def _live(bt: dict) -> dict:
    horizons = [{"h": h, "mase": bt.get(f"mase_h{h}"), "naive_mase": bt.get(f"naive_mase_h{h}"),
                 "skill": bt.get(f"skill_h{h}"), "n": bt.get(f"n_h{h}")} for h in backtest.HORIZONS]
    names = {"ensemble": "Holt-Winters + gradient boosting", "ets": "Holt-Winters alone", "theta": "Theta method",
             "gbm": "gradient boosting alone", "snaive": "seasonal naive", "nn": "BESSER neural network",
             "airline": "SARIMA airline", "stl_ets": "STL then damped ETS"}
    methods = [{"method": m["method"], "name": names.get(m["method"], m["method"]), "chosen": m.get("chosen", False),
                **{f"mase_h{h}": m.get(f"mase_h{h}") for h in backtest.HORIZONS}, "mase": m.get("mase"),
                "coverage": m.get("coverage")} for m in bt.get("methods") or []]
    scored = [m for m in methods if m["mase"] is not None]
    best = min(scored, key=lambda m: m["mase"])["method"] if scored else None
    return {"origins": bt.get("origins"), "first_origin": bt.get("first_origin"), "last_origin": bt.get("last_origin"),
            "step": bt.get("step"), "horizons": horizons, "mase": bt.get("mase"), "naive_mase": bt.get("naive_mase"),
            "methods": methods, "best": best}


def build(method: str, bt: Optional[dict], periods: list[str], prob: float,
          scenarios: Optional[list] = None, complete_until: Optional[str] = None) -> dict:
    """The model card of a forecast of the series ``periods`` (the months it was fitted on) by ``method``,
    with ``bt`` the forecast's backtest (``service.forecast``)."""
    members = list(models.ENSEMBLE) if method == "ensemble" else [method]
    pooled = pool.key() is not None
    cutoff = pool.cutoff_for(periods[-1])
    n_series, n_rows = _pool_span(cutoff) if "gbm" in members and pooled else (0, 0)
    mods = []
    for m in members:
        name, detail = MEMBERS.get(m, (m, models.LABELS.get(m, m)))
        if m == "nn":
            detail = f"{detail} ({nn.NETWORK.upper()})"
        mods.append({"method": m, "name": name, "detail": detail})
    training = {"first": periods[0], "last": periods[-1], "months": len(periods),
                "breaks": [list(b) for b in models.BREAKS]}
    if "gbm" in members or "nn" in members:
        training.update({"pooled": pooled, "cutoff": cutoff, "pool_series": n_series, "pool_examples": n_rows,
                         "window_years": models.GBM_WINDOW // 12})
    if "gbm" in members:
        training["gbm_params"] = _gbm_params()

    bt = bt or {}
    live = _live(bt)
    band = {"level": prob, "method": "split conformal", "coverage": bt.get("coverage"), "checks": bt.get("coverage_n"),
            "calibration": f"the {prob:.0%} quantile (finite-sample, (n+1)) of this series' own backtest errors at "
                           "each horizon; the band never narrows further ahead",
            "scored": f"each origin's band calibrated only on the errors known at that origin, after at least "
                      f"{backtest.MIN_CALIBRATION} of them"}

    limits = []
    for sc in (scenarios or [])[:1]:
        limits.append(f"Since {_month_words(sc['since'])} a product line, {sc['line_label']}, adds trade on a scale no "
                      "recent pattern shows, so no model has learned it. The two scenarios (the line stops, the line "
                      "stays at its recent level) bracket it; the model's own line assumes the recent rise goes on.")
    if not limits:
        limits.append("Breaks the past does not show (a new tariff, a lost customer, a new product line) are in no "
                      "model and not in the band.")
    limits.append("Twelve months ahead the models are close to repeating the same month a year earlier: they earn "
                  "their keep within the next six months.")
    limits.append(f"Uses the data through {periods[-1]}"
                  + (f"; months after {complete_until} are provisional and the source revises them." if
                     complete_until and periods[-1] > complete_until else ".")
                  + " Values are nominal euros: no price or volume split.")
    limits.append("The COVID months (2020-03 to 2020-06) are replaced by a counterfactual for training only, so the "
                  "models do not learn the collapse as a pattern.")

    return {"method": method, "members": mods, "combination": "the mean of the members' forecasts, month by month"
            if len(members) > 1 else None,
            "target": "the year-on-year log growth of the target month" if "gbm" in members else None,
            "features": [FEATURE_WORDS.get(f, f) for f in models.GBM_FEATURES] if "gbm" in members else [],
            "training": training, "study": study(), "live": live, "band": band,
            "nn_available": nn.AVAILABLE, "limits": limits}
