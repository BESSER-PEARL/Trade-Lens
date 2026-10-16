# SPDX-License-Identifier: MIT
"""Forecasting of the monthly trade series for /insights/forecast (see docs/forecasting.md).

- ``models``: the methods (seasonal naive, STL + ETS, Holt-Winters, SARIMA airline, Theta,
  gradient boosting, and the average of Holt-Winters and gradient boosting), one function each.
- ``pool``: the gradient boosting model trained on every HS2 chapter at once, once a year.
- ``backtest``: the rolling-origin backtest (MASE, MAPE, conformal coverage, annual error).
- ``service``: the forecast of a series by a method, with conformal bands and the backtest
  table of every method, cached per series.
- ``scenarios``: "the smartphone line stops / continues".
- ``card``: the model card of a forecast (the members, the features, the two backtests, the band).
- ``nn``: the neural network BESSER generated from a B-UML model (method ``nn``, when torch is
  installed); see docs/forecasting_nn.md.
"""
from . import pool
from . import nn  # registers the "nn" method before the service lists the methods
from . import card
from .models import LABELS, METHODS
from .scenarios import LINES, line_scenarios
from .service import DEFAULT, TABLE, NotEnoughHistory, forecast, label, resolve

__all__ = ["DEFAULT", "card", "LABELS", "LINES", "METHODS", "NotEnoughHistory", "TABLE", "forecast", "label", "line_scenarios", "nn", "pool",
           "resolve"]
