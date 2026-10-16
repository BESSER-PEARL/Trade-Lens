# SPDX-License-Identifier: MIT
"""The forecasting package of the backend (app/backend/forecasting): models, backtest, service,
scenarios. Synthetic series only, so it runs without the database; the endpoint itself is tested
in test_insights_new.py."""
import sys
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("statsmodels")
pytest.importorskip("sklearn")

BACKEND = Path(__file__).resolve().parents[1] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from forecasting import backtest, models, pool, scenarios, service  # noqa: E402

START = "2010-01"


def synthetic(n: int = 132, trend: float = 0.004, noise: float = 0.03, seed: int = 1) -> tuple[list[str], np.ndarray]:
    """A positive monthly series with a trend, a yearly season and noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    season = 0.15 * np.sin(2 * np.pi * t / 12) - 0.2 * (t % 12 == 7)   # a dip every August
    values = 1e9 * np.exp(trend * t + season + noise * rng.standard_normal(n))
    s0 = models.month_index(START)
    return [models.period_of(s0 + i) for i in range(n)], values


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """A cache of its own, and no pool: the gradient boosting model trains on the series alone."""
    monkeypatch.setattr(pool, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setitem(models.GBM_PARAMS, "max_iter", 60)   # a lighter model: these tests are about the plumbing
    saved = pool._loader
    pool.set_loader(None)
    service._MEM.clear()
    yield
    service._MEM.clear()
    pool.set_loader(saved)


# --------------------------------------------------------------------------- models
@pytest.mark.parametrize("method", sorted(models.METHODS))
def test_every_method_forecasts_finite_and_deterministic(method):
    periods, values = synthetic()
    a, za, log = models.point(method, values, 15, periods[0])
    b, _, _ = models.point(method, values, 15, periods[0])
    assert log and a.shape == (15,) and np.isfinite(a).all() and (a > 0).all()
    assert np.array_equal(a, b)
    # a sensible level: within a factor 2 of the last year
    assert 0.5 * values[-12:].min() < a.mean() < 2 * values[-12:].max()


def test_seasonal_naive_repeats_the_last_year():
    periods, values = synthetic()
    point, _, _ = models.point("snaive", values, 14, periods[0])
    assert np.allclose(point[:12], values[-12:]) and np.allclose(point[12:], values[-12:-10])


def test_models_beat_the_seasonal_naive_on_a_trending_series():
    periods, values = synthetic(trend=0.01, noise=0.01)
    actual = values[-12:]
    def err(m):
        return np.abs(models.point(m, values[:-12], 12, periods[0])[0] - actual).mean()
    naive = err("snaive")
    for m in ("ets", "airline", "gbm", "ensemble"):
        assert err(m) < naive, m


def test_ensemble_is_the_mean_of_its_members():
    periods, values = synthetic()
    z, _ = models.transform(values)
    assert models.ENSEMBLE == ("ets", "gbm")
    parts = np.vstack([models.METHODS[m](z, 6, periods[0]) for m in models.ENSEMBLE])
    assert np.allclose(models.ensemble(z, 6, periods[0]), parts.mean(axis=0))


def test_breaks_are_cleaned_for_training_only():
    periods, values = synthetic(n=150, seed=3)
    i = periods.index("2020-03")
    hit = values.copy()
    hit[i:i + 4] *= 0.5                              # a COVID-like collapse
    z, _ = models.transform(hit)
    clean = models.clean_breaks(z, periods[0])
    assert (clean[i:i + 4] > z[i:i + 4] + 0.3).all()
    assert np.array_equal(np.delete(clean, range(i, i + 4)), np.delete(z, range(i, i + 4)))


def test_series_with_zeros_are_not_logged():
    periods, values = synthetic()
    values[5] = 0.0
    point, _, log = models.point("ets", values, 3, periods[0])
    assert not log and np.isfinite(point).all()


def test_weekdays():
    assert models.weekdays(models.month_index("2026-02")) == 20
    assert models.weekdays(models.month_index("2025-08")) == 21


# --------------------------------------------------------------------------- backtest
def test_schedule_takes_quarter_ends_before_the_last_month():
    periods, _ = synthetic(n=132)
    origins = backtest.schedule(periods)
    assert len(origins) == backtest.ORIGINS
    assert all(int(periods[o][5:7]) % 3 == 0 for o in origins)
    assert origins[-1] < len(periods) - 1 and origins[0] >= backtest.MIN_TRAIN - 1
    extra = periods.index("2014-12")
    assert extra in backtest.schedule(periods, (extra,))


def test_scores_of_a_perfect_forecast():
    periods, values = synthetic()
    z, log = models.transform(values)
    origins = backtest.schedule(periods)
    perfect = {o: np.array([z[o + h] if o + h < len(z) else z[o] for h in range(1, 13)]) for o in origins}
    s = backtest.score(values, periods, z, log, perfect, 0.8)
    assert s["mase_h1"] < 1e-9 and s["mape_h12"] < 1e-9 and s["mase"] < 1e-9
    assert s["coverage"] == 1.0 and s["coverage_n"] > 0
    assert s["annual_mape"] < 1e-9 and s["annual"]


def test_conformal_quantile_is_finite_sample_valid():
    xs = list(range(1, 11))                         # 10 errors: the 80% band needs the 9th
    assert backtest._quantile(xs, 0.8) == 9
    assert backtest._quantile(xs, 0.99) == 10
    assert backtest._quantile([], 0.8) is None


def test_gbm_backtest_retrains_once_a_year():
    periods, values = synthetic()
    z, _ = models.transform(values)
    origins = backtest.schedule(periods)[-8:]
    calls = []
    real = models.gbm_fit
    try:
        models.gbm_fit = lambda *a, **k: calls.append(1) or real(*a, **k)
        out = backtest.run(z, periods[0], "gbm", origins)
    finally:
        models.gbm_fit = real
    assert set(out) == set(origins) and len(calls) == 2   # 8 quarter ends = 2 years


# --------------------------------------------------------------------------- pool
def test_pool_model_is_trained_once_per_december_and_cached():
    series = {f"EXPORT:{i:02d}": synthetic(n=150, seed=10 + i) for i in range(6)}
    calls = []
    pool.set_loader(lambda: calls.append(1) or series)
    assert service.label("gbm") == models.LABELS["gbm"]
    a = pool.model("2018-12")
    assert a is not None and pool.model("2018-12") is a and len(calls) == 1
    assert models.GBM_PARAMS["random_state"] == 0      # the binning subsamples large pools: seeded
    assert list(pool.CACHE_DIR.glob("gbm_*_2018-12.pkl"))
    pool._models.clear()
    b = pool.model("2018-12")                          # read back from the disk
    periods, values = synthetic(n=150, seed=99)
    z, _ = models.transform(values)
    assert np.array_equal(models.gbm(z, 12, periods[0], model=a), models.gbm(z, 12, periods[0], model=b))
    # the backtest of gbm uses the pool's model of the December before each origin, never a local one
    real = models.gbm_fit
    try:
        models.gbm_fit = lambda *a, **k: pytest.fail("trained a local model although there is a pool")
        backtest.run(z, periods[0], "gbm", backtest.schedule(periods)[-4:])
    finally:
        models.gbm_fit = real


def test_pool_cutoffs_and_gaps():
    assert pool.cutoff_for("2025-03") == "2024-12"
    assert pool.cutoff_for("2025-12") == "2025-12"
    periods, values = pool.consecutive(["2020-01", "2020-02", "2020-05", "2020-06", "2020-07"], [1, 2, 3, 4, 5])
    assert periods == ["2020-05", "2020-06", "2020-07"] and list(values) == [3, 4, 5]
    pool.set_loader(lambda: (_ for _ in ()).throw(RuntimeError("database down")))
    assert pool.model("2020-12") is None and service.label("gbm") == models.LOCAL_GBM_LABEL


# --------------------------------------------------------------------------- service
def test_forecast_band_table_and_summary():
    periods, values = synthetic()
    r = service.forecast(values, periods, "ensemble", 18, 0.8)
    assert (r["lo"] <= r["point"]).all() and (r["point"] <= r["hi"]).all()
    rel = np.log(r["hi"]) - np.log(r["point"])
    assert (np.diff(rel) >= -1e-12).all()                # never narrower further ahead
    bt = r["backtest"]
    names = [m["method"] for m in bt["methods"]]
    assert names == list(service.TABLE) and sum(m["chosen"] for m in bt["methods"]) == 1
    for key in ("origins", "mape_h1", "mape_h12", "naive_mape_h1", "naive_mape_h12", "mase_h3", "skill_h3",
                "coverage", "period", "mape", "naive_mape", "summary"):
        assert key in bt
    assert bt["origins"] == backtest.ORIGINS and 0 <= bt["coverage"] <= 1
    assert bt["skill_h1"] == pytest.approx(1 - bt["mase_h1"] / bt["naive_mase_h1"])
    assert "seasonal naive" in bt["summary"]
    value, lo, hi = r["sum_band"](1, 6, observed=5.0)
    assert lo < value < hi and value == pytest.approx(5.0 + r["point"][:6].sum())


def test_wider_interval_is_wider():
    periods, values = synthetic()
    a = service.forecast(values, periods, "ets", 6, 0.8)
    b = service.forecast(values, periods, "ets", 6, 0.95)
    assert (b["hi"] >= a["hi"]).all() and (b["lo"] <= a["lo"]).all()
    assert np.array_equal(a["point"], b["point"])


def test_cache_in_memory_and_on_disk():
    periods, values = synthetic(seed=7)
    t = time.perf_counter()
    a = service.forecast(values, periods, "ensemble", 12, 0.8)
    first = time.perf_counter() - t
    t = time.perf_counter()
    b = service.forecast(values, periods, "ensemble", 12, 0.8)
    assert time.perf_counter() - t < max(0.5, first / 5)
    service._MEM.clear()                                # a restart: the disk cache answers
    c = service.forecast(values, periods, "ensemble", 12, 0.8)
    for x in (b, c):
        assert np.array_equal(a["point"], x["point"]) and np.array_equal(a["hi"], x["hi"])
        assert a["backtest"] == x["backtest"]
    assert list(pool.CACHE_DIR.glob("*.json"))


def test_a_test_year_outside_the_schedule_is_backtested():
    periods, values = synthetic()
    r = service.forecast(values, periods, "ets", 12, 0.8, test_year="2015")
    assert r["backtest"]["period"] == "2015" and r["backtest"]["mape"] is not None
    assert r["backtest"]["naive_mape"] == pytest.approx(backtest.naive_year(values, periods, "2015"))


def test_resolve():
    assert service.resolve(None) == service.resolve("auto") == service.DEFAULT
    assert service.resolve("gbm") == "gbm"


# --------------------------------------------------------------------------- scenarios
def line_data(jump: float):
    """(periods, the rest of the series, a line that jumps by ``jump`` a month from 2025-09)."""
    periods, rest = synthetic(n=200, seed=5)
    _, line = synthetic(n=200, seed=6, trend=0.0)
    line = line * 0.05
    line[periods.index("2025-09"):] += jump
    return periods, rest, line


def test_scenarios_of_a_new_line():
    periods, total, line = line_data(jump=2e8)
    total = total + line
    sc = scenarios.line_scenarios(total, line, periods, scenarios.LINES[0], "ets", 12)
    assert sc is not None and sc["id"] == "smartphones"
    assert sc["recent_extra"] == pytest.approx(2e8, rel=0.3)
    assert np.allclose(sc["continues"] - sc["stops"], sc["recent_extra"])
    only = scenarios.line_scenarios(line, line, periods, scenarios.LINES[0], "ets", 12, only_line=True)
    assert only is not None and (only["stops"] < only["continues"]).all()


def test_no_scenario_when_the_line_is_small():
    periods, total, line = line_data(jump=0.0)
    assert scenarios.line_scenarios(total + line, line, periods, scenarios.LINES[0], "ets", 12) is None
