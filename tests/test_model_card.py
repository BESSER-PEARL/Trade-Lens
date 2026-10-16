# SPDX-License-Identifier: MIT
"""The model card of /insights/forecast (``model_card``, app/backend/forecasting/card.py): what the page's
"Model card" button shows next to a forecast chart.

It must follow the code (the ensemble's members, the gradient boosting features), keep the offline study
(66 quarter ends) apart from this series' own backtest (the last 20), agree with docs/forecasting.md, and
be a new key only: the rest of the response is unchanged.
"""
import os
import re
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
def client():
    if not DB.exists():
        pytest.skip("no local database data/lux_trade.db (load the data first)")
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
    _drop_backend_modules()
    sys.path.insert(0, str(BACKEND))
    from forecasting import pool
    saved_dir = pool.CACHE_DIR
    pool.CACHE_DIR = REPO / ".pytest_cache" / "forecast"   # the tests' own cache, not the running app's
    try:
        import main_api
        from routers import insights
        insights.SDMX = None  # never fetch live in tests
        with TestClient(main_api.app) as c:
            yield c
    finally:
        pool.CACHE_DIR = saved_dir
        sys.path.remove(str(BACKEND))
        _drop_backend_modules()
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved


def _get(client, **params):
    r = client.get("/insights/forecast", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_card_follows_the_code_and_the_response(client):
    from forecasting import models
    f = _get(client, flow="EXPORT", freq="M", horizon=12, interval=0.8, aggregate="A")
    card, bt = f["model_card"], f["backtest"]
    # the members and the features are the code's
    assert [m["method"] for m in card["members"]] == list(models.ENSEMBLE)
    assert card["combination"] and len(card["features"]) == len(models.GBM_FEATURES)
    assert any("working days" in x for x in card["features"])
    tr = card["training"]
    assert tr["last"] == f["last_observed"] and tr["months"] == f["n_obs"]
    assert tr["pooled"] and tr["pool_series"] >= 100 and tr["pool_examples"] > 100_000
    assert tr["cutoff"].endswith("-12") and tr["cutoff"] <= tr["last"]
    assert tr["gbm_params"]["trees"] == models.GBM_PARAMS["max_iter"]
    # this series' own backtest: the response's numbers, with how many origins each horizon was scored on
    live = card["live"]
    assert live["origins"] == bt["origins"] == 20 and live["first_origin"] == bt["first_origin"]
    for row in live["horizons"]:
        h = row["h"]
        assert row["mase"] == bt[f"mase_h{h}"] and row["skill"] == bt[f"skill_h{h}"]
        assert row["n"] == bt[f"n_h{h}"]
    ns = [r["n"] for r in live["horizons"]]
    assert ns[0] == 20 and ns == sorted(ns, reverse=True) and ns[-1] < 20   # 12 months ahead is not seen yet for the last origins
    assert {m["method"] for m in live["methods"]} >= {"snaive", "ets", "gbm", "ensemble"}
    assert live["best"] in {m["method"] for m in live["methods"]}
    # the band
    assert card["band"]["level"] == 0.8 and card["band"]["method"] == "split conformal"
    assert card["band"]["coverage"] == bt["coverage"] and card["band"]["checks"] == bt["coverage_n"]
    # the limits name the smartphone line (the export total carries its scenarios) and nominal euros
    text = " ".join(card["limits"])
    assert "smartphone" in text and "nominal euros" in text and f["last_observed"] in text


def test_the_study_is_the_documented_backtest(client):
    card = _get(client, flow="IMPORT", freq="M", horizon=3)["model_card"]
    st = card["study"]
    assert (st["origins"], st["first_origin"], st["last_origin"], st["horizon"]) == (66, "2009-12", "2026-03", 12)
    rows = {r["key"]: r for r in st["methods"]}
    assert rows["ensemble"]["mase"] < rows["gbm"]["mase"] < rows["snaive"]["mase"]
    assert rows["nn_mlp"]["mase_h12"] > rows["snaive_nn"]["mase_h12"]           # the network loses at 12 months
    skill = {r["h"]: r["skill"] for r in st["skill"]}
    assert round(skill[1], 2) == 0.35 and round(skill[12], 2) == 0.04
    # the same figures as docs/forecasting.md's table
    doc = (REPO / "docs" / "forecasting.md").read_text(encoding="utf-8")
    line = next(x for x in doc.splitlines() if x.startswith("| **ensemble"))
    figures = [float(v) for v in re.findall(r"\*\*(\d\.\d\d)\*\*", line)]
    e = rows["ensemble"]
    assert figures[:5] == [e["mase_h1"], e["mase_h3"], e["mase_h6"], e["mase_h12"], e["mase"]]


def test_the_card_is_a_new_key_only(client):
    f = _get(client, flow="EXPORT", freq="M", horizon=6, backtest="false")
    assert f["backtest"] is None                        # backtest=false still leaves the table out
    assert f["model_card"]["live"]["origins"] == 20     # the card keeps its own numbers
    for key in ("method", "method_label", "history", "forecast", "rows", "reference", "scenarios", "caveat"):
        assert key in f
    one = _get(client, flow="EXPORT", freq="M", horizon=2, method="ets")["model_card"]
    assert [m["method"] for m in one["members"]] == ["ets"] and one["features"] == [] and one["combination"] is None
