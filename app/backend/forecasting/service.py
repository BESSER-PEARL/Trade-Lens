# SPDX-License-Identifier: MIT
"""Forecast a monthly series with a chosen method, conformal bands and the backtest table.

``forecast(values, periods, method, steps, prob)`` is what /insights/forecast calls. The fitted
forecasts (the backtest's and the live one) depend only on the series (and on the pool the
gradient boosting model is trained on), so they are computed once per series and kept in memory
and on disk (``FORECAST_CACHE_DIR``, by default a folder in the system's temporary directory). The
band's coverage, the horizon and the aggregation are applied on top, so changing them costs
nothing.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np

from . import backtest, models, nn, pool

VERSION = "6"                      # bump when a model changes: it invalidates the disk cache
MAX_STEPS = 36
# Gradient boosting needs scikit-learn. Without it (a Python that only has the base requirements)
# the forecast falls back to Holt-Winters alone instead of failing: "auto", "ensemble" and "gbm"
# then answer with "ets", and the label says which model ran.
HAS_GBM = importlib.util.find_spec("sklearn") is not None
DEFAULT = "ensemble" if HAS_GBM else "ets"   # chosen by the backtest, see docs/forecasting.md
# Backtested on every call (cached): the benchmark, the ensemble's members, a second classical
# model and the ensemble. ``airline`` and ``stl_ets`` (the first version) are left out for speed:
# they are the slowest and did worse; asking for one of them adds it to the table.
TABLE = ("snaive", "ets", "theta", "gbm", "ensemble") if HAS_GBM else ("snaive", "ets", "theta")
METHODS = tuple(models.METHODS)    # every method that can be asked for
ALIASES = {"auto": DEFAULT} if HAS_GBM else {"auto": DEFAULT, "ensemble": "ets", "gbm": "ets"}

_MEM: dict[str, dict] = {}
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def resolve(method: Optional[str]) -> str:
    """The method a request names: ``auto`` (or nothing) is the default."""
    return ALIASES.get(method or "auto", method or DEFAULT)


def label(method: str) -> str:
    if method == "gbm" and pool.key() is None:
        return models.LOCAL_GBM_LABEL
    if method == "nn":
        return nn.label()
    return models.LABELS[method]


# --------------------------------------------------------------------------- fitted forecasts
def _key(values: np.ndarray, periods: list[str]) -> str:
    h = hashlib.sha1(np.asarray(values, float).tobytes())
    h.update(f"{periods[0]}|{periods[-1]}|{VERSION}|{pool.key()}|{nn.fingerprint()}".encode())
    return h.hexdigest()[:24]


def _load(key: str) -> dict:
    if key in _MEM:
        return _MEM[key]
    state = {"final": {}, "bt": {}}
    try:
        raw = json.loads((pool.CACHE_DIR / f"{key}.json").read_text(encoding="utf-8"))
        state["final"] = {m: np.array(v, float) for m, v in raw["final"].items()}
        state["bt"] = {m: {int(o): np.array(v, float) for o, v in fs.items()} for m, fs in raw["bt"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        state = {"final": {}, "bt": {}}
    _MEM[key] = state
    if len(_MEM) > 128:
        _MEM.pop(next(iter(_MEM)))
    return state


def _save(key: str, state: dict) -> None:
    try:
        pool.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        raw = {"final": {m: v.tolist() for m, v in state["final"].items()},
               "bt": {m: {str(o): v.tolist() for o, v in fs.items()} for m, fs in state["bt"].items()}}
        tmp = pool.CACHE_DIR / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        tmp.replace(pool.CACHE_DIR / f"{key}.json")
    except OSError:
        pass  # the cache is a speed-up, never a requirement


def _final(method: str, z: np.ndarray, periods: list[str]) -> np.ndarray:
    if method == "gbm":
        model = pool.model(pool.cutoff_for(periods[-1]))
        return np.asarray(models.gbm(z, MAX_STEPS, periods[0], model=model), float)
    return np.asarray(models.METHODS[method](z, MAX_STEPS, periods[0]), float)


def fitted(values: np.ndarray, periods: list[str], methods: tuple, origins: list[int]) -> dict:
    """The live forecast (``final``, MAX_STEPS months) and the backtest forecasts (``bt``) of
    ``methods`` at ``origins``, computing only what the cache does not have yet."""
    key = _key(values, periods)
    with _GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    with lock:
        state = _load(key)
        z, _ = models.transform(values)
        start = periods[0]
        order = list(dict.fromkeys(m for m in methods if m != "ensemble"))
        if "ensemble" in methods:
            order = list(dict.fromkeys([*order, *models.ENSEMBLE]))
        todo_final = [m for m in order if m not in state["final"]]
        todo_bt = {m: [o for o in origins if o not in state["bt"].get(m, {})] for m in order}
        todo_bt = {m: os_ for m, os_ in todo_bt.items() if os_}
        changed = bool(todo_final or todo_bt)
        if changed:
            if "gbm" in todo_final or "gbm" in todo_bt:          # train the pool's models first, once
                for c in sorted({pool.cutoff_for(models.period_of(models.month_index(start) + o))
                                 for o in todo_bt.get("gbm", [])} | {pool.cutoff_for(periods[-1])}):
                    pool.model(c)
            with ThreadPoolExecutor(max_workers=4) as ex:
                finals = {m: ex.submit(_final, m, z, periods) for m in todo_final}
                bts = {m: ex.submit(backtest.run, z, start, m, os_) for m, os_ in todo_bt.items()}
                for m, f in finals.items():
                    state["final"][m] = f.result()
                for m, f in bts.items():
                    state["bt"].setdefault(m, {}).update(f.result())
        if "ensemble" in methods:
            if "ensemble" not in state["final"]:
                state["final"]["ensemble"] = np.mean(np.vstack([state["final"][m] for m in models.ENSEMBLE]), axis=0)
                changed = True
            ens = state["bt"].setdefault("ensemble", {})
            for o in origins:
                if o not in ens:
                    ens[o] = np.mean(np.vstack([state["bt"][m][o] for m in models.ENSEMBLE]), axis=0)
                    changed = True
        if changed:
            _save(key, state)
        return state


# --------------------------------------------------------------------------- the forecast
def _skill(model: Optional[float], naive: Optional[float]) -> Optional[float]:
    """How much smaller the error is than the seasonal naive's: 0.2 = 20% better, negative = worse."""
    if model is None or naive is None or naive == 0:
        return None
    return 1 - model / naive


def summary(method: str, table: dict) -> str:
    """One sentence for the forecaster agent: where the method beats the seasonal naive forecast."""
    me, nv = table.get(method), table.get("snaive")
    if method == "snaive" or not me or not nv:
        return "The seasonal naive forecast repeats the same month a year earlier; it is the benchmark."
    wins, losses = [], []
    for h in backtest.HORIZONS:
        s = _skill(me.get(f"mase_h{h}"), nv.get(f"mase_h{h}"))
        if s is None:
            continue
        word = "1 month" if h == 1 else f"{h} months"
        (wins if s >= 0.005 else losses).append((word, s))
    names = {"ensemble": ("The ensemble of Holt-Winters, gradient boosting and the neural network" if nn.IN_ENSEMBLE
                          else "The ensemble of Holt-Winters and gradient boosting"),
             "nn": "The neural network generated by BESSER",
             "snaive": "The seasonal naive forecast"}
    name = names.get(method, models.LABELS[method][0].upper()
                     + models.LABELS[method][1:].split(" on ")[0].split(",")[0])
    if wins and not losses:
        return (f"{name} beats the seasonal naive forecast at every horizon tested: by "
                + ", ".join(f"{s:.0%} at {w}" for w, s in wins) + " ahead.")
    if not wins:
        return f"{name} does not beat the seasonal naive forecast at any horizon tested; trust the band, not the line."
    return (f"{name} beats the seasonal naive forecast by " + ", ".join(f"{s:.0%} at {w}" for w, s in wins)
            + " ahead, but not at " + " or ".join(w for w, _ in losses) + ".")


class NotEnoughHistory(ValueError):
    """The series is too short for a tested forecast (no backtest origin): the API says so, never a 500."""


def forecast(values, periods: list[str], method: str, steps: int, prob: float,
             test_year: Optional[str] = None, table: tuple = TABLE) -> dict:
    """The forecast of the next ``steps`` months by ``method``, with ``prob`` conformal bands.

    Returns ``point``, ``lo`` and ``hi`` (in euros), ``sum_band(a, b, observed)`` for the sum of
    months a..b ahead (1-based), and ``backtest``: the scores of the chosen method (with the fields
    of the first version), the table of every method in ``table`` and a sentence that says where
    the method beats the seasonal naive forecast.
    """
    values = np.asarray(values, float)
    z, log = models.transform(values)
    index = {p: i for i, p in enumerate(periods)}
    extra = ()
    if test_year and f"{test_year}-01" in index:
        extra = (index[f"{test_year}-01"] - 1,)
    origins = backtest.schedule(periods, extra)
    if not origins:
        raise NotEnoughHistory(
            f"Not enough monthly history for a tested forecast: this series has {len(periods)} consecutive months "
            f"(from {periods[0] if periods else '-'}), and a forecast whose band is measured on its own past needs "
            f"more than {backtest.MIN_TRAIN}. Forecast its chapter or the total instead.")
    methods = tuple(dict.fromkeys([*table, method, "snaive"]))
    state = fitted(values, periods, methods, origins)

    scores = {m: backtest.score(values, periods, z, log, {o: state["bt"][m][o] for o in origins}, prob)
              for m in methods}
    err = backtest.errors(values, z, log, {o: state["bt"][method][o] for o in origins})
    hw, kw = backtest.half_widths(err, prob)

    def width(h: int, ws: list) -> float:
        base = ws[min(h, 12) - 1] or 0.0
        return base * math.sqrt(h / 12) if h > 12 else base

    zf = state["final"][method][:steps]
    point = models.untransform(zf, log)
    w = np.array([width(h, hw) for h in range(1, steps + 1)])
    lo = models.untransform(zf - w, log)
    hi = models.untransform(zf + w, log)

    def sum_band(a: int, b: int, observed: float = 0.0) -> tuple[float, float, float]:
        """(value, lo, hi) of ``observed`` plus the forecast of months a..b ahead."""
        s = float(point[a - 1:b].sum())
        q = width(min(b - a + 1, 12), kw) * (math.sqrt(b / 12) if b > 12 else 1.0)
        if log and s > 0:
            return observed + s, observed + s * math.exp(-q), observed + s * math.exp(q)
        return observed + s, observed + s - q, observed + s + q

    me, nv = scores[method], scores["snaive"]
    complete = [r["period"] for r in me["annual"]]
    year = test_year if test_year in complete else (complete[-1] if complete and not test_year else None)
    bt = {
        "origins": len(origins), "first_origin": periods[origins[0]], "last_origin": periods[origins[-1]],
        "step": "quarter", "horizons": list(backtest.HORIZONS), "interval": prob,
        "mape_h1": me["mape_h1"], "mape_h12": me["mape_h12"],
        "naive_mape_h1": nv["mape_h1"], "naive_mape_h12": nv["mape_h12"],
    }
    for h in backtest.HORIZONS:
        bt[f"mase_h{h}"] = me[f"mase_h{h}"]
        bt[f"naive_mase_h{h}"] = nv[f"mase_h{h}"]
        bt[f"skill_h{h}"] = _skill(me[f"mase_h{h}"], nv[f"mase_h{h}"])
        bt[f"n_h{h}"] = me[f"n_h{h}"]
    bt.update({
        "mase": me["mase"], "naive_mase": nv["mase"], "skill": _skill(me["mase"], nv["mase"]),
        "coverage": me["coverage"], "coverage_n": me["coverage_n"],
        "period": year, "mape": None, "naive_mape": None,
        "methods": [{"method": m, "label": label(m), "chosen": m == method,
                     "skill": _skill(scores[m]["mase"], nv["mase"]),
                     **{k: v for k, v in scores[m].items() if k != "annual"}} for m in methods],
        "summary": summary(method, scores),
    })
    if year:
        bt["mape"] = next(r["ape"] for r in me["annual"] if r["period"] == year)
        bt["naive_mape"] = backtest.naive_year(values, periods, year)
    return {"point": point, "lo": lo, "hi": hi, "log": log, "sum_band": sum_band, "backtest": bt,
            "label": label(method)}
