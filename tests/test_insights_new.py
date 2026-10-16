# SPDX-License-Identifier: MIT
"""The /insights endpoints written for agents: trend, trends, forecast, events, flows, and (contract
1.1) matrix, tree, the movers totals, and the /trends endpoints the view pre-flights.

Runs app/backend in process (FastAPI TestClient) on the local database data/lux_trade.db; skipped
when it is not there. Live fetches from Eurostat are switched off.
"""
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("statsmodels")

from fastapi.testclient import TestClient  # noqa: E402

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
    if not DB.exists():
        pytest.skip("no local database data/lux_trade.db (load the data first)")
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
    _drop_backend_modules()
    sys.path.insert(0, str(BACKEND))
    try:
        import main_api
        from routers import insights
        insights.SDMX = None  # never fetch live in tests
        with TestClient(main_api.app) as c:
            yield c, insights
    finally:
        sys.path.remove(str(BACKEND))
        _drop_backend_modules()
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved


@pytest.fixture(scope="module")
def client(backend):
    return backend[0]


def get(client, endpoint, **params):
    r = client.get(f"/insights/{endpoint}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- forecast
def _check_band(rows):
    assert rows
    for row in rows:
        assert row["lo"] <= row["value"] <= row["hi"], row
        assert row["low"] == row["lo"] and row["high"] == row["hi"]


@pytest.fixture(scope="module", autouse=True)
def forecast_cache():
    """The fitted forecasts of these tests go to .pytest_cache (kept between runs), not to the
    cache of the running app."""
    sys.path.insert(0, str(BACKEND))
    try:
        from forecasting import pool
    except ImportError:          # scikit-learn missing: the forecast tests fail with a clear error
        sys.path.remove(str(BACKEND))
        yield None
        return
    saved = pool.CACHE_DIR
    pool.CACHE_DIR = REPO / ".pytest_cache" / "forecast"
    yield pool
    pool.CACHE_DIR = saved
    sys.path.remove(str(BACKEND))


def test_forecast_monthly_band_holds_the_value(client):
    f = get(client, "forecast", flow="EXPORT", freq="M", horizon=6)
    assert f["freq"] == "M" and f["method"] == "ensemble" and f["level"] == 0.8
    assert f["interval_method"] == "conformal" and "Holt-Winters" in f["method_label"]
    assert len(f["forecast"]) == 6 and len(f["reference"]) == 6
    _check_band(f["forecast"])
    assert f["forecast"][0]["period"] > f["history"][-1]["period"]
    assert f["rows"][-1] == f["forecast"][-1]
    widths = [r["hi"] / r["value"] for r in f["forecast"]]
    assert all(b >= a - 1e-9 for a, b in zip(widths, widths[1:]))   # the band never narrows further ahead


def test_forecast_annual_is_observed_plus_forecast(client):
    f = get(client, "forecast", flow="EXPORT", freq="M", horizon=12, method="ets", interval=0.8, aggregate="A")
    assert f["freq"] == "A" and f["method"] == "ets"
    year = f["rows"][-1]
    _check_band(f["forecast"])
    assert year["observed_months"] + year["forecast_months"] == 12
    assert year["period"] == str(int(f["history"][-1]["period"]) + 1)
    assert year["value"] > year["observed"]
    bt = f["backtest"]
    for key in ("origins", "mape_h1", "mape_h12", "naive_mape_h1", "naive_mape_h12", "period", "mape", "naive_mape",
                "mase_h3", "naive_mase_h3", "skill_h3", "coverage", "methods", "summary"):
        assert key in bt
    assert bt["origins"] == 20 and 0 <= bt["mape"] < 1 and 0 <= bt["coverage"] <= 1
    beats = bt["mase_h12"] < bt["naive_mase_h12"]
    assert ("did not beat" in f["caveat"]) is (not beats)
    assert isinstance(f["method_label"], str) and f["n_obs"] >= 36


def test_forecast_backtest_table_compares_every_method(client):
    f = get(client, "forecast", flow="IMPORT", freq="M", horizon=3)
    table = {m["method"]: m for m in f["backtest"]["methods"]}
    assert set(table) >= {"snaive", "ets", "theta", "gbm", "ensemble"}
    assert table["ensemble"]["chosen"] and not table["ets"]["chosen"]
    for row in table.values():
        assert row["mase_h1"] > 0 and row["mape_h12"] > 0 and isinstance(row["label"], str)
    assert table["snaive"]["skill"] == 0
    # the ensemble is the backtest's choice: better than the seasonal naive forecast a quarter ahead
    assert table["ensemble"]["mase_h3"] < table["snaive"]["mase_h3"]
    assert "seasonal naive" in f["backtest"]["summary"]
    assert "every HS2 chapter" in table["gbm"]["label"]  # trained on the pool, not on one series


def test_forecast_methods_are_selectable(client):
    for method in ("snaive", "stl_ets", "airline", "theta", "gbm"):
        f = get(client, "forecast", flow="EXPORT", freq="M", horizon=2, method=method)
        assert f["method"] == method and len(f["forecast"]) == 2
        _check_band(f["forecast"])
    auto = get(client, "forecast", flow="EXPORT", freq="M", horizon=2, method="auto")
    assert auto["method"] == "ensemble"


def test_forecast_smartphone_scenarios(client):
    f = get(client, "forecast", flow="EXPORT", freq="M", horizon=12, aggregate="A")
    sc = {s["case"]: s for s in f["scenarios"]}
    assert set(sc) == {"stops", "continues"}
    stops, goes_on = sc["stops"]["rows"][-1], sc["continues"]["rows"][-1]
    assert stops["period"] == f["rows"][-1]["period"] and stops["value"] < goes_on["value"]
    assert stops["observed"] == goes_on["observed"] == f["rows"][-1]["observed"]
    assert sc["stops"]["since"] == "2025-09" and sc["stops"]["recent_extra"] > 0
    assert "stops or continues" in f["caveat"]
    _check_band(sc["continues"]["rows"])
    monthly = get(client, "forecast", flow="EXPORT", freq="M", horizon=4)
    assert all(len(s["rows"]) == 4 for s in monthly["scenarios"])
    # chapters by month exist against the world only: no scenario for one partner
    assert get(client, "forecast", flow="EXPORT", freq="M", horizon=2, partner="DE")["scenarios"] == []


def test_forecast_is_deterministic(backend, forecast_cache, tmp_path):
    client, insights = backend
    from forecasting import service
    params = dict(flow="IMPORT", freq="A", horizon=12)
    insights._FORECAST_CACHE.clear()
    a = get(client, "forecast", **params)
    insights._FORECAST_CACHE.clear()
    service._MEM.clear()
    saved, forecast_cache.CACHE_DIR = forecast_cache.CACHE_DIR, tmp_path   # recompute the series
    try:
        b = get(client, "forecast", **params)
    finally:
        forecast_cache.CACHE_DIR = saved
    assert a == b


def test_forecast_refuses_bad_parameters(client):
    assert client.get("/insights/forecast", params={"flow": "EXPORT", "method": "prophet"}).status_code == 400
    assert client.get("/insights/forecast", params={"flow": "EXPORT", "horizon": 99}).status_code == 400
    assert client.get("/insights/forecast", params={"partner": "XX"}).status_code == 400


def test_forecast_of_a_line_too_young_for_a_backtest_says_so(client):
    # the smartphone line (CN8 85171300) has monthly data from 2022 only: too short for a tested forecast
    r = client.get("/insights/forecast", params={"flow": "EXPORT", "product": "85171300", "freq": "M",
                                                 "horizon": 12, "aggregate": "A"})
    assert r.status_code in (400, 422), r.text      # never a 500
    assert "history" in r.json()["detail"] or "consecutive months" in r.json()["detail"]


# --------------------------------------------------------------------------- trend, trends
def test_trend_slope_sign(client):
    up = get(client, "trend", flow="EXPORT", freq="A", start="2020", end="2022")
    down = get(client, "trend", flow="EXPORT", freq="A", start="2015", end="2018")
    assert up["method"] == "ols_log" and up["slope_pct_per_period"] > 0 and up["cagr"] > 0
    assert down["slope_pct_per_period"] < 0 and down["slope_per_year"] < 0
    assert len(up["fitted"]) == up["n_obs"] == 3 and 0 <= up["r2"] <= 1
    assert up["rows"][0]["period"] == "2020" and "fitted" in up["rows"][0]


def test_trend_monthly_uses_stl(client):
    t = get(client, "trend", flow="EXPORT", freq="M", start="2018")
    assert t["method"] == "stl" and t["start"] == "2018-01" and len(t["fitted"]) == t["n_obs"]
    assert client.get("/insights/trend", params={"flow": "EXPORT", "start": "2024", "end": "2024"}).status_code == 400


def test_trends_rank_by_growth(client):
    t = get(client, "trends", by="partner", flow="EXPORT", limit=5)
    growth = [i["growth"] for i in t["items"]]
    assert growth == sorted(growth, reverse=True) and len(growth) == 5
    # Defaults to the whole annual span of the database (1999 with the history top-up, 2015 without).
    assert t["start"] in ("1999", "2015") and t["end"] == "2025" and t["threshold"] == 0.01
    assert all(i["share"] >= 0 for i in t["items"])
    falling = [i["growth"] for i in t["falling"]]
    assert falling == sorted(falling)
    alias = get(client, "trends", by="partner", flow="EXPORT", limit=5, base="2015", period="2025")
    same = get(client, "trends", by="partner", flow="EXPORT", limit=5, start="2015", end="2025")
    assert [i["code"] for i in alias["items"]] == [i["code"] for i in same["items"]]


# --------------------------------------------------------------------------- events
def test_events_return_covid_and_the_eu_uk_agreement(client):
    ids = [e["id"] for e in get(client, "events", start="2015")]
    assert "covid-19-2020" in ids and "eu-uk-tca-2021" in ids
    covid = next(e for e in get(client, "events", start="2020", end="2020") if e["id"] == "covid-19-2020")
    assert covid["at"] == "2020-03" and covid["category"] == "pandemic" and covid["source"].startswith("https://")
    gb = [e["id"] for e in get(client, "events", partner="GB")]
    assert "eu-uk-tca-2021" in gb and "ceta-2017" not in gb
    tariffs = get(client, "events", kind="tariff")
    assert tariffs and all(e["kind"] == "tariff" for e in tariffs)
    january = [e["id"] for e in get(client, "events", **{"from": "2021-01", "to": "2021-01"})]
    assert "eu-uk-tca-2021" in january and "us-steel-tariffs-2018" in january  # a span overlapping it
    assert "covid-19-2020" not in january and "ever-given-2021" not in january


def test_events_file_is_complete():
    rows = json.loads((BACKEND / "events.json").read_text(encoding="utf-8"))
    kinds = {"agreement", "dispute", "tariff", "sanction", "war", "disaster", "pandemic", "policy", "shock"}
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["kind"] in kinds and r["source_url"].startswith("https://") and r["confidence"] == "curated"
        assert set(r["scope"]) == {"partners", "products", "flows"}
        assert not r["end_date"] or r["end_date"] >= r["date"]
        # each row cites a page about that event, not the generic list of trade negotiations
        assert not r["source_url"].endswith("/negotiations-and-agreements_en"), r["id"]


# --------------------------------------------------------------------------- flows
def test_flows_totals_equal_the_rankings(client):
    f = get(client, "flows", period="2025", max_items=8)
    imports = get(client, "ranking", by="partner", period="2025", flow="IMPORT", max_items=8)
    exports = get(client, "ranking", by="partner", period="2025", flow="EXPORT", max_items=8)
    assert f["in"]["total"] == imports["total"] and f["out"]["total"] == exports["total"]
    assert [i["code"] for i in f["in"]["items"]] == [i["code"] for i in imports["items"]]
    assert f["balance"] == pytest.approx(exports["total"] - imports["total"])
    ladder = get(client, "ladder", by="partner", period="2025", flow="IMPORT")
    assert f["partner_counts"]["in"] == ladder["n_items"]
    assert len(f["out"]["items"]) <= 8 and f["loaded"] is True


def test_flows_with_previous(client):
    f = get(client, "flows", period="2025", previous="2024")
    item = f["out"]["items"][0]
    assert item["change"] == pytest.approx(item["value"] - item["previous"])
    assert f["previous_balance"] == pytest.approx(f["out"]["previous_total"] - f["in"]["previous_total"])


# --------------------------------------------------------------------------- filters
def test_a_typo_in_the_grouped_dimension_is_a_400(client):
    # /ranking groups by partner, so a partner filter is not applied; a typo there still says so.
    r = client.get("/insights/ranking", params={"by": "partner", "period": "2025", "partner": "ZZ"})
    assert r.status_code == 400 and "ZZ" in r.text
    assert client.get("/insights/ranking", params={"by": "partner", "period": "2025", "partner": "FR"}).status_code == 200


# --------------------------------------------------------------------------- months without freq
def test_a_month_without_freq_reads_the_monthly_data(client):
    rows = get(client, "series", flow="EXPORT", start="2025-01", end="2025-03")
    assert [r["period"] for r in rows] == ["2025-01", "2025-02", "2025-03"]
    explicit = get(client, "series", flow="EXPORT", freq="M", start="2025-01", end="2025-03")
    assert rows == explicit
    # years keep the annual default
    assert [r["period"] for r in get(client, "series", flow="EXPORT", start="2024", end="2025")] == ["2024", "2025"]


# --------------------------------------------------------------------------- movers totals (1.1)
def test_movers_totals_are_the_selections_totals(client):
    m = get(client, "movers", by="product", period="2025", previous="2024", flow="EXPORT", limit=5)
    now = get(client, "ranking", by="product", period="2025", flow="EXPORT")
    before = get(client, "ranking", by="product", period="2024", flow="EXPORT")
    assert m["total"] == now["total"] and m["previous_total"] == before["total"]
    by_partner = get(client, "movers", by="partner", period="2025", previous="2024", flow="EXPORT")
    assert by_partner["total"] == m["total"]  # the same flow, whatever it is broken down by
    assert all(r["change"] > 0 for r in m["up"]) and all(r["change"] < 0 for r in m["down"])
    assert not {r["code"] for r in m["up"]} & {r["code"] for r in m["down"]}
    assert len(m["up"]) <= 5 and len(m["down"]) <= 5


def test_movers_totals_when_drilling(client):
    m = get(client, "movers", by="product", period="2025", previous="2024", flow="EXPORT", parent="85")
    ranking = get(client, "ranking", by="product", period="2025", flow="EXPORT", parent="85")
    assert m["total"] == ranking["total"]  # the parent's value
    assert all(r["code"].startswith("85") for r in m["up"] + m["down"])


def test_the_waterfall_facts_resolve(client):
    sys.path.insert(0, str(BACKEND))
    try:
        from view.validate import resolve_path
    finally:
        sys.path.remove(str(BACKEND))
    m = get(client, "movers", by="product", period="2025", previous="2024", flow="EXPORT", limit=8)
    for path in ("total", "previous_total", "up.0.change"):
        assert isinstance(resolve_path(m, path), float), path


# --------------------------------------------------------------------------- matrix (1.1)
def test_matrix_shape_and_values(client):
    m = get(client, "matrix", by="partner", start="2015", end="2025", flow="EXPORT", max_items=6)
    assert m["by"] == "partner" and m["measure"] == "value" and m["loaded"] is True
    assert m["periods"] == [str(y) for y in range(2015, 2026)]
    assert len(m["rows"]) == 6
    totals = [r["total"] for r in m["rows"]]
    assert totals == sorted(totals, reverse=True)
    for r in m["rows"]:
        assert len(r["values"]) == len(m["periods"]) and r["name"] and r["code"] != "WORLD"
    ranking = get(client, "ranking", by="partner", period="2025", flow="EXPORT", max_items=60)
    de = next(r for r in m["rows"] if r["code"] == "DE")
    assert de["values"][-1] == next(i["value"] for i in ranking["items"] if i["code"] == "DE")


def test_matrix_measures(client):
    base = dict(by="product", start="2020", end="2025", flow="EXPORT", max_items=8)
    value = get(client, "matrix", **base)
    share = get(client, "matrix", measure="share", **base)
    rank = get(client, "matrix", measure="rank", **base)
    growth = get(client, "matrix", measure="growth", **base)
    codes = [r["code"] for r in value["rows"]]
    assert codes == [r["code"] for r in share["rows"]] == [r["code"] for r in growth["rows"]]
    for j, period in enumerate(share["periods"]):
        col = [r["values"][j] for r in share["rows"] if r["values"][j] is not None]
        assert all(0 <= v <= 1 for v in col) and sum(col) <= 1 + 1e-9
        total = get(client, "ranking", by="product", period=period, flow="EXPORT")["total"]
        assert share["rows"][0]["values"][j] == pytest.approx(value["rows"][0]["values"][j] / total)
    for r in rank["rows"]:
        assert all(v is None or (isinstance(v, int) and v >= 1) for v in r["values"])
    ranks_2025 = [r["values"][-1] for r in rank["rows"] if r["values"][-1] is not None]
    assert len(ranks_2025) == len(set(ranks_2025))  # one rank per code in a period
    for rv, rg in zip(value["rows"], growth["rows"]):
        assert rg["values"][0] is None
        a, b = rv["values"][-2], rv["values"][-1]
        if a and b is not None:
            assert rg["values"][-1] == pytest.approx(b / a - 1)


def test_matrix_rank_one_is_the_largest(client):
    rank = get(client, "matrix", by="partner", start="2024", end="2025", flow="EXPORT", measure="rank", max_items=3)
    top = get(client, "ranking", by="partner", period="2025", flow="EXPORT", max_items=1)["items"][0]["code"]
    assert next(r for r in rank["rows"] if r["code"] == top)["values"][-1] == 1


def test_matrix_months_and_drill(client):
    m = get(client, "matrix", by="product", start="2025-01", end="2025-06", flow="EXPORT", max_items=3)
    assert m["freq"] == "M" and m["periods"] == [f"2025-0{i}" for i in range(1, 7)]
    d = get(client, "matrix", by="product", start="2023", end="2025", flow="EXPORT", parent="85", max_items=4)
    assert d["rows"] and all(r["code"].startswith("85") and len(r["code"]) == 4 for r in d["rows"])


def test_matrix_refuses_bad_parameters(client):
    ok = dict(by="partner", start="2015", end="2025")
    assert client.get("/insights/matrix", params={**ok, "measure": "volume"}).status_code == 400
    assert client.get("/insights/matrix", params={**ok, "start": "2025", "end": "2015"}).status_code == 400
    assert client.get("/insights/matrix", params={**ok, "max_items": 0}).status_code == 400
    assert client.get("/insights/matrix", params={**ok, "by": "colour"}).status_code == 400
    assert client.get("/insights/matrix", params={"by": "partner", "start": "2015"}).status_code == 422


# --------------------------------------------------------------------------- tree (1.1)
def _nodes(children):
    for c in children:
        yield c
        yield from _nodes(c["children"])


def test_tree_shape_and_sums(client):
    t = get(client, "tree", by="product", period="2025", flow="EXPORT", depth=2)
    assert t["by"] == "product" and t["period"] == "2025" and t["depth"] == 2 and t["loaded"] is True
    total = get(client, "ranking", by="product", period="2025", flow="EXPORT")["total"]
    assert t["total"] == total
    assert sum(c["value"] for c in t["children"]) == pytest.approx(total)
    for node in _nodes(t["children"]):
        assert set(node) >= {"code", "name", "value", "share", "children"}
        assert node["share"] == pytest.approx(node["value"] / total)
        if node["children"]:
            assert sum(c["value"] for c in node["children"]) == pytest.approx(node["value"])
            assert all(c["code"] == "OTHER" or c["code"].startswith(node["code"]) for c in node["children"])
    hs2 = [c for c in t["children"] if c["code"] != "OTHER"]
    assert all(len(c["code"]) == 2 and c["share"] >= 0.01 for c in hs2)  # the ladder: 1% at HS2
    deeper = [n for c in hs2 for n in c["children"] if n["code"] != "OTHER"]
    assert deeper and all(len(n["code"]) == 4 and n["share"] >= 0.001 for n in deeper)  # 0.1% at HS4
    assert all(n["children"] == [] for n in deeper)


def test_tree_threshold_root_and_depth(client):
    auto = get(client, "tree", by="product", period="2025", flow="EXPORT", depth=1)
    coarse = get(client, "tree", by="product", period="2025", flow="EXPORT", depth=1, threshold=0.05)
    assert len(coarse["children"]) < len(auto["children"])
    assert all(c["share"] >= 0.05 for c in coarse["children"] if c["code"] != "OTHER")
    other = next(c for c in coarse["children"] if c["code"] == "OTHER")
    assert other["count"] > 0 and other["children"] == []
    phones = get(client, "tree", by="product", period="2025", flow="EXPORT", depth=3, product="85")
    assert phones["root"] == "85" and phones["depth"] == 3
    assert all(c["code"].startswith("85") for c in phones["children"] if c["code"] != "OTHER")
    assert any(len(n["code"]) == 8 for n in _nodes(phones["children"]))  # CN8 under the HS6


def test_tree_falls_back_to_the_levels_loaded(client):
    # Before 2015 the loader keeps the chapters (headings only where a question fetched them on demand): the
    # tree says how deep it could go, never deeper than asked.
    t = get(client, "tree", by="product", period="2010", flow="EXPORT", depth=2)
    assert t["requested_depth"] == 2 and t["children"] and 1 <= t["depth"] <= 2


def test_tree_refuses_bad_parameters(client):
    ok = dict(by="product", period="2025")
    assert client.get("/insights/tree", params={**ok, "depth": 4}).status_code == 400
    assert client.get("/insights/tree", params={**ok, "threshold": "lots"}).status_code == 400
    assert client.get("/insights/tree", params={**ok, "threshold": 1.5}).status_code == 400
    assert client.get("/insights/tree", params={**ok, "by": "partner"}).status_code == 400
    assert client.get("/insights/tree", params={**ok, "product": "85,84"}).status_code == 400
    assert client.get("/insights/tree", params={**ok, "product": "XYZ"}).status_code == 400


# --------------------------------------------------------------------------- /trends, as pre-flighted
def test_trends_endpoints_answer_a_preflight(client):
    a = client.get("/trends/analyze", params={"flow": "EXPORT", "freq": "M", "start": "1999-01", "end": "2026-06",
                                              "penalty": 1.5, "min_size": 12})
    assert a.status_code == 200 and a.json().get("loaded") is not False
    assert "2009-01" in [c["period"] for c in a.json()["changepoints"]]
    h = client.get("/trends/seasonal-heatmap", params={"flow": "EXPORT", "freq": "M", "top": 5})
    assert h.status_code == 200 and h.json()["cycle"] == 12 and h.json()["rows"]
    # months as bounds are enough to read the monthly data
    h2 = client.get("/trends/seasonal-heatmap", params={"flow": "EXPORT", "start": "2015-01", "top": 3})
    assert h2.status_code == 200 and h2.json()["rows"]


def test_trends_analyze_names_the_goods_and_a_possible_change(client):
    base = {"flow": "EXPORT", "freq": "M", "penalty": 1.5, "min_size": 12}
    assert client.get("/trends/analyze", params=base).json()["goods"] == "goods"
    assert client.get("/trends/analyze", params={**base, "product": "40"}).json()["goods"] == "rubber"
    assert client.get("/trends/analyze", params={**base, "product": "84"}).json()["goods"] == "machinery"
    assert client.get("/trends/analyze", params={**base, "product": "40,84"}).json()["goods"] == "rubber and machinery"
    # China's imports: the trend rose from a few million to tens of millions with a hump in between; the
    # change is the trend's own start to end, never below -100%, and the direction agrees with its sign
    a = client.get("/trends/analyze", params={**base, "flow": "IMPORT", "partner": "CN"}).json()
    assert a["relative_change"] is None or a["relative_change"] > -1
    assert a["relative_change"] == pytest.approx(a["trend_end"] / a["trend_start"] - 1)
    assert a["trend_end"] > a["trend_start"] and a["direction"] in ("increasing", "volatile")
    assert "Oct" not in (a["seasonality_summary"] or "").replace("October", "")
    assert a["trend_summary"].startswith("Over the span")


def test_trends_options_have_clean_picker_labels(client):
    o = client.get("/trends/options").json()
    labels = {p["code"]: p["label"] for p in o["partners"]}
    for code, label in labels.items():
        assert label.count("(") == label.count(")"), label
        assert ";" not in label, label
    assert labels.get("PS", "Occupied Palestinian Territory") == "Occupied Palestinian Territory"
    groups = {p["code"]: p.get("group") for p in o["partners"]}
    for code in ("YU", "CS", "AN"):
        if code in groups:
            assert groups[code] == "former"
    for code in ("QV", "QW", "QS"):
        if code in groups:
            assert groups[code] == "other"
    assert not any("confidential" in p["label"].lower() for p in o["products"])


def test_ranking_reaches_lines_two_levels_below_the_parent(client):
    # A heading's 8-digit lines (7216 -> 7216xxxx) are its descendants, not its children: matched by code prefix.
    deep = get(client, "ranking", by="product", period="2025", flow="EXPORT", partner="US", parent="7216", level=4,
               threshold=0, max_items=5)
    one = get(client, "ranking", by="product", period="2025", flow="EXPORT", partner="US", parent="721633", level=4,
              threshold=0, max_items=5)
    codes = [i["code"] for i in deep["items"]]
    assert codes and all(c.startswith("7216") and len(c) == 8 for c in codes)
    assert one["items"][0]["code"] in codes
