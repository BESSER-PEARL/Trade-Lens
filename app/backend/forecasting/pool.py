# SPDX-License-Identifier: MIT
"""The gradient boosting model trained on a pool of series: every HS2 chapter of exports and
imports (about 190 monthly series since 1999), not just the series being forecast.

One series has some 300 months: too few for a machine-learning model, which then does worse than
exponential smoothing. Pooled, the model sees 300 000 (origin, horizon) examples of how a trade
series moves after a given pattern of growth, season and working days, and the backtest shows it
earns its place in the ensemble. The features are scale-free (growth rates and log ratios), so the
same model forecasts a chapter, a partner or the total.

The model is retrained once a year, on the data up to each December (``cutoff_for``): the
backtest uses the model of the December before each origin, the live forecast the one of the last
December. The router gives the pool with ``set_loader``; without it the gradient boosting model
is trained on the series alone.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import models

VERSION = "4"
POOL_TTL = 600          # seconds the pool is kept before it is read again
MIN_MONTHS = 48
CACHE_DIR = Path(os.environ.get("FORECAST_CACHE_DIR") or Path(tempfile.gettempdir()) / "statec_forecast_cache")

_loader: Optional[Callable[[], dict]] = None
_pool: dict = {}                    # {"at": time, "key": hash, "series": {name: (start, values)}}
_models: dict[tuple[str, str], object] = {}
_lock = threading.Lock()
_model_locks: dict[tuple[str, str], threading.Lock] = {}


def set_loader(loader: Optional[Callable[[], dict]]) -> None:
    """``loader()`` returns ``{name: (periods, values)}``: the monthly series of the pool."""
    global _loader
    _loader = loader
    _pool.clear()


def ensure_loader(loader: Callable[[], dict]) -> None:
    """``set_loader(loader)`` unless it is already the loader (so the pool stays cached)."""
    if _loader is not loader:
        set_loader(loader)


def consecutive(periods: list[str], values) -> tuple[list[str], np.ndarray]:
    """The longest run of consecutive months that ends with the last one."""
    values = np.asarray(values, float)
    i = len(periods) - 1
    while i > 0 and models.month_index(periods[i]) - models.month_index(periods[i - 1]) == 1:
        i -= 1
    return list(periods[i:]), values[i:]


def cutoff_for(period: str) -> str:
    """The December the model used at ``period`` was trained up to: the last one not after it."""
    year, month = int(period[:4]), int(period[5:7])
    return f"{year if month == 12 else year - 1}-12"


def current() -> Optional[tuple[str, dict]]:
    """(key, {name: (start, z)}) of the pool, logged and cleaned, or None without a loader."""
    if _loader is None:
        return None
    with _lock:
        if _pool and time.time() - _pool["at"] < POOL_TTL:
            return _pool["key"], _pool["series"]
        try:
            raw = _loader() or {}
        except Exception:  # the pool is an improvement; without it the model trains on the series
            return None
        series, digest = {}, hashlib.sha1(VERSION.encode())
        for name in sorted(raw):
            periods, values = consecutive(*raw[name])
            if len(values) < MIN_MONTHS or not (values > 0).all():
                continue
            z, _ = models.transform(values)
            series[name] = (periods[0], z)
            digest.update(name.encode() + periods[0].encode() + values.tobytes())
        if not series:
            return None
        _pool.update(at=time.time(), key=digest.hexdigest()[:24], series=series)
        return _pool["key"], series


def key() -> Optional[str]:
    p = current()
    return p[0] if p else None


def model(cutoff: str):
    """The model trained on the pool up to ``cutoff`` (a December), or None without a pool."""
    p = current()
    if p is None:
        return None
    pkey, series = p
    k = (pkey, cutoff)
    if k in _models:
        return _models[k]
    with _lock:
        lock = _model_locks.setdefault(k, threading.Lock())
    with lock:
        if k in _models:
            return _models[k]
        path = CACHE_DIR / f"gbm_{pkey}_{cutoff}.pkl"
        fitted = None
        try:
            fitted = pickle.loads(path.read_bytes())
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError):
            fitted = None
        if fitted is None:
            c = models.month_index(cutoff)
            X, y = [], []
            for start, z in series.values():
                end = c - models.month_index(start)       # index of the cutoff in this series
                if end + 1 < MIN_MONTHS:
                    continue
                zc = models.clean_breaks(z[:end + 1], start)
                xs, ys = models.training_rows(zc, models.month_index(start))
                X.append(xs)
                y.append(ys)
            if not X:
                return None
            fitted = models.gbm_train(np.vstack(X), np.concatenate(y))
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_bytes(pickle.dumps(fitted))
                tmp.replace(path)
            except OSError:
                pass
        _models[k] = fitted
        if len(_models) > 16:
            _models.pop(next(iter(_models)))
        return fitted
