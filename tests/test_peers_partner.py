# SPDX-License-Identifier: MIT
"""Other EU countries' own trade with one partner (Belgium's exports to the United States): pre-loaded for the
EU-27 x US, CN, GB, CH, fetched live on first use for any other reporter and partner, compared by reporter.

Runs app/backend in process on the local database data/lux_trade.db (skipped when it is not there, or when it
was loaded before these slices); live fetches are switched off unless a test fakes the source.
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
            rows = c.get("/insights/series", params={"reporter": "BE", "partner": "US", "flow": "EXPORT",
                                                     "start": "2015", "end": "2025"}).json()
            if len(rows) < 11:
                pytest.skip("data/lux_trade.db was loaded before the EU-27 x partner slices")
            yield c, insights
    finally:
        sys.path.remove(str(BACKEND))
        _drop_backend_modules()
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved


def get(client, endpoint, **params):
    r = client.get(f"/insights/{endpoint}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_compare_by_reporter_gives_each_countrys_own_trade_with_the_partner(backend):
    client, _ = backend
    rows = get(client, "compare", by="reporter", period="2024", group="LU,BE,NL,DE", partner="US", flow="EXPORT")
    by = {r["code"]: r["value"] for r in rows}
    assert set(by) == {"LU", "BE", "NL", "DE"}
    # Eurostat ds-045409, exports to the United States in 2024 (checked on 15 October 2026)
    assert by["BE"] == pytest.approx(34_293_796_915) and by["NL"] == pytest.approx(43_294_709_219)
    assert by["DE"] == pytest.approx(161_242_829_806) and by["LU"] == pytest.approx(429_629_760)
    # never Luxembourg's exports TO those countries
    to_them = get(client, "compare", by="partner", period="2024", group="BE,NL,DE", flow="EXPORT")
    assert {r["code"]: r["value"] for r in to_them}["BE"] != by["BE"]


def test_series_takes_a_reporter_and_a_partner_annual_and_monthly(backend):
    client, _ = backend
    annual = get(client, "series", reporter="BE", partner="US", flow="EXPORT", start="2015", end="2025")
    assert [r["period"] for r in annual] == [str(y) for y in range(2015, 2026)]
    assert annual[-2]["value"] == pytest.approx(34_293_796_915)
    monthly = get(client, "series", reporter="FR", partner="CN", product="30", flow="EXPORT", freq="M",
                  start="2025-06", end="2025-06")
    assert monthly == [{"period": "2025-06", "value": pytest.approx(152_643_587)}]   # Eurostat: 152,643,587
    # the monthly slice of the seven named peers goes back to 2019
    months = get(client, "series", reporter="NL", partner="GB", flow="IMPORT", freq="M", start="2019-01", end="2019-12")
    assert len(months) == 12


def test_a_reporter_other_than_luxembourg_is_a_slice_the_api_fetches(backend, monkeypatch):
    client, insights = backend
    monkeypatch.setattr(insights, "SDMX", insights.CONFIG["sdmx"])   # for the key only: nothing is fetched
    from database import SessionLocal
    db = SessionLocal()
    try:
        filters = {"reporter": ["ES"], "partner": ["CN"], "product": ["0901"], "flow": ["EXPORT"], "freq": ["A"],
                   "indicators": ["VALUE_IN_EUROS"]}
        needed, _ = insights._needed(db, filters)
        assert needed["reporter"] == ("all_of", {"ES"}) and needed["partner"] == ("all_of", {"CN"})
        assert insights._sdmx_key(needed, {}) == "A.ES.CN.0901.2.VALUE_IN_EUROS"
        # Spain x China x HS2 is pre-loaded for 2015-2025, coffee (HS4) is not: fetched on the first ask
        hs2 = dict(filters, product=["09"])
        assert insights._covered(db, insights._needed(db, hs2)[0], "2015", "2025") is True
        assert insights._covered(db, needed, "1999", "2025") is False
    finally:
        db.close()


def test_a_country_that_cannot_be_read_is_said_so_never_zero(backend, monkeypatch):
    client, insights = backend
    import requests

    def offline(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(insights, "SDMX", insights.CONFIG["sdmx"])
    monkeypatch.setattr(requests, "get", offline)
    insights._FAILED.clear()
    rows = get(client, "compare", by="reporter", period="2024", group="LU,MT", partner="JP", product="0901",
               flow="EXPORT")
    mt = next(r for r in rows if r["code"] == "MT")
    assert mt["value"] is None and mt["loaded"] is False
    insights._FAILED.clear()
