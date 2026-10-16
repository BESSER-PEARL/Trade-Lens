# SPDX-License-Identifier: MIT
"""The neural forecaster as a forecasting method ("nn"): a thin adapter around code BESSER generated.

The networks are B-UML models (``model/forecast_nn.py``); BESSER's NN metamodel describes them and
our ``TimeSeriesForecasterGenerator`` (``besser_sdmx/generators/forecast_nn``) generated
``generated/forecaster_<network>.py`` from them: the PyTorch network plus the time-series pipeline
(logs and year-on-year differences, windowing, seeded training, prediction). Nothing here defines
a layer: change the model and regenerate (``python model/forecast_nn.py --install``).

What this adapter adds is what the other methods of the service already do:

- the series arrives in logs (``models.transform``), so the generated code is told not to log again;
- the COVID months are cleaned for training (``models.clean_breaks``);
- like the gradient boosting model, the network is retrained once a year, on the data up to each
  December, and forecasts every origin of that year from its latest window. It is trained on the
  pool of every HS2 chapter when the service has one (``pool.py``), else on the series alone;
- trained networks are cached in memory and on disk (``pool.CACHE_DIR``).

``FORECAST_NN_NETWORK`` picks the network (``mlp`` by default, or ``gru``). Without torch the method
is not offered (``AVAILABLE`` is False). docs/forecasting_nn.md has the backtest.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import threading
from pathlib import Path
from types import ModuleType
from typing import Optional

import numpy as np

from . import models, pool

AVAILABLE = importlib.util.find_spec("torch") is not None
GENERATED = Path(__file__).parent / "generated"
NETWORKS = ("mlp", "gru")
NETWORK = os.environ.get("FORECAST_NN_NETWORK", "mlp")
# A pooled training set has ~50 000 windows instead of ~250: fewer passes, larger batches.
POOL_EPOCHS, POOL_BATCH = 6, 256
VERSION = "1"            # bump when the adapter changes how a network is trained

_modules: dict[str, ModuleType] = {}
_trained: dict[tuple, object] = {}
_lock = threading.Lock()
_locks: dict[tuple, threading.Lock] = {}


def generated(network: Optional[str] = None) -> ModuleType:
    """The module BESSER generated for ``network`` (``generated/forecaster_<network>.py``)."""
    network = network or NETWORK
    if network not in NETWORKS:
        raise ValueError(f"Unknown network {network!r}; use one of {NETWORKS}")
    if network not in _modules:
        path = GENERATED / f"forecaster_{network}.py"
        spec = importlib.util.spec_from_file_location(f"forecasting.generated.forecaster_{network}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _modules[network] = module
    return _modules[network]


def _digest(*parts) -> str:
    h = hashlib.sha1(VERSION.encode())
    for p in parts:
        h.update(p.tobytes() if isinstance(p, np.ndarray) else str(p).encode())
    return h.hexdigest()[:20]


def _cached(key: tuple, path: Path, train):
    """The forecaster under ``key``: from memory, from ``path``, or trained and saved there."""
    if key in _trained:
        return _trained[key]
    with _lock:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if key in _trained:
            return _trained[key]
        gen = generated(key[0])
        forecaster = None
        try:
            forecaster = gen.load(str(path))
        except Exception:  # missing or unreadable: train again
            forecaster = None
        if forecaster is None:
            forecaster = train()
            if forecaster is None:
                return None
            try:
                pool.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                gen.save(forecaster, str(tmp))
                tmp.replace(path)
            except OSError:
                pass  # the cache is a speed-up
        _trained[key] = forecaster
        if len(_trained) > 64:
            _trained.pop(next(iter(_trained)))
        return forecaster


def pooled(cutoff: str, network: Optional[str] = None):
    """The network trained on every series of the pool up to ``cutoff`` (a December), or None
    without a pool."""
    network = network or NETWORK
    p = pool.current()
    if p is None:
        return None
    pkey, series = p
    gen = generated(network)

    def train():
        c = models.month_index(cutoff)
        zs, starts = [], []
        for start, z in series.values():
            end = c - models.month_index(start)
            if end + 1 < pool.MIN_MONTHS:
                continue
            zs.append(models.clean_breaks(z[:end + 1], start))
            starts.append(models.month_index(start) % 12)
        if not zs:
            return None
        return gen.fit(zs, starts, log=False, epochs=POOL_EPOCHS, batch_size=POOL_BATCH)

    key = (network, "pool", pkey, cutoff)
    return _cached(key, pool.CACHE_DIR / f"nn_{network}_{_digest(pkey, cutoff)}.pt", train)


def local(z: np.ndarray, start: str, network: Optional[str] = None):
    """The network trained on ``z`` alone (already in logs), or None when it is too short."""
    network = network or NETWORK
    gen = generated(network)
    if len(z) < gen.WINDOW + gen.DIFFERENCE + 12:
        return None
    zc = models.clean_breaks(z, start)
    key = (network, "local", _digest(zc, start))
    return _cached(key, pool.CACHE_DIR / f"nn_{network}_{key[2]}.pt",
                   lambda: gen.fit(zc, [models.month_index(start) % 12], log=False))


def forecaster_for(z: np.ndarray, start: str, origin: int, network: Optional[str] = None):
    """The network that forecasts from ``origin``: trained up to the December before it (on the
    pool, else on the series alone)."""
    cutoff = pool.cutoff_for(models.period_of(models.month_index(start) + origin))
    trained = pooled(cutoff, network)
    if trained is None:
        end = models.month_index(cutoff) - models.month_index(start)
        trained = local(z[:end + 1], start, network) if end >= 0 else None
    return trained


def forecast(z: np.ndarray, h: int, start: str = "2000-01", network: Optional[str] = None) -> np.ndarray:
    """The method's signature (``models.METHODS``): ``h`` months after ``z`` (logs), on its scale."""
    gen = generated(network)
    trained = forecaster_for(z, start, len(z) - 1, network)
    if trained is None or len(z) < gen.WINDOW + gen.DIFFERENCE:
        return models.snaive(z, h)
    return np.asarray(trained.predict(models.clean_breaks(z, start), h, models.month_index(start) % 12), float)


def run(z: np.ndarray, start: str, origins: list[int], network: Optional[str] = None) -> dict[int, np.ndarray]:
    """Backtest forecasts {origin: next 12 months}, each by the network of the December before it."""
    return {o: forecast(z[:o + 1], models.SEASON, start, network) for o in origins}


def label(network: Optional[str] = None, pooled_: Optional[bool] = None) -> str:
    network = network or NETWORK
    kind = {"mlp": "a multilayer perceptron", "gru": "a recurrent network (GRU)"}[network]
    pooled_ = pool.key() is not None if pooled_ is None else pooled_
    where = "every HS2 chapter" if pooled_ else "this series"
    return (f"{kind} on the last 24 months of year-on-year growth, modelled in BESSER and generated as "
            f"PyTorch code, trained on {where}")


def fingerprint() -> str:
    """Changes when the generated code or this adapter changes: it invalidates cached forecasts."""
    h = hashlib.sha1(VERSION.encode() + NETWORK.encode())
    for network in NETWORKS:
        try:
            h.update((GENERATED / f"forecaster_{network}.py").read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


# ``nn`` is a method of the service when torch is installed. FORECAST_NN_ENSEMBLE=1 also makes it a
# member of the default ensemble; it is off because the backtest says it does not help
# (docs/forecasting_nn.md).
IN_ENSEMBLE = AVAILABLE and os.environ.get("FORECAST_NN_ENSEMBLE", "0") not in ("", "0", "false", "no")
if AVAILABLE:
    models.METHODS["nn"] = forecast
    models.LABELS["nn"] = label(pooled_=True)
    if IN_ENSEMBLE:
        models.ENSEMBLE = (*models.ENSEMBLE, "nn")
        models.LABELS["ensemble"] = ("the average of Holt-Winters exponential smoothing, gradient boosting trained "
                                     "on every HS2 chapter and a neural network generated by BESSER")
