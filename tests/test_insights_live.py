# SPDX-License-Identifier: MIT
"""The generated analytics API fetches slices that were never loaded, and never shows them as zero.

Offline: a BESSER backend is generated for the synthetic fixture model, filled by the loader from a
fake client (which also writes the loaded-slice registry), and the SDMX source is a fake
``requests.get``.
"""
import importlib
import sys

import pandas as pd
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from besser_sdmx.generators import InsightsGenerator  # noqa: E402
from besser_sdmx.importer import sdmx_to_buml  # noqa: E402
from besser_sdmx.loader import Loader, Slice, import_module  # noqa: E402
from besser_sdmx.patches import patch_generated_backend  # noqa: E402
from besser_sdmx.importer.structure import parse_structure  # noqa: E402

COLS = ["DATAFLOW", "freq", "reporter", "partner", "product", "flow", "rank", "TIME_PERIOD", "OBS_VALUE", "OBS_FLAG"]


def _row(partner, product, flow, year, value):
    return {"DATAFLOW": "TEST:DF_TEST", "freq": "A", "reporter": "LU", "partner": partner, "product": product,
            "flow": flow, "rank": "1", "TIME_PERIOD": year, "OBS_VALUE": str(value), "OBS_FLAG": ""}


class FakeClient:
    def __init__(self, frame):
        self.frame = frame

    def data(self, flow, key, start=None, end=None):
        return self.frame


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def api(structure_xml, tmp_path, monkeypatch):
    from besser.generators.backend import BackendGenerator

    dsd = parse_structure(structure_xml, "DF_TEST")
    model, mapping = sdmx_to_buml(dsd, model_name="TestTrade", class_names={"CL_AREA": "Country"},
                                  source="eurostat-comext")
    backend = tmp_path / "backend"
    BackendGenerator(model=model, output_dir=str(backend)).generate()
    patch_generated_backend(backend)
    InsightsGenerator(model, mapping, output_dir=str(backend), focus={"reporter": "LU"}).generate()

    # Loaded: Luxembourg x every partner x chapters (TOTAL, 84, 85), 2024-2025. Not loaded: anything below.
    rows = [_row(p, prod, f, y, v) for p in ("WORLD", "DE", "FR") for prod, v in (("TOTAL", 300), ("84", 200), ("85", 100))
            for f in ("1", "2") for y in ("2024", "2025")]
    db = tmp_path / "data.db"
    orm = import_module(backend / "sql_alchemy.py", name="test_live_orm")
    loader = Loader(orm, f"sqlite:///{db.as_posix()}", dsd, mapping)
    loader.reset()
    loader.load_codelists()
    loader.load_slices(FakeClient(pd.DataFrame(rows, columns=COLS)),
                       [Slice({"freq": "A", "reporter": "LU", "partner": "", "product": ["TOTAL", "84", "85"],
                               "flow": ["1", "2"]}, "2024", "2025", "overview")])

    calls = []

    def fake_get(url, params=None, timeout=None, headers=None):
        calls.append(url)
        if fake_get.offline:
            raise ConnectionError("offline")
        key = url.rsplit("/", 1)[1]
        partner, products = key.split(".")[2], key.split(".")[3].split("+")
        lines = [",".join(COLS)]
        for prod in products:
            if prod.startswith("84") and len(prod) == 4:  # the HS4 headings of chapter 84
                lines.append(",".join(_row(partner, prod, "2", "2025", 50).values()))
        return FakeResponse(200, "\n".join(lines) + "\n")

    fake_get.offline = False
    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.syspath_prepend(str(backend))
    for name in ("database", "sql_alchemy", "routers", "routers.insights", "bal_stdlib"):
        sys.modules.pop(name, None)
    router = importlib.import_module("routers.insights")
    app = FastAPI()
    app.include_router(router.router)
    yield TestClient(app), calls, fake_get, db
    for name in ("database", "sql_alchemy", "routers", "routers.insights", "bal_stdlib", "test_live_orm"):
        sys.modules.pop(name, None)


def test_loader_registers_slices(api):
    import sqlite3

    _, _, _, db = api
    rows = sqlite3.connect(db).execute("SELECT spec, start_period, end_period, source FROM _loaded_slice").fetchall()
    assert len(rows) == 1 and rows[0][1:] == ("2024", "2025", "load")
    assert '"partner": "*"' in rows[0][0] and '"84"' in rows[0][0]


def test_loaded_slice_is_not_fetched(api):
    client, calls, _, _ = api
    r = client.get("/insights/ranking", params={"by": "product", "period": "2025", "flow": "EXPORT", "level": 1}).json()
    assert r["fetched_live"] is False and r["loaded"] is True
    assert {i["code"] for i in r["items"]} == {"84", "85"}
    assert calls == []


def test_missing_slice_is_fetched_live_once(api):
    client, calls, _, _ = api
    params = {"by": "product", "period": "2025", "flow": "EXPORT", "partner": "DE", "parent": "84"}
    r = client.get("/insights/ranking", params=params).json()
    assert r["fetched_live"] is True and r["loaded"] is True
    assert {i["code"] for i in r["items"]} == {"8401", "8402", "8403", "8404"}
    assert r["total"] == 200  # the chapter's own value, not the whole export total
    assert len(calls) == 1 and ".DE." in calls[0]
    again = client.get("/insights/ranking", params=params).json()
    assert again["fetched_live"] is False and len(again["items"]) == 4 and len(calls) == 1


def test_offline_is_not_zero(api):
    client, _, fake_get, _ = api
    fake_get.offline = True
    r = client.get("/insights/ranking", params={"by": "product", "period": "2025", "flow": "EXPORT",
                                                 "partner": "FR", "parent": "84"}).json()
    assert r["loaded"] is False and r["items"] == [] and r["other"] is None
    m = client.get("/insights/movers", params={"by": "product", "period": "2025", "previous": "2024",
                                               "flow": "EXPORT", "partner": "FR", "parent": "85"}).json()
    assert m["loaded"] is False and m["up"] == []


def test_unknown_codes_are_400(api):
    client, _, _, _ = api
    assert client.get("/insights/series", params={"flow": "BOGUS"}).status_code == 400
    assert client.get("/insights/ranking", params={"by": "product", "period": "2025", "partner": "QQQ"}).status_code == 400


def test_meta_periods_end_with_the_headline_series(api):
    """A year with some partners but no WORLD total (Eurostat publishes extra-EU trade early) is not offered."""
    import sqlite3

    client, _, _, db = api
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(observation)")]
    row = dict(zip(cols, con.execute("SELECT * FROM observation WHERE partner_id = 'DE' LIMIT 1").fetchone()))
    row.update(id=None, time_period="2026")
    con.execute(f"INSERT INTO observation ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [row[c] for c in cols])
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM observation WHERE time_period = '2026'").fetchone()[0] == 1
    con.close()
    assert client.get("/insights/meta").json()["periods"]["A"][-1] == "2025"


def test_meta_labels_and_constants(api):
    client, _, _, _ = api
    meta = client.get("/insights/meta").json()
    flow = next(d for d in meta["dimensions"] if d["id"] == "flow")
    assert {v["label"] for v in flow["values"]} == {"Import", "Export"}
    freq = next(d for d in meta["dimensions"] if d["id"] == "freq")
    assert freq["constant"] is True  # one frequency in the data: context, not a choice
