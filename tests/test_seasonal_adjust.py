# SPDX-License-Identifier: MIT
"""Seasonal adjustment, /insights/series?adjust=true (the "Seasonally adjusted" switch of a monthly line).

adjusted = value - seasonal, the seasonal component of one robust STL fit (period 12) of the slice's whole
monthly history, cut to the months asked for; a series under five years (or with trade in fewer than 60
months) is refused as "too short to adjust". The first version fitted STL on the window alone: for exports to
the United States, 2023-01 to 2026-07 (43 months), "adjusted" was the trend itself in 2023 and from 2025-08.

The helper tests run on synthetic series; the endpoint tests on the local database (skipped without it).
"""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("statsmodels")

import numpy as np  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from statsmodels.tsa.seasonal import STL  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "app" / "backend"
DB = REPO / "data" / "lux_trade.db"
OWN = ("main_api", "database", "sql_alchemy", "pydantic_classes", "bal_stdlib", "routers", "view")


def _drop_backend_modules() -> None:
    for name in list(sys.modules):
        if name.split(".")[0] in OWN:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def backend():
    """(insights module, TestClient or None): the client only with the local database."""
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}" if DB.exists() else "sqlite:///:memory:"
    _drop_backend_modules()
    sys.path.insert(0, str(BACKEND))
    try:
        from routers import insights
        insights.SDMX = None  # never fetch live in tests
        if DB.exists():
            from fastapi.testclient import TestClient
            import main_api
            with TestClient(main_api.app) as c:
                yield insights, c
        else:
            yield insights, None
    finally:
        sys.path.remove(str(BACKEND))
        _drop_backend_modules()
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved


@pytest.fixture(scope="module")
def insights(backend):
    return backend[0]


@pytest.fixture(scope="module")
def client(backend):
    if backend[1] is None:
        pytest.skip("no local database data/lux_trade.db (load the data first)")
    return backend[1]


def _months(first_year: int, n: int) -> list[str]:
    return [f"{first_year + i // 12}-{i % 12 + 1:02d}" for i in range(n)]


def _synthetic(n: int = 120, seed: int = 1) -> list[dict]:
    """A level with a slow rise, a yearly pattern, noise and two one-off spikes."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    pattern = np.array([-8, -5, 2, 0, -1, 12, 0, -9, -3, 8, -2, -4], float)
    values = 100 + 0.2 * t + pattern[t % 12] + rng.normal(0, 3, n)
    if n > 40:
        values[40] += 40
    if n > 77:
        values[77] -= 30
    return [{"period": p, "value": float(v)} for p, v in zip(_months(2015, n), values)]


# --------------------------------------------------------------------------- the helper
def test_adjusted_is_value_minus_seasonal_of_the_full_history_fit(insights):
    history = _synthetic()
    window = [dict(r) for r in history[-43:]]
    insights._adjust_rows(window, history)
    ref = STL(np.array([r["value"] for r in history]), period=12, robust=True).fit()
    for k, r in enumerate(window):
        i = len(history) - 43 + k
        assert r["adjusted"] == pytest.approx(r["value"] - r["seasonal"], abs=1e-9)
        assert r["seasonal"] == pytest.approx(ref.seasonal[i], abs=1e-9)
        assert r["trend"] == pytest.approx(ref.trend[i], abs=1e-9)
    # the irregular part stays in: the adjusted line is not the trend
    assert not any(abs(r["adjusted"] - r["trend"]) < 1e-6 for r in window)


def test_a_window_is_not_fitted_on_its_own(insights):
    history = _synthetic()
    window = [dict(r) for r in history[-43:]]
    insights._adjust_rows(window, history)
    alone = STL(np.array([r["value"] for r in window]), period=12, robust=True).fit()
    # the old way: on 43 months robust STL gives many months a zero remainder (adjusted = trend)
    assert (np.abs(alone.resid) < 1e-2).sum() > 10  # a level of about 100: a remainder of nothing
    assert max(abs(r["seasonal"] - s) for r, s in zip(window, alone.seasonal)) > 1


def test_a_month_without_a_row_counts_as_zero(insights):
    history = _synthetic()
    gapped = [r for i, r in enumerate(history) if i != 50]
    rows = [dict(r) for r in gapped]
    insights._adjust_rows(rows, gapped)
    filled = np.array([r["value"] for r in history])
    filled[50] = 0
    ref = STL(filled, period=12, robust=True).fit()
    assert rows[60]["seasonal"] == pytest.approx(ref.seasonal[61], abs=1e-9)
    assert all(r["period"] != history[50]["period"] for r in rows)


@pytest.mark.parametrize("n", [24, 36, 43, 59])
def test_a_short_history_is_too_short_to_adjust(insights, n):
    history = _synthetic(n)
    rows = [dict(r) for r in history]
    with pytest.raises(HTTPException) as e:
        insights._adjust_rows(rows, history)
    assert e.value.status_code == 422
    assert e.value.detail.startswith("Too short to adjust")
    assert f"{n} months" in e.value.detail
    assert not any("adjusted" in r or "trend" in r for r in rows)


def test_sixty_months_are_enough(insights):
    history = _synthetic(60)
    rows = [dict(r) for r in history]
    insights._adjust_rows(rows, history)
    assert all(r["adjusted"] == pytest.approx(r["value"] - r["seasonal"]) for r in rows)


def test_a_sparse_history_is_too_short_to_adjust(insights):
    history = [r for i, r in enumerate(_synthetic(120)) if i % 3 == 0]  # trade in 40 of 118 months
    with pytest.raises(HTTPException) as e:
        insights._adjust_rows([dict(r) for r in history], history)
    assert e.value.status_code == 422
    assert "Too short to adjust" in e.value.detail and "only 40 of its" in e.value.detail


def test_yearly_data_is_not_adjusted(insights):
    history = [{"period": str(1999 + i), "value": float(i)} for i in range(70)]
    with pytest.raises(HTTPException) as e:
        insights._adjust_rows([dict(r) for r in history], history)
    assert e.value.status_code == 422 and "monthly" in e.value.detail


# --------------------------------------------------------------------------- the endpoint, real data
def _get(client, **params):
    r = client.get("/insights/series", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _full_fit(client, **filters):
    full = _get(client, **filters)
    values = np.array([r["value"] for r in full])
    return {r["period"]: i for i, r in enumerate(full)}, values, STL(values, period=12, robust=True).fit()


@pytest.mark.parametrize("filters", [
    {"flow": "EXPORT", "freq": "M", "partner": "US", "start": "2023-01", "end": "2026-07"},
    {"flow": "EXPORT", "freq": "M", "partner": "DE", "start": "2023-01"},
    {"flow": "EXPORT", "freq": "M"},
    {"flow": "IMPORT", "freq": "M", "product": "72", "start": "2024-01"},
])
def test_the_window_is_cut_from_the_full_history_fit(client, filters):
    rows = _get(client, **filters, adjust="true")
    if not rows:
        pytest.skip("slice not in the local database")
    index, values, ref = _full_fit(client, **{k: v for k, v in filters.items() if k not in ("start", "end")})
    if len(values) < 60:
        pytest.skip("history too short in the local database")
    for r in rows:
        i = index[r["period"]]
        assert r["adjusted"] == pytest.approx(r["value"] - r["seasonal"], rel=1e-12, abs=1e-6)
        assert r["seasonal"] == pytest.approx(ref.seasonal[i], rel=1e-9, abs=1e-3)
        assert r["trend"] == pytest.approx(ref.trend[i], rel=1e-9, abs=1e-3)


def test_us_exports_since_2023_are_not_the_trend(client):
    rows = _get(client, flow="EXPORT", freq="M", partner="US", start="2023-01", end="2026-07", adjust="true")
    if len(rows) < 40:
        pytest.skip("US exports not in the local database")
    same = [r["period"] for r in rows if abs(r["adjusted"] - r["trend"]) < 1e-6 * max(1.0, abs(r["value"]))]
    assert not same, f"adjusted equals the trend in {same}"


def test_the_endpoint_refuses_a_short_series(client):
    r = client.get("/insights/series", params={"flow": "EXPORT", "freq": "M", "partner": "AG", "adjust": "true"})
    if r.status_code == 200 and not r.json():
        pytest.skip("partner AG not in the local database")
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("Too short to adjust")


def test_the_endpoint_refuses_yearly_data(client):
    r = client.get("/insights/series", params={"flow": "EXPORT", "freq": "A", "adjust": "true"})
    assert r.status_code == 422 and "monthly" in r.json()["detail"]


def test_without_adjust_nothing_changes(client):
    rows = _get(client, flow="EXPORT", freq="M", start="2025-01", end="2025-06")
    assert rows and all(set(r) == {"period", "value"} for r in rows)


# --------------------------------------------------------------------------- /trends/analyze and /insights/trend
@pytest.fixture(scope="module")
def analysis(backend):
    from trends import analysis as mod
    return mod


def test_analysis_fits_the_history_and_keeps_the_span(analysis):
    history = _synthetic()
    window = history[-43:]
    out = analysis.analyze_series(window, history=history, min_size=12, penalty=1.5)
    ref = STL(np.array([r["value"] for r in history]), period=12, robust=True).fit()
    assert out["method"] == "stl" and out["adjustment_note"] is None
    assert (out["fit_start"], out["fit_end"]) == (history[0]["period"], history[-1]["period"])
    assert [p["period"] for p in out["points"]] == [r["period"] for r in window]
    for k, pt in enumerate(out["points"]):
        i = len(history) - 43 + k
        assert pt["trend"] == pytest.approx(ref.trend[i], abs=1e-9)
        assert pt["seasonal"] == pytest.approx(ref.seasonal[i], abs=1e-9)
        assert pt["adjusted"] == pytest.approx(pt["value"] - pt["seasonal"], abs=1e-9)
    assert out["trend_start"] == pytest.approx(ref.trend[len(history) - 43])
    # change points: searched on the span's own values
    cps = analysis.detect_changepoints(np.array([r["value"] for r in window]), min_size=12, penalty=1.5)
    assert [c["index"] for c in out["changepoints"]] == cps


def test_analysis_of_a_short_history_says_too_short(analysis):
    short = _synthetic(48)
    out = analysis.analyze_series(short)
    assert out["method"] == "moving_average" and out["adjustment_note"].startswith("Too short to adjust")
    assert all(p["adjusted"] is None and p["seasonal"] is None for p in out["points"])


def test_trends_analyze_endpoint_cuts_the_full_fit(client):
    r = client.get("/trends/analyze", params={"flow": "EXPORT", "freq": "M", "partner": "US", "start": "2023-01",
                                              "penalty": 1.5, "min_size": 12})
    assert r.status_code == 200, r.text
    d = r.json()
    if d["n_points"] < 40:
        pytest.skip("US exports not in the local database")
    index, values, ref = _full_fit(client, flow="EXPORT", freq="M", partner="US")
    assert d["fit_start"] == next(iter(index))
    for pt in d["points"]:
        assert pt["trend"] == pytest.approx(ref.trend[index[pt["period"]]], rel=1e-9, abs=1e-3)
        assert pt["adjusted"] == pytest.approx(pt["value"] - pt["seasonal"], rel=1e-12, abs=1e-6)
    assert d["relative_change"] == pytest.approx(d["trend_end"] / d["trend_start"] - 1)


def test_trends_analyze_short_series_says_too_short(client):
    r = client.get("/trends/analyze", params={"flow": "EXPORT", "freq": "M", "partner": "AG"})
    if r.status_code != 200:
        pytest.skip(r.text)
    assert r.json()["adjustment_note"].startswith("Too short to adjust")


def test_trend_line_uses_the_full_fit_and_measures_the_span(client):
    r = client.get("/insights/trend", params={"flow": "EXPORT", "freq": "M", "partner": "US", "start": "2023",
                                              "end": "2026-07"})
    assert r.status_code == 200, r.text
    d = r.json()
    if d["n_obs"] < 40:
        pytest.skip("US exports not in the local database")
    index, values, ref = _full_fit(client, flow="EXPORT", freq="M", partner="US")
    assert d["method"] == "stl" and "whole monthly history" in d["method_label"]
    fitted = [f["value"] for f in d["fitted"]]
    assert fitted == pytest.approx([ref.trend[index[f["period"]]] for f in d["fitted"]], rel=1e-9)
    years = (len(fitted) - 1) / 12
    assert d["cagr"] == pytest.approx((fitted[-1] / fitted[0]) ** (1 / years) - 1)
