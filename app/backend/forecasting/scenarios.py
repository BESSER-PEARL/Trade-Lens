# SPDX-License-Identifier: MIT
"""Scenarios for a known break that no model can see coming: one product line that appeared at a
known month and may stop or continue.

In September 2025 a line of smartphones (CN 8517 13 00) started to pass through Luxembourg, in
both directions: chapter 85 went from about 50 to 430 million euros of exports a month. Whether it
continues is a business decision, not a pattern in the past, so the forecast says both:

- *stops*: the series without chapter 85, forecast by the chosen method, plus chapter 85 as it
  was heading before the line appeared (forecast from the months before ``since``);
- *continues*: the same, plus the line's extra trade at its level of the last three months.

The model's own forecast of the whole series reads the line as part of the series: its seasonal
pattern partly repeats last autumn's peak (430 million euros in November 2025), so for the autumn
it can sit above both scenarios. The scenarios are the cleaner reading of a break like this one.
"""
from __future__ import annotations

import hashlib
from typing import Optional

import numpy as np

from . import models, pool

LINES = [{
    "id": "smartphones", "product": "85", "since": "2025-09", "cn": "85171300",
    "label": "the smartphone line (CN 8517 13 00)",
    "stops": "The smartphone line stops", "continues": "The smartphone line continues at its recent level",
}]
MATERIAL = 0.01       # a line is worth a scenario when its recent extra trade is 1% of the series or more
RECENT = 3            # months that set the line's "recent level"
_CACHE: dict[str, np.ndarray] = {}


def _point(method: str, values: np.ndarray, h: int, start: str) -> np.ndarray:
    """The point forecast in euros, with the pool's gradient boosting model of the series' last December."""
    key = hashlib.sha1(np.asarray(values, float).tobytes() + f"{method}|{h}|{start}|{pool.key()}".encode()).hexdigest()
    if key not in _CACHE:
        if len(_CACHE) > 256:
            _CACHE.clear()
        model = None
        if models.model_for(method):
            model = pool.model(pool.cutoff_for(models.period_of(models.month_index(start) + len(values) - 1)))
        _CACHE[key] = models.point(method, values, h, start, model=model)[0]
    return _CACHE[key]


def line_scenarios(total: np.ndarray, line: np.ndarray, periods: list[str], spec: dict, method: str,
                   steps: int, only_line: bool = False) -> Optional[dict]:
    """The two scenarios of ``spec`` for the next ``steps`` months, or None when the line is not
    material (or the data does not reach back far enough).

    ``total`` and ``line`` are aligned on ``periods``; ``only_line`` is set when the series *is*
    the line's chapter (then there is no rest of the series to forecast).
    """
    total, line = np.asarray(total, float), np.asarray(line, float)
    if spec["since"] not in periods:
        return None
    i0, n = periods.index(spec["since"]), len(periods)
    if i0 < 60 or i0 >= n:
        return None
    pre = line[:i0]
    if not (pre > 0).all():
        return None
    baseline = _point(method if method != "snaive" else "ets", pre, n - i0 + steps, periods[0])
    extra = line[i0:] - baseline[:n - i0]
    recent = float(extra[-RECENT:].mean())
    level = float(np.mean(total[-12:])) or 1.0
    if abs(recent) / abs(level) < MATERIAL:
        return None
    future_line = baseline[n - i0:n - i0 + steps]
    if only_line:
        rest = np.zeros(steps)
    else:
        core = total - line
        if not (core > 0).all():
            return None
        rest = _point(method, core, steps, periods[0])
    stops = rest + future_line
    return {"id": spec["id"], "label": spec["label"], "since": spec["since"], "product": spec["product"],
            "cn": spec["cn"], "recent_extra": recent, "observed_extra": float(extra.sum()),
            "stops": stops, "continues": stops + recent,
            "stops_label": spec["stops"], "continues_label": spec["continues"]}
