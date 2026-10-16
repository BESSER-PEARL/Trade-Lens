# SPDX-License-Identifier: MIT
"""Quantities next to values (contract 1.8): measure=value|weight|units|all on /insights/series, /ranking,
/ranking/records, /movers and /compare; the unit of each response; the unit values computed by the backend; a
missing quantity never shown as 0; every default unchanged.

Runs app/backend in process on the local database data/lux_trade.db (skipped when it is not there, or was loaded
before the quantities); live fetches are switched off. Figures checked against Eurostat on 15 October 2026.
"""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

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
            r = c.get("/insights/series", params={"flow": "EXPORT", "product": "8703", "measure": "units",
                                                  "start": "2025", "end": "2025"})
            if r.status_code != 200 or not r.json():
                pytest.skip("data/lux_trade.db was loaded before the quantities")
            yield c, insights
    finally:
        sys.path.remove(str(BACKEND))
        _drop_backend_modules()
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved


def get(client, endpoint, status=200, **params):
    r = client.get(f"/insights/{endpoint}", params=params)
    assert r.status_code == status, r.text
    return r.json()


def test_cars_are_counted_in_vehicles_with_their_value_and_value_per_car(backend):
    client, _ = backend
    [row] = get(client, "series", flow="EXPORT", product="8703", measure="all", start="2025", end="2025")
    # Eurostat: the 28 CN8 lines of 8703 count 37,654 vehicles, €826,875,433, 1,224,642.98 x 100 kg
    assert row["units"] == 37_654 and row["unit"] == "items"
    assert row["value"] == pytest.approx(826_875_433) and row["weight"] == pytest.approx(122_464.298)
    assert row["eur_per_unit"] == pytest.approx(826_875_433 / 37_654)
    assert row["eur_per_tonne"] == pytest.approx(826_875_433 / 122_464.298)
    env = get(client, "series", flow="EXPORT", product="8703", measure="units", start="2025", end="2025",
              envelope="true")
    assert env["unit"] == "items" and env["unit_name"] == "Number of items" and env["summed_lines"] == 28
    assert env["rows"] == [{"period": "2025", "value": 37_654, "unit": "items", "value_eur": pytest.approx(826_875_433)}]


def test_weight_is_in_tonnes_for_any_product_and_units_say_why_they_are_missing(backend):
    client, _ = backend
    [total] = get(client, "series", flow="EXPORT", measure="weight", start="2025", end="2025")
    assert total["unit"] == "t" and total["value"] == pytest.approx(6_912_985.354)   # 69,129,853.54 x 100 kg
    r = client.get("/insights/series", params={"flow": "EXPORT", "measure": "units"})
    assert r.status_code == 422 and "many different units" in r.text
    r = client.get("/insights/series", params={"flow": "EXPORT", "product": "72163390", "measure": "units",
                                               "start": "2025", "end": "2025"})
    assert r.status_code == 422 and "weight (tonnes) only" in r.text
    [steel] = get(client, "series", flow="EXPORT", product="72", measure="all", start="2025", end="2025")
    assert steel["units"] is None and steel["eur_per_unit"] is None and "many different units" in steel["units_note"]
    assert steel["weight"] > 0 and steel["eur_per_tonne"] == pytest.approx(steel["value"] / steel["weight"])


def test_rankings_read_a_measure_and_say_its_unit(backend):
    client, _ = backend
    lines = get(client, "ranking/records", by="product", period="2025", flow="EXPORT", level=4, parent="870380",
                measure="all")
    by = {r["code"]: r for r in lines}
    assert by["87038010"]["units"] == 391 and by["87038010"]["unit"] == "items"   # new electric cars, 2025
    assert by["87038010"]["eur_per_unit"] == pytest.approx(by["87038010"]["value"] / 391)
    tonnes = get(client, "ranking", by="product", period="2025", flow="EXPORT", measure="weight", max_items=3)
    assert tonnes["unit"] == "t" and tonnes["measure"] == "weight" and len(tonnes["items"]) == 3
    assert all(i["value_eur"] for i in tonnes["items"])
    r = client.get("/insights/ranking", params={"by": "product", "period": "2025", "flow": "EXPORT", "measure": "units"})
    assert r.status_code == 400
    assert client.get("/insights/ranking", params={"by": "partner", "period": "2025", "measure": "pieces"}).status_code == 400


def test_every_default_is_unchanged(backend):
    client, _ = backend
    rows = get(client, "series", flow="EXPORT", start="2024", end="2025")
    assert [set(r) for r in rows] == [{"period", "value"}] * 2
    plain = get(client, "ranking", by="partner", period="2025", flow="EXPORT", max_items=3)
    assert "measure" not in plain and "unit" not in plain and set(plain["items"][0]) == \
        {"rank", "code", "name", "value", "share", "cum_share"}
    assert set(get(client, "ranking/records", by="partner", period="2025", flow="EXPORT")[0]) == \
        {"name", "code", "value", "share"}
    movers = get(client, "movers", by="partner", period="2025", previous="2024", flow="EXPORT")
    assert "measure" not in movers and "unit" not in movers
    euros = get(client, "series", flow="EXPORT", start="2025", end="2025", measure="value")
    assert euros == get(client, "series", flow="EXPORT", start="2025", end="2025")


def test_a_missing_quantity_is_said_never_zero(backend):
    _, insights = backend
    row = {"value": 1000.0, "weight": None, "units": None}
    insights._unit_values(row)
    assert row["eur_per_tonne"] is None and row["eur_per_unit"] is None
    assert row["quantity_note"] == "no quantity published for this line"


def test_the_validator_and_the_catalogue_know_the_measure(backend):
    _, insights = backend
    from view import validate
    for endpoint in ("/insights/series", "/insights/ranking", "/insights/ranking/records", "/insights/movers",
                     "/insights/compare"):
        assert "measure" in validate.ENDPOINT_PARAMS[endpoint][1]
        assert "measure" in insights.METHODS[endpoint]["inputs"]
        names = {v["name"] for v in insights.METHODS[endpoint].get("variants", []) if "measure" in v["when"]}
        assert {"Weight in tonnes", "Count in the line's own unit"} <= names
    assert validate.PARAM_VALUES[("/insights/series", "measure")] == {"value", "weight", "units", "all"}
    assert "not prices" in insights.METHODS["/insights/series"]["variants"][-1]["detail"]


def test_a_quantity_fact_is_written_as_a_count():
    sys.path.insert(0, str(REPO))
    try:
        from agents.kit.view import fact_api
    finally:
        sys.path.remove(str(REPO))
    assert fact_api("/insights/series", {"product": "8703", "measure": "units"}, "-1.value", "eur")["format"] == "qty"
    assert fact_api("/insights/series", {"product": "72", "measure": "weight"}, "0.value", "eur")["format"] == "qty"
    assert fact_api("/insights/series", {"product": "8703", "measure": "units"}, "0.value_eur", "eur")["format"] == "eur"
    assert fact_api("/insights/series", {"product": "8703"}, "-1.value", "eur")["format"] == "eur"
