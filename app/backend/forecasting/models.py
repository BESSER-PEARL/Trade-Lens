# SPDX-License-Identifier: MIT
"""Point forecasts of a monthly series, one function per method.

Every method has the same signature, ``f(z, h, start) -> array of h``: ``z`` is the series on the
scale the model works on (the logs of the values when they are all positive, see ``transform``),
``h`` the number of months ahead, ``start`` the calendar month of ``z[0]`` as ``"YYYY-MM"`` (the
calendar features need it). The result is on the same scale as ``z``. Every method is
deterministic: no random seeds, no simulation.

Methods (docs/forecasting.md says why these, with the backtest that chose them):

- ``snaive``: the same month a year earlier, the benchmark every method must beat.
- ``stl_ets``: STL (robust, period 12) then exponential smoothing with a damped trend on the
  seasonally adjusted series. The method of the first version, kept for comparison.
- ``ets``: Holt-Winters exponential smoothing, damped additive trend, additive seasonality (on
  the logs, so the season is multiplicative in euros).
- ``airline``: SARIMA(0,1,1)(0,1,1)12, the "airline model" of Box and Jenkins.
- ``theta``: the Theta method on the seasonally adjusted series (statsmodels ThetaModel).
- ``gbm``: gradient boosting (scikit-learn HistGradientBoostingRegressor) on lag, seasonal and
  working-day features, one model for every horizon ("direct" multi-horizon, the horizon is a
  feature). It predicts the growth on the same month a year earlier, so it is the seasonal naive
  forecast plus a learned correction. Trained on every HS2 chapter at once when the service gives
  it that pool (``model=``, see ``pool.py``), else on the series alone.
- ``ensemble``: the mean of ``ENSEMBLE`` (Holt-Winters and gradient boosting), the default.

Known breaks (``BREAKS``, the COVID months) are replaced for training by the same months a year
earlier, grown at the pace of the three months before the break: the models must not learn the
collapse of April 2020 as a pattern. The replacement only uses months before the break.
"""
from __future__ import annotations

import calendar
import warnings
from functools import lru_cache
from typing import Callable, Optional

import numpy as np

SEASON = 12
BREAKS = [("2020-03", "2020-06")]


# --------------------------------------------------------------------------- calendar
def month_index(period: str) -> int:
    return int(period[:4]) * 12 + int(period[5:7]) - 1


def period_of(index: int) -> str:
    return f"{index // 12}-{index % 12 + 1:02d}"


@lru_cache(maxsize=4096)
def weekdays(index: int) -> int:
    """Weekdays (Monday to Friday) in a month: trade is made on working days."""
    year, month = index // 12, index % 12 + 1
    first, days = calendar.monthrange(year, month)
    return sum(1 for d in range(days) if (first + d) % 7 < 5)


# --------------------------------------------------------------------------- transform
def transform(values: np.ndarray) -> tuple[np.ndarray, bool]:
    """(z, log): the logs when every value is positive, else the values themselves."""
    values = np.asarray(values, float)
    if len(values) and (values > 0).all():
        return np.log(values), True
    return values.copy(), False


def untransform(z: np.ndarray, log: bool) -> np.ndarray:
    return np.exp(z) if log else np.asarray(z, float)


def clean_breaks(z: np.ndarray, start: str) -> np.ndarray:
    """``z`` with the months of ``BREAKS`` replaced by the same months a year earlier plus the
    year-on-year growth of the three months before the break (only months before it are used)."""
    s0 = month_index(start)
    out = None
    for a, b in BREAKS:
        i0, i1 = month_index(a) - s0, month_index(b) - s0
        if i0 < SEASON + 3 or i0 >= len(z):
            continue
        out = z.copy() if out is None else out
        pace = float(np.mean(out[i0 - 3:i0] - out[i0 - 15:i0 - 12]))
        for i in range(i0, min(i1 + 1, len(z))):
            out[i] = out[i - SEASON] + pace
    return z if out is None else out


# --------------------------------------------------------------------------- classical
def snaive(z: np.ndarray, h: int, start: str = "2000-01") -> np.ndarray:
    last = z[-SEASON:]
    return np.array([last[k % SEASON] for k in range(h)], float)


def stl_ets(z: np.ndarray, h: int, start: str = "2000-01") -> np.ndarray:
    import pandas as pd
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel
    from statsmodels.tsa.forecasting.stl import STLForecast
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = STLForecast(pd.Series(z), ETSModel, period=SEASON, robust=True,
                            model_kwargs={"error": "add", "trend": "add", "damped_trend": True})
        return np.asarray(model.fit(fit_kwargs={"disp": False}).forecast(h), float)


def ets(z: np.ndarray, h: int, start: str = "2000-01") -> np.ndarray:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    z = clean_breaks(z, start)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(z, trend="add", damped_trend=True, seasonal="add",
                                   seasonal_periods=SEASON, initialization_method="estimated").fit(use_brute=False)
        return np.asarray(fit.forecast(h), float)


def airline(z: np.ndarray, h: int, start: str = "2000-01") -> np.ndarray:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    z = clean_breaks(z, start)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = SARIMAX(z, order=(0, 1, 1), seasonal_order=(0, 1, 1, SEASON), enforce_stationarity=False,
                      enforce_invertibility=False, concentrate_scale=True).fit(disp=False)
        return np.asarray(fit.forecast(h), float)


def theta(z: np.ndarray, h: int, start: str = "2000-01") -> np.ndarray:
    from statsmodels.tsa.forecasting.theta import ThetaModel
    z = clean_breaks(z, start)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # additive deseasonalising: z is in logs, so this is multiplicative in euros
        fit = ThetaModel(z, period=SEASON, deseasonalize=True, method="additive").fit()
        return np.asarray(fit.forecast(h), float)


# --------------------------------------------------------------------------- machine learning
# random_state is fixed because the binning subsamples 200 000 rows of the pool: without it two
# trainings on the same data give different models.
GBM_PARAMS = dict(max_iter=250, learning_rate=0.05, max_depth=4, min_samples_leaf=50, l2_regularization=1.0,
                  loss="absolute_error", early_stopping=False, random_state=0)
GBM_WINDOW = 144    # origins of the last 12 years train the model
GBM_FEATURES = ("horizon", "target month", "growth t", "growth t-1", "growth t-2", "growth 3m", "growth 6m",
                "growth 12m", "last month vs last 12", "base month vs last 12", "working days vs a year earlier")


def features(z: np.ndarray, s0: int, ts: np.ndarray, ks: np.ndarray) -> np.ndarray:
    """Feature rows for the months ``t + k`` seen from origins ``t`` (arrays of the same length,
    1 <= k <= 12, t >= 23): only values up to ``t`` are used, plus the calendar."""
    ts, ks = np.asarray(ts, int), np.asarray(ks, int)
    growth = np.full(len(z), np.nan)
    growth[SEASON:] = z[SEASON:] - z[:-SEASON]
    cg = np.concatenate([[0.0], np.nancumsum(growth)])
    cz = np.concatenate([[0.0], np.cumsum(z)])

    def mean_growth(w):
        return (cg[ts + 1] - cg[ts + 1 - w]) / w
    level12 = (cz[ts + 1] - cz[ts - 11]) / 12
    target = s0 + ts + ks
    wd = np.array([weekdays(int(i)) - weekdays(int(i) - SEASON) for i in target], float)
    return np.column_stack([
        ks, target % 12, growth[ts], growth[ts - 1], growth[ts - 2], mean_growth(3), mean_growth(6),
        mean_growth(12), z[ts] - level12, z[ts + ks - SEASON] - level12, wd,
    ])


def training_rows(z: np.ndarray, s0: int, window: Optional[int] = None) -> tuple[np.ndarray, np.ndarray]:
    """(X, y) of every (origin, horizon) pair of ``z`` whose target is observed, from the origins
    of the last ``window`` months (``GBM_WINDOW`` by default)."""
    window = GBM_WINDOW if window is None else window
    n = len(z)
    t0 = max(2 * SEASON - 1, n - 1 - window)
    if n - 1 <= t0:
        return np.empty((0, len(GBM_FEATURES))), np.empty(0)
    ts, ks = np.meshgrid(np.arange(t0, n - 1), np.arange(1, SEASON + 1), indexing="ij")
    ts, ks = ts.ravel(), ks.ravel()
    keep = ts + ks < n
    ts, ks = ts[keep], ks[keep]
    return features(z, s0, ts, ks), z[ts + ks] - z[ts + ks - SEASON]


def gbm_train(X: np.ndarray, y: np.ndarray):
    from sklearn.ensemble import HistGradientBoostingRegressor
    model = HistGradientBoostingRegressor(**GBM_PARAMS, categorical_features=[1])
    model.fit(X, y)
    return model


def gbm_fit(z: np.ndarray, start: str = "2000-01"):
    """A model trained on ``z`` alone, or None when ``z`` is too short."""
    if len(z) < 3 * SEASON:
        return None
    return gbm_train(*training_rows(clean_breaks(z, start), month_index(start)))


def gbm(z: np.ndarray, h: int, start: str = "2000-01", model=None) -> np.ndarray:
    """Gradient boosting forecast: ``model`` (trained on the pool of chapters, or on an earlier part
    of the series) or, when None, a model trained on ``z`` alone. Horizons beyond 12 chain on the
    forecast."""
    model = model if model is not None else gbm_fit(z, start)
    if model is None or len(z) < 2 * SEASON:
        return snaive(z, h)
    s0 = month_index(start)
    ext = clean_breaks(z, start).copy()
    out = np.empty(h)
    for k0 in range(0, h, SEASON):                    # blocks of 12 from the last (possibly forecast) month
        t = len(ext) - 1
        ks = np.arange(1, min(SEASON, h - k0) + 1)
        pred = model.predict(features(ext, s0, np.full(len(ks), t), ks))
        block = ext[t + ks - SEASON] + pred
        out[k0:k0 + len(block)] = block
        ext = np.concatenate([ext, block])
    return out


# --------------------------------------------------------------------------- ensemble
# The best combination in the backtest of 2009-2026 (docs/forecasting.md): the best classical model
# and the pooled machine-learning model make different mistakes, so their average beats both.
ENSEMBLE = ("ets", "gbm")


def ensemble(z: np.ndarray, h: int, start: str = "2000-01", model=None, members: Optional[tuple] = None) -> np.ndarray:
    parts = [gbm(z, h, start, model=model) if m == "gbm" else METHODS[m](z, h, start) for m in (members or ENSEMBLE)]
    return np.mean(np.vstack(parts), axis=0)


METHODS: dict[str, Callable] = {"snaive": snaive, "stl_ets": stl_ets, "ets": ets, "airline": airline,
                                "theta": theta, "gbm": gbm, "ensemble": ensemble}
LABELS = {
    "snaive": "the same month a year earlier (seasonal naive)",
    "stl_ets": "exponential smoothing with a damped trend, after STL seasonal adjustment",
    "ets": "Holt-Winters exponential smoothing with a damped trend",
    "airline": "a seasonal ARIMA (the airline model)",
    "theta": "the Theta method",
    "gbm": "gradient boosting on lags, seasons and working days, trained on every HS2 chapter",
    "ensemble": "the average of Holt-Winters exponential smoothing and gradient boosting trained on every HS2 chapter",
}
LOCAL_GBM_LABEL = "gradient boosting on lags, seasons and working days, trained on this series"


def point(method: str, values: np.ndarray, h: int, start: str, model=None) -> tuple[np.ndarray, np.ndarray, bool]:
    """(forecast in levels, forecast on the model's scale, log) of ``method`` for the next h months.
    ``model`` is the gradient boosting model to use (gbm and ensemble), None to train one."""
    z, log = transform(values)
    if method in ("gbm", "ensemble"):
        zhat = np.asarray(METHODS[method](z, h, start, model=model), float)
    else:
        zhat = np.asarray(METHODS[method](z, h, start), float)
    return untransform(zhat, log), zhat, log


def model_for(method: str) -> Optional[str]:
    """Which members of ``method`` need the gradient boosting model."""
    return "gbm" if method in ("gbm", "ensemble") else None
