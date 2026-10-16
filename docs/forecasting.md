# Forecasting: methods, backtest and how honest it is

`/insights/forecast` forecasts any monthly series of the database (a flow's total, a chapter, a
partner) and says how much to trust it. The code is in `app/backend/forecasting/`; the response
is documented in `docs/agents.md`. Everything is deterministic: the same data gives the same answer.

## What it does

- **Default model:** the average of two models that make different mistakes.
  - Holt-Winters exponential smoothing: damped additive trend and additive season, fitted on
    the logs of the series, so the season is multiplicative in euros. It uses the full history
    since 1999.
  - Gradient boosting: a scikit-learn `HistGradientBoostingRegressor`, trained on all 190
    monthly HS2 chapter series (exports and imports) at once. It does not learn the level of the
    series. It learns the year-on-year growth of the target month from scale-free features: the
    last three months' growth, the 3-, 6- and 12-month average growth, the last month and the
    base month against the last 12, the calendar month, the horizon, and the working days
    against a year earlier. Its forecast is the seasonal naive forecast plus a learned
    correction. One model covers every horizon from 1 to 12 months (the horizon is a feature);
    this is the "direct" multi-horizon approach. The model is retrained once a year, on the data
    up to each December.
- **Other methods** (`method=`): `ets`, `theta`, `airline` (SARIMA(0,1,1)(0,1,1)12), `gbm`,
  `stl_ets` (the first version: STL, then damped ETS) and `snaive` (the same month a year earlier).
  With torch installed, also `nn`: a neural network modelled in BESSER (B-UML NN metamodel) and
  generated as PyTorch code; [forecasting_nn.md](forecasting_nn.md) has its backtest.
- **Known breaks:** for training only, the COVID months (2020-03 to 2020-06) are replaced by the
  same months a year earlier, grown at the pace of the three months before the break.
- **Intervals:** conformal. An 80% band is where 80% of the method's own backtest errors on this
  series fell at that horizon. The quantile is the finite-sample (n+1) one, and the band never
  narrows further ahead. Annual bands use the backtest errors of sums of k months. The method
  makes no assumption about the error distribution, and every method is treated the same way.
- **Scenarios:** since September 2025 one product line (smartphones, CN 8517 13 00) has added
  40 to 380 million euros a month to chapter 85. No model can know whether it lasts, so for a
  flow's total (or chapter 85) against the world, the forecast also gives two scenarios:
  - *the line stops:* the series without chapter 85, plus chapter 85 as it was heading before
    the line appeared;
  - *the line continues:* the same, plus the line at its level of the last three months.

## The backtest that chose it

The backtest is rolling-origin. At each quarter end from December 2009 to March 2026 (66
origins), every method is fitted only on the data up to that origin, forecasts 12 months ahead,
and is scored against what happened. The tables score the totals of exports and imports
(2 series, about 1,500 forecasts per method).

**MASE** is the error divided by the in-sample error of the seasonal naive forecast, so 1.0
means as good as the naive forecast.

| method | MASE 1 month | 3 months | 6 months | 12 months | MASE average | MAPE 1 month | MAPE 12 months | calendar year MAPE |
|---|---|---|---|---|---|---|---|---|
| **ensemble (Holt-Winters + gradient boosting)** | **0.67** | **0.69** | **0.77** | **0.93** | **0.76** | **6.8%** | **9.4%** | **5.3%** |
| Holt-Winters | 0.69 | 0.72 | 0.79 | 0.96 | 0.79 | 7.0% | 9.6% | 5.7% |
| Theta | 0.71 | 0.73 | 0.79 | 0.95 | 0.80 | 7.2% | 9.6% | 5.9% |
| gradient boosting (pooled) | 0.73 | 0.74 | 0.81 | 0.98 | 0.81 | 7.5% | 10.1% | 5.6% |
| SARIMA airline | 0.73 | 0.74 | 0.80 | 1.01 | 0.83 | 7.4% | 10.3% | 5.9% |
| first version (STL + ETS) | 0.85 | 0.85 | 0.92 | 0.97 | 0.87 | 8.6% | 9.7% | 5.8% |
| seasonal naive | 1.03 | 0.96 | 0.96 | 0.97 | 0.96 | 10.5% | 9.7% | 6.8% |

On the top 8 chapters of each flow (16 series), the average MASE is 0.89 for the ensemble, 1.07
for the first version and 1.16 for the seasonal naive. The 80% conformal bands held 78% of the
actual months.

### What we tried that did not win

- **Gradient boosting trained on the one series being forecast:** worse than every classical
  model (MASE 0.86). About 300 months is too little for machine learning; pooled over every
  chapter, the same model wins its place in the ensemble.
- **Picking the best method per series from its own past errors:** worse than the fixed
  ensemble (0.82 against 0.76).
- **Larger ensembles:** the median of four models (0.79) and the mean of four models (0.78) lost
  to the mean of two. Adding Theta to the pair changed nothing.
- **Training on the last 10 years only, instead of the full history:** worse at 1 month (0.67
  against 0.61 for Holt-Winters, in a first experiment with origins every two months).
- **Bottom-up:** summing Holt-Winters forecasts of the 80 to 96 chapters beats forecasting the
  total directly by 1.5% (exports) to 4% (imports). It needs about 100 model fits per request,
  so it is not live. A nightly batch could add it.

## Honesty

- **The response says where the method does not help.** `backtest.methods` has the scores of
  every method on this very series, over its last 20 quarter ends. `backtest.summary` is a
  sentence the agent can quote, for example: "The ensemble of Holt-Winters and gradient boosting
  beats the seasonal naive forecast by 49% at 1 month … 3% at 12 months ahead" (the figures change
  each month as data arrives; read them from the response).
- **Twelve months ahead, no method is much better than the seasonal naive forecast** (0.93
  against 0.97). The models earn their keep within the next six months. The caveat says so
  whenever the method loses at 12 months on the series.
- **Breaks that the past does not show are not in the band:** a new tariff, a lost customer, a
  new smartphone line. That is why the scenarios exist. For the autumn, the model's own forecast
  of the total partly repeats the November 2025 smartphone peak, so it can sit above both
  scenarios.
- **The last months are provisional** and the source revises them. All values are nominal euros.

## Speed

- A forecast with its backtest table takes about 2 s the first time for a series and 20 ms after
  that. The fitted forecasts are cached in memory and on disk (`FORECAST_CACHE_DIR`, by default a
  folder in the system temp directory).
- The pooled gradient boosting models take about 40 s to train the very first time (one per
  December, from 2020 to 2025). The backend trains them in the background at startup, and keeps
  them on disk (`FORECAST_WARMUP=0` turns this off).
- The offline experiments behind this page are in the project scratchpad (`forecast_report.md`).

## On the page and in the agents

- **The band** is a `forecast` mark on the trend line. Since contract 1.3 its `params.scenarios: true`
  also draws the response's scenarios at the band's end, as two labelled points in the forecaster's
  ink ("If smartphones stop €16.7 bn", "If smartphones continue €17 bn"). The line chart gives the
  years after the data a zone of its own (shaded, "Forecast"), wide enough to read one forecast year
  on a 27-year axis. A forecast mark is never dimmed as "about another year".
- **Both agents ask for the default method** (`method` left out, so the backtest's winner) and say the
  backtest out loud: `backtest.summary`, the skill at 1, 3, 6 and 12 months, how often the band held,
  and the scenarios as the honest range when one line drove the last change. The real forecaster also
  answers "which method works best?" from `backtest.methods`. Facts the notes can cite:
  `backtest.summary` (text), `backtest.skill_h3` and `backtest.coverage` (pct),
  `scenarios.[id=smartphones_stops].rows.[period=2026].value` (eur).
- **Without scikit-learn** (a Python with only the base requirements) the service falls back to
  Holt-Winters alone instead of failing: `auto`, `ensemble` and `gbm` answer with `ets`, and the label
  says which model ran. `run.py` starts the backend with `.venv`'s Python, which has it.

## What could improve it next

Ordered by expected gain for the effort, from the experiments above:

1. **Bottom-up as a nightly batch** (1.5 to 4% better on the totals): forecast the 80 to 96 chapters
   once a night, cache their sum, and serve it as a method. No request would wait for the 100 fits.
2. **One-off lines as regressors, not only scenarios.** The smartphone line is known (CN 8517 13 00,
   since 2025-09). Forecasting the total without it, and the line separately with its own short model,
   would stop the model from repeating the November 2025 peak. The same holds for any CN8 line that
   moves more than a third of a chapter's change (the movers endpoint already finds them).
3. **Hierarchical reconciliation** (MinT) across total, chapters and partners, so the forecasts of the
   parts add up to the total on every chart. It also tends to improve the parts.
4. **Exogenous signals for 6 to 12 months ahead**, where every method is close to the seasonal naive:
   the EU partners' own monthly imports (already loaded as reporters), euro area industrial production,
   and a Brent price series for chapter 27. These are the leading indicators STATEC would use.
5. **Probabilistic scoring**: report the CRPS or the interval score next to MASE, so the bands are
   judged as bands, and tune the conformal level per horizon.
6. **Revisions**: the last months are provisional. Backtesting on vintages (the data as it was known
   at each origin) would give honest errors for the newest months.
