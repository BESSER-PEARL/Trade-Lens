# SPDX-License-Identifier: MIT
"""The /view router: the store, ops, pre-flight, SSE, undo/redo/reset, persistence, the agent registry,
and contract 1.1: the page context, set_context, placeholders in the pre-flight, mark periods.

Runs app/backend in process (FastAPI TestClient) on the local database data/lux_trade.db; skipped
when it is not there. Live fetches from Eurostat are switched off. Each test gets its own state file.
"""
import copy
import json
import os
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "app" / "backend"
DB = REPO / "data" / "lux_trade.db"
EXAMPLES = REPO / "contract" / "examples"
OWN = ("main_api", "database", "sql_alchemy", "pydantic_classes", "bal_stdlib", "routers", "view")


def _drop_backend_modules() -> None:
    for name in list(sys.modules):
        if name.split(".")[0] in OWN:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def backend():
    """(main_api module, routers.view module, routers.insights module) of app/backend."""
    if not DB.exists():
        pytest.skip("no local database data/lux_trade.db (load the data first)")
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "VIEW_STATE_PATH")}
    os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
    _drop_backend_modules()
    sys.path.insert(0, str(BACKEND))
    try:
        import main_api
        from routers import insights, view
        insights.SDMX = None  # never fetch live in tests
        yield main_api, view, insights
    finally:
        sys.path.remove(str(BACKEND))
        _drop_backend_modules()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(scope="module")
def http(backend):
    with TestClient(backend[0].app) as c:
        yield c


@pytest.fixture
def client(backend, http, tmp_path):
    """A client on a fresh store (its own state file)."""
    backend[1].configure(tmp_path / "view_state.json")
    return http


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def baseline() -> dict:
    return json.loads((BACKEND / "view" / "baseline.json").read_text(encoding="utf-8"))


def event_batch(run_id: str, base_version=None, at="2020-03", label="COVID-19", agent="cause_finder") -> dict:
    env = {"agent": {"name": agent, "run_id": run_id},
           "ops": [{"op": "add_mark", "tmp_id": "ev",
                    "mark": {"kind": "event", "target": "trend", "params": {"at": at, "label": label},
                             "note": "Lockdowns across Europe from March 2020."}}]}
    if base_version is not None:
        env["base_version"] = base_version
    return env


def post(client, envelope):
    return client.post("/view/ops", json=envelope)


def strip_times(x):
    if isinstance(x, dict):
        return {k: strip_times(v) for k, v in x.items() if k not in ("created_at", "updated_at")}
    if isinstance(x, list):
        return [strip_times(v) for v in x]
    return x


# --------------------------------------------------------------------------- reading
def test_get_view_is_the_baseline(client):
    spec = client.get("/view").json()
    base = baseline()
    assert spec["version"] == 0
    assert [p["id"] for p in spec["panels"]] == [p["id"] for p in base["panels"]]
    assert spec["layout"] == base["layout"] and spec["marks"] == []
    assert client.get("/view", params={"since": 0}).status_code == 204


def test_ops_make_version_1_with_numbers(client):
    r = post(client, example("01_smartphone_thread"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["warnings"] == []
    assert body["assigned"] == {"phones_thread": {"id": "m1", "n": 1}, "phones_path": {"id": "m2", "n": 2}}
    spec = body["spec"]
    assert spec["next_mark_n"] == 3 and spec["updated_by"] == "trend_finder"
    m1 = next(m for m in spec["marks"] if m["id"] == "m1")
    assert m1["status"] == "proposed" and m1["agent"] == "trend_finder"
    urls = [s["url"] for s in m1["provenance"]["sources"]]
    assert "/insights/ranking?by=partner&period=2025&flow=EXPORT&product=85171300" in urls  # pre-flighted fact
    assert client.get("/view").json()["version"] == 1


def test_replay_01_to_03_gives_the_contract_example(client):
    for name in ("01_smartphone_thread", "02_events", "03_forecast"):
        r = post(client, example(name))
        assert r.status_code == 200, (name, r.text)
    expected = json.loads((EXAMPLES / "spec_after_03.json").read_text(encoding="utf-8"))
    spec = client.get("/view").json()
    # the store gives every spec a context (contract 1.1); the 1.0 example has none
    assert spec.pop("context") == {"period": "2025", "product": "TOTAL"}
    assert strip_times(spec) == strip_times(expected)


def test_retry_with_the_same_run_id_changes_nothing(client):
    first = post(client, event_batch("cf-1")).json()
    again = post(client, event_batch("cf-1"))
    assert again.status_code == 200 and again.json()["version"] == first["version"] == 1
    assert again.json()["assigned"] == first["assigned"] and "retry" in again.json()["warnings"][0]
    assert client.get("/view").json()["version"] == 1
    other = event_batch("cf-1", at="2021-01", label="Other")
    assert post(client, other).status_code == 409  # same run_id, different ops


# --------------------------------------------------------------------------- refusals
def test_preflight_400_is_a_422_that_echoes_it(client):
    env = event_batch("cf-x")
    env["ops"][0]["mark"]["facts"] = {"v": {"endpoint": "/insights/series",
                                            "params": {"flow": "EXPORT", "partner": "XX"}, "path": "0.value"}}
    env["ops"][0]["mark"]["note"] = "Exports to nowhere: {v}."
    r = post(client, env)
    assert r.status_code == 422, r.text
    err = r.json()["errors"][0]
    assert err["code"] == "preflight" and err["status"] == 400
    assert err["path"] == "/ops/0/mark/facts/v"
    assert "XX" in err["message"] and "Unknown code" in err["message"]
    assert client.get("/view").json()["version"] == 0  # nothing changed


def test_invalid_batch_is_a_422_with_paths(client):
    env = event_batch("cf-y")
    env["ops"][0]["mark"]["target"] = "nowhere"
    r = post(client, env)
    assert r.status_code == 422
    assert {"path": "/ops/0/mark/target", "code": "unknown_target"}.items() <= r.json()["errors"][0].items()


def test_stale_base_version_is_a_409_with_the_spec(client):
    assert post(client, event_batch("cf-1", base_version=0)).status_code == 200
    r = post(client, event_batch("cf-2", base_version=0, at="2021-01", label="EU-UK"))
    assert r.status_code == 409
    assert r.json()["version"] == 1 and r.json()["spec"]["version"] == 1


# --------------------------------------------------------------------------- SSE
def test_sse_delivers_the_new_version(client):
    out = {}

    def listen():
        out["r"] = client.get("/view/events", params={"since": 0, "max_events": 1, "heartbeat": 0.2})

    t = threading.Thread(target=listen)
    t.start()
    assert post(client, event_batch("cf-sse")).status_code == 200
    t.join(timeout=30)
    assert not t.is_alive()
    r = out["r"]
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [b for b in r.text.split("\n\n") if b.startswith("id:")]
    assert len(events) == 1
    lines = dict(line.split(": ", 1) for line in events[0].splitlines())
    assert lines["event"] == "spec" and lines["id"] == "1"
    assert json.loads(lines["data"])["version"] == 1


def test_sse_sends_the_current_spec_on_connect(client):
    r = client.get("/view/events", params={"max_events": 1})
    data = next(line for line in r.text.splitlines() if line.startswith("data: "))
    assert json.loads(data[6:])["version"] == 0


# --------------------------------------------------------------------------- keep, clear, undo, reset
def test_keep_then_clear_keeps_the_mark(client):
    post(client, event_batch("cf-1"))
    post(client, event_batch("cf-2", at="2021-01", label="EU-UK trade agreement"))
    assert post(client, {"agent": {"name": "user"}, "ops": [{"op": "keep_mark", "id": "m1"}]}).status_code == 200
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "clear", "scope": "all"}]})
    assert r.status_code == 200, r.text
    marks = r.json()["spec"]["marks"]
    assert [(m["id"], m["status"]) for m in marks] == [("m1", "kept")]


def test_clear_puts_a_rewritten_baseline_panel_back(client):
    post(client, example("01_smartphone_thread"))
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "clear", "scope": "all"}]})
    spec = r.json()["spec"]
    headline = next(p for p in spec["panels"] if p["id"] == "headline")
    assert headline == next(p for p in baseline()["panels"] if p["id"] == "headline")
    assert "phones_drill" not in [p["id"] for p in spec["panels"]]
    assert spec["layout"] == baseline()["layout"]


def test_clear_spares_the_pages_own_edits(client):
    # The year control upserts baseline panels as agent "user"; clearing the agents' work keeps them.
    kpi = copy.deepcopy(next(p for p in baseline()["panels"] if p["id"] == "kpi_exports"))
    kpi["title"] = "Sold in 2024"
    for q in kpi.get("queries", []):
        if q["params"].get("period") == "2025":
            q["params"]["period"] = "2024"
    assert post(client, {"agent": {"name": "user"}, "ops": [{"op": "upsert_panel", "panel": kpi}]}).status_code == 200
    r = post(client, {"agent": {"name": "trend_finder"}, "ops": [{"op": "clear", "scope": "all"}]})
    assert r.status_code == 200, r.text
    after = next(p for p in r.json()["spec"]["panels"] if p["id"] == "kpi_exports")
    assert after["title"] == "Sold in 2024" and after["agent"] == "user"


def test_the_user_removes_a_page_panel_with_its_marks(client):
    # "I want to be able to delete all of the charts": the user may remove the page's own panels too.
    post(client, event_batch("cf-1"))
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "remove_panel", "id": "trend", "force": True}]})
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert "trend" not in [p["id"] for p in spec["panels"]]
    assert all("trend" not in row["panels"] for row in spec["layout"]["rows"])
    assert spec["marks"] == []                      # the event on the trend went with it
    assert [p["id"] for p in client.post("/view/undo").json()["spec"]["panels"]].count("trend") == 1


def test_a_pinned_page_panel_cannot_be_removed(client):
    post(client, {"agent": {"name": "user"}, "ops": [{"op": "pin_panel", "id": "crossroads", "pinned": True}]})
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "remove_panel", "id": "crossroads", "force": True}]})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "pinned"


def test_clear_the_page_leaves_it_empty_and_reset_brings_it_back(client):
    post(client, example("01_smartphone_thread"))
    post(client, event_batch("cf-1"))
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "clear", "scope": "page"}]})
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert spec["panels"] == [] and spec["marks"] == [] and spec["layout"]["rows"] == []
    # undo gives the page back as it was; reset gives the starting page
    undone = client.post("/view/undo").json()["spec"]
    assert "crossroads" in [p["id"] for p in undone["panels"]] and undone["marks"]
    post(client, {"agent": {"name": "user"}, "ops": [{"op": "clear", "scope": "page"}]})
    reset = client.post("/view/reset").json()["spec"]
    assert [p["id"] for p in reset["panels"]] == [p["id"] for p in baseline()["panels"]]


def test_clear_the_page_keeps_what_the_user_kept(client):
    post(client, event_batch("cf-1"))
    post(client, {"agent": {"name": "user"}, "ops": [{"op": "keep_mark", "id": "m1"},
                                                     {"op": "pin_panel", "id": "peers", "pinned": True}]})
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "clear", "scope": "page"}]})
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert sorted(p["id"] for p in spec["panels"]) == ["peers", "trend"]   # pinned; carries a kept mark
    assert [m["id"] for m in spec["marks"]] == ["m1"]


def test_only_the_user_clears_the_whole_page(client):
    r = post(client, {"agent": {"name": "trend_finder"}, "ops": [{"op": "clear", "scope": "page"}]})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "user_only"


def test_an_agent_draws_on_an_empty_page(client):
    post(client, {"agent": {"name": "user"}, "ops": [{"op": "clear", "scope": "page"}]})
    batch = example("06b_waterfall")
    r = post(client, batch)
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert spec["panels"] and [row["panels"] for row in spec["layout"]["rows"]] == [[p["id"] for p in spec["panels"]]]


def test_remove_then_undo_restores_and_redo_removes_again(client):
    post(client, event_batch("cf-1"))
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "remove_mark", "id": "m1"}]})
    assert r.json()["spec"]["marks"] == []
    undone = client.post("/view/undo")
    assert undone.status_code == 200
    assert undone.json()["version"] == 3 and [m["id"] for m in undone.json()["spec"]["marks"]] == ["m1"]
    redone = client.post("/view/redo").json()
    assert redone["version"] == 4 and redone["spec"]["marks"] == []
    hist = client.get("/view/history").json()
    assert [e["action"] for e in hist["entries"][:4]] == ["redo", "undo", "ops", "ops"]
    # what each batch did, for the "What changed" drawer: the op kinds, a mark's kind with it
    assert hist["entries"][2]["op_kinds"] == ["remove_mark"] and hist["entries"][3]["op_kinds"] == ["add_mark event"]
    assert "op_kinds" not in hist["entries"][0]          # undo and redo restore a version, they send no ops
    assert client.get("/view/history/1").json()["marks"][0]["id"] == "m1"


def test_numbers_are_never_reused_after_undo(client):
    post(client, event_batch("cf-1"))
    client.post("/view/undo")
    body = post(client, event_batch("cf-2", at="2021-01", label="EU-UK")).json()
    assert body["assigned"]["ev"] == {"id": "m2", "n": 2}
    assert client.post("/view/redo").status_code == 409  # a new batch clears the redo stack


def test_reset_gives_the_baseline(client):
    post(client, example("01_smartphone_thread"))
    r = client.post("/view/reset")
    assert r.status_code == 200
    spec, base = r.json()["spec"], baseline()
    assert spec["version"] == 2 and spec["next_mark_n"] == 3
    for key in ("title", "panels", "layout", "marks"):
        assert spec[key] == base[key]
    assert [m["id"] for m in client.post("/view/undo").json()["spec"]["marks"]] == ["m1", "m2"]


def test_state_survives_a_store_reload(backend, client, tmp_path):
    post(client, event_batch("cf-1"))
    post(client, {"agent": {"name": "user"}, "ops": [{"op": "keep_mark", "id": "m1"}]})
    before = client.get("/view").json()
    path = tmp_path / "view_state.json"
    assert path.exists()
    backend[1].configure(path)  # a new store reading the same file, as after a restart
    assert client.get("/view").json() == before
    assert post(client, event_batch("cf-1")).json()["version"] == 1  # run_ids survive too
    assert client.post("/view/undo").json()["spec"]["marks"][0]["status"] == "proposed"


def test_store_reload_directly(backend, tmp_path):
    from view.store import ViewStore
    path = tmp_path / "s.json"
    store = ViewStore(path)
    out = store.apply({"agent": {"name": "user"}, "ops": [{"op": "set_title", "title": "A new title"}]})
    assert out["version"] == 1
    again = ViewStore(path)
    assert again.current() == store.current() and again.current()["title"] == "A new title"
    assert copy.deepcopy(again.history_entries()["entries"][0])["action"] == "ops"


# --------------------------------------------------------------------------- agents
def test_register_then_list_agents(client):
    reg = {"name": "trend_finder", "title": "Trend finder", "color": "#E2142D",
           "capabilities": [{"kind": "find_movers", "produces": ["thread", "highlight", "bar"],
                             "description": "Finds what changed and drills down to the product line",
                             "inputs": ["period", "flow"]}],
           "examples": ["Why did exports jump in 2025?"]}
    r = client.post("/agents/register", json=reg)
    assert r.status_code == 200 and r.json()["online"] is True and r.json()["ttl"] == 60
    agents = client.get("/agents").json()
    mine = next(a for a in agents if a["name"] == "trend_finder")
    assert mine["color"] == "#E2142D" and mine["capabilities"][0]["kind"] == "find_movers"
    assert client.post("/agents/register", json={**reg, "name": "baseline"}).status_code == 422
    assert client.post("/agents/register", json={**reg, "color": "red"}).status_code == 422


# --------------------------------------------------------------------------- contract 1.1: context
def without_base(envelope: dict) -> dict:
    return {k: v for k, v in envelope.items() if k != "base_version"}


def set_context(run_id: str, agent: str = "user", **fields) -> dict:
    return {"agent": {"name": agent, "run_id": run_id}, "ops": [{"op": "set_context", **fields}]}


def bump_panel(start="$period", end="2024") -> dict:
    """A bump chart whose matrix query is only valid when $period is not after 2024."""
    return {"id": "ranks", "type": "bump", "title": "Who buys most, year by year",
            "queries": [{"key": "main", "endpoint": "/insights/matrix", "tone": "exports",
                         "params": {"by": "partner", "start": start, "end": end, "flow": "EXPORT",
                                    "measure": "rank", "max_items": 5}}]}


def test_the_spec_has_a_context_by_default(client, backend):
    spec = client.get("/view").json()
    complete = client.get("/insights/meta").json()["complete_until"]
    assert spec["context"] == {"period": complete[:4], "product": "TOTAL"}
    assert backend[1].default_context() == spec["context"]


def test_sse_carries_the_context(client):
    r = client.get("/view/events", params={"max_events": 1})
    data = next(line for line in r.text.splitlines() if line.startswith("data: "))
    assert json.loads(data[6:])["context"]["product"] == "TOTAL"


def test_set_context_merges_and_undo_restores_it(client):
    r = post(client, without_base(example("08_context_2020")))
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["context"] == {"period": "2020", "product": "TOTAL"}
    r = post(client, set_context("user-ctx-2", product="85", flow="EXPORT"))
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["context"] == {"period": "2020", "product": "85", "flow": "EXPORT"}
    assert client.get("/view").json()["context"]["product"] == "85"
    undone = client.post("/view/undo").json()["spec"]
    assert undone["context"] == {"period": "2020", "product": "TOTAL"}
    hist = client.get("/view/history").json()["entries"]
    assert hist[1]["agent"] == "user" and hist[1]["action"] == "ops"


def test_set_context_with_a_persona_applies_and_the_history_says_it(client):
    """Contract 1.4: the persona is one more context field; the rest of the context stays, and the
    audit trail (/view/history) says which reader each version was drawn for."""
    r = post(client, set_context("user-persona-1", persona="journalist", period="2025"))
    assert r.status_code == 200, r.text
    ctx = r.json()["spec"]["context"]
    assert ctx["persona"] == "journalist" and ctx["product"] == "TOTAL"
    r = post(client, set_context("user-persona-2", period="2020"))
    assert r.json()["spec"]["context"] == {**ctx, "period": "2020"}  # no persona in the op: it stays
    entries = client.get("/view/history").json()["entries"]
    assert [e.get("persona") for e in entries] == ["journalist", "journalist", None]
    undone = client.post("/view/undo").json()["spec"]
    assert undone["context"]["persona"] == "journalist" and undone["context"]["period"] == "2025"
    r = post(client, set_context("user-persona-3", persona="Not a persona"))
    assert r.status_code == 422 and r.json()["errors"][0]["path"] == "/ops/0/persona"


def test_a_spec_without_persona_behaves_as_before(client):
    r = post(client, set_context("user-nopersona-1", product="85"))
    assert r.status_code == 200, r.text
    assert "persona" not in r.json()["spec"]["context"]
    assert all("persona" not in e for e in client.get("/view/history").json()["entries"])


def test_an_agent_can_move_the_page_to_a_month(client):
    r = post(client, set_context("viz-ctx-1", agent="visualizer", period="2020-03"))
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["context"]["period"] == "2020-03"


def test_set_context_without_data_is_refused(client):
    r = post(client, set_context("user-ctx-x", period="2031"))
    assert r.status_code == 422
    err = r.json()["errors"][0]
    assert err["code"] == "preflight" and err["path"] == "/ops/0/period" and "2031" in err["message"]
    r = post(client, set_context("user-ctx-y", period="2030-01"))
    assert r.status_code == 422 and r.json()["errors"][0]["path"] == "/ops/0/period"
    r = post(client, set_context("user-ctx-z", product="XYZ"))
    assert r.status_code == 422
    assert r.json()["errors"][0]["path"] == "/ops/0/product" and "XYZ" in r.json()["errors"][0]["message"]
    r = post(client, {"agent": {"name": "user"}, "ops": [{"op": "set_context"}]})
    assert r.status_code == 422  # at least one field (the schema)
    assert client.get("/view").json()["version"] == 0


def test_placeholders_are_preflighted_with_the_context(client):
    # With the default context (the latest year, after 2024) start=$period > end=2024: a 400.
    r = post(client, {"agent": {"name": "trend_finder", "run_id": "tf-ph-1"},
                      "ops": [{"op": "upsert_panel", "panel": bump_panel()}]})
    assert r.status_code == 422, r.text
    err = r.json()["errors"][0]
    assert err["code"] == "preflight" and err["status"] == 400
    assert err["path"] == "/ops/0/panel/queries/0" and "start=2025" in err["url"]
    # In a batch that first moves the page to 2020, the same panel is checked with 2020: accepted.
    r = post(client, {"agent": {"name": "trend_finder", "run_id": "tf-ph-2"},
                      "ops": [{"op": "set_context", "period": "2020"}, {"op": "upsert_panel", "panel": bump_panel()}]})
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    panel = next(p for p in spec["panels"] if p["id"] == "ranks")
    assert panel["queries"][0]["params"]["start"] == "$period"  # the spec keeps the placeholder
    # And once the page is at 2020, a panel added later is checked with 2020 too.
    r = post(client, {"agent": {"name": "trend_finder", "run_id": "tf-ph-3"},
                      "ops": [{"op": "upsert_panel", "panel": {**bump_panel(), "id": "ranks2"}}]})
    assert r.status_code == 200, r.text


def test_provenance_records_the_filled_query(client):
    panel = {"id": "export_change", "type": "waterfall", "title": "What changed in exports",
             "text": "Exports went from {before} to {after}.",
             "queries": [{"key": "main", "endpoint": "/insights/movers", "tone": "exports",
                          "params": {"by": "product", "period": "$period", "previous": "$previous", "flow": "EXPORT"}}],
             "facts": {"before": {"query": "main", "path": "previous_total", "format": "eur"},
                       "after": {"query": "main", "path": "total", "format": "eur"}},
             "provenance": {"agent": "cause_finder", "created_at": "2026-10-16T10:00:00Z", "sources": []}}
    r = post(client, {"agent": {"name": "cause_finder", "run_id": "cf-prov"},
                      "ops": [{"op": "set_context", "period": "2021"}, {"op": "upsert_panel", "panel": panel}]})
    assert r.status_code == 200, r.text
    stored = next(p for p in r.json()["spec"]["panels"] if p["id"] == "export_change")
    urls = [s["url"] for s in stored["provenance"]["sources"]]
    assert "/insights/movers?by=product&period=2021&previous=2020&flow=EXPORT" in urls


def test_a_mark_keeps_its_period(client):
    env = event_batch("cf-period")
    env["ops"][0]["mark"]["period"] = "2020-03"
    r = post(client, env)
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["marks"][0]["period"] == "2020-03"
    r = post(client, {"agent": {"name": "cause_finder"},
                      "ops": [{"op": "update_mark", "id": "m1", "set": {"period": "2020"}}]})
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["marks"][0]["period"] == "2020"


def test_the_1_1_examples_are_accepted(backend, client):
    backend[1].get_store().max_agent_panels = 50   # four agent charts in a row: no replacement here
    for name in ("06_maps_and_waterfall", "06b_waterfall", "07_heatmap_and_breaks"):
        r = post(client, without_base(example(name)))
        assert r.status_code == 200, (name, r.text)
    spec = client.get("/view").json()
    types = {p["id"]: p["type"] for p in spec["panels"]}
    assert types["export_map"] == "choropleth" and types["routes"] == "flowmap"
    assert types["export_change"] == "waterfall" and types["export_months"] == "heatmap"
    kinds = [m["kind"] for m in spec["marks"]]
    assert kinds.count("trend_break") == 3 and "thread" in kinds and "annotation" in kinds


def test_new_panel_types_preflight_against_the_new_endpoints(client):
    panels = [
        {"id": "goods", "type": "treemap", "title": "What Luxembourg exports",
         "queries": [{"key": "main", "endpoint": "/insights/tree", "tone": "exports",
                      "params": {"by": "product", "period": "$period", "flow": "EXPORT", "depth": 2,
                                 "threshold": "auto", "product": "$product"}}]},
        {"id": "chapters", "type": "heatmap", "title": "Chapters by year",
         "queries": [{"key": "main", "endpoint": "/insights/matrix", "tone": "exports",
                      "params": {"by": "product", "start": "1999", "end": "$period", "flow": "EXPORT",
                                 "measure": "share", "max_items": 12}}],
         "options": {"kind": "product_year"}},
        {"id": "seasons", "type": "heatmap", "title": "The typical year",
         "queries": [{"key": "main", "endpoint": "/trends/seasonal-heatmap", "tone": "exports",
                      "params": {"flow": "EXPORT", "freq": "M", "top": 8}}],
         "options": {"kind": "seasonal"}},
    ]
    r = post(client, {"agent": {"name": "visualizer", "run_id": "viz-new-types"},
                      "ops": [{"op": "upsert_panel", "panel": p} for p in panels]})
    assert r.status_code == 200, r.text


def test_a_state_file_without_context_gets_one(backend, tmp_path):
    from view.store import ViewStore
    path = tmp_path / "old.json"
    store = ViewStore(path, default_context={"period": "2024", "product": "TOTAL"})
    store.apply({"agent": {"name": "user"}, "ops": [{"op": "set_title", "title": "Before 1.1"}]})
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["spec"]["context"]
    path.write_text(json.dumps(state), encoding="utf-8")
    again = ViewStore(path, default_context={"period": "2024", "product": "TOTAL"})
    assert again.current()["context"] == {"period": "2024", "product": "TOTAL"}
    assert again.current()["title"] == "Before 1.1"


@pytest.mark.parametrize("flow", ["IMPORT", "EXPORT"])
def test_range_totals_and_partner_ranking(client, flow):
    common = {"by": "partner", "flow": flow, "freq": "A", "threshold": 0, "max_items": 400}
    years = [client.get("/insights/ranking", params={**common, "period": y}).json() for y in ("2023", "2024")]
    response = client.get("/insights/ranking", params={**common, "period": "2024", "start": "2023", "end": "2024"})
    assert response.status_code == 200
    combined = response.json()
    assert combined["total"] == pytest.approx(sum(y["total"] for y in years))
    for code in ("DE", "FR", "BE"):
        expected = sum(next(i["value"] for i in y["items"] if i["code"] == code) for y in years)
        item = next(i for i in combined["items"] if i["code"] == code)
        assert item["value"] == pytest.approx(expected)
        assert item["share"] == pytest.approx(expected / combined["total"])
    ladder = client.get("/insights/ladder", params={**common, "period": "2024", "start": "2023", "end": "2024"}).json()
    assert ladder["total"] == pytest.approx(combined["total"])
    single = client.get("/insights/ranking", params={**common, "period": "2024", "start": "2024", "end": "2024"}).json()
    assert single["items"] == years[1]["items"]


def test_chatbot_range_ops_persist_and_undo(client):
    from agents.kit.view import flow_range_ops
    before = client.get("/view").json()
    # Both front-end and agent changes use the same ordinary view operations.
    spec = before.get("spec", before)
    ops = flow_range_ops(spec, "2023", "2024")
    result = client.post("/view/ops", json={"agent": {"name": "visualizer", "run_id": "range-test"},
                                          "base_version": spec["version"], "ops": ops})
    assert result.status_code == 200, result.text
    changed = result.json()["spec"]
    panels = {p["id"]: p for p in changed["panels"]}
    assert changed["context"]["start"] == "2023" and changed["context"]["period"] == "2024"
    for pid in ("headline", "crossroads", "kpi_imports", "kpi_balance", "kpi_exports"):
        assert all(q["params"]["start"] == "$start" and q["params"]["end"] == "$period" for q in panels[pid]["queries"])
    assert client.get("/view").json()["panels"] == changed["panels"]
    headline = client.get("/insights/ladder", params=resolved(panels["headline"]["queries"][0], changed["context"])).json()
    assert headline["span"] == "2023–2024"
    undone = client.post("/view/undo").json()["spec"]
    assert undone["panels"] == spec["panels"]


def resolved(query: dict, context: dict) -> dict:
    from agents.kit import fill_placeholders
    return fill_placeholders(query["params"], context)


def test_a_range_then_set_context_moves_every_page_panel(client):
    """The range is part of the page context: after From/To 2022-2022, an agent's set_context 2020 moves
    every baseline panel's queries to 2020 (no panel stays on a literal year)."""
    from agents.kit.view import flow_range_ops
    spec = client.get("/view").json()
    r = post(client, {"agent": {"name": "user", "run_id": "range-2022"}, "ops": flow_range_ops(spec, "2022", "2022")})
    assert r.status_code == 200, r.text
    r = post(client, set_context("viz-2020", agent="visualizer", period="2020"))
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    ctx = spec["context"]
    for panel in spec["panels"]:
        for q in panel["queries"]:
            params = resolved(q, ctx)
            for name in ("period", "end"):
                if name in params and name in q["params"] and str(q["params"][name]).startswith("$"):
                    assert params[name] == "2020", (panel["id"], q["key"], params)
            if q["params"].get("start") == "$start":
                assert params["start"] == "2020", (panel["id"], params)  # 2022 is after the page year
        assert not any(isinstance(v, str) and v == "2022" for q in panel["queries"] for v in q["params"].values()), panel["id"]
    # The single-year range asks what the baseline asks, and a month still reads the monthly data.
    cross = next(p for p in spec["panels"] if p["id"] == "crossroads")
    one = client.get("/insights/ranking", params=resolved(cross["queries"][1], ctx)).json()
    base = client.get("/insights/ranking", params={"by": "partner", "period": "2020", "flow": "EXPORT", "max_items": 8}).json()
    assert one["items"] == base["items"] and one["span"] == "2020"
    month = client.get("/insights/ranking", params=resolved(cross["queries"][1], {**ctx, "period": "2020-03"}))
    assert month.status_code == 200 and month.json()["period"] == "2020-03"


def test_a_persisted_page_with_literal_years_is_repaired_on_load(backend, tmp_path):
    """A state saved by the old controls (the page's own panels on literal 2022, the trend from 2015 under
    the baseline's name) comes back following the context, as a new version that undo can reverse."""
    view = backend[1]
    from view.store import ViewStore
    path = tmp_path / "old_state.json"
    store = ViewStore(path)
    spec = copy.deepcopy(store.spec)
    for p in spec["panels"]:
        if p["id"] in ("kpi_exports", "crossroads"):
            p["agent"] = "user"
            for q in p["queries"]:
                q["params"]["period"] = "2022"
        if p["id"] == "trend":
            p["title"] = "Imports have been larger than exports every year since 2015"
            for q in p["queries"]:
                q["params"]["start"] = "2015"
    store.spec = spec
    store.history.append({"version": 0, "at": "x", "agent": "user", "action": "ops", "message": None,
                          "run_id": None, "n_ops": 1, "spec": spec})
    store._save()
    again = ViewStore(path)
    panels = {p["id"]: p for p in again.spec["panels"]}
    assert panels["kpi_exports"]["queries"][0]["params"]["period"] == "$period"
    assert panels["crossroads"]["agent"] == "baseline"
    assert panels["trend"]["queries"][0]["params"]["start"] == "1999" and "1999" in panels["trend"]["title"]
    assert again.spec["version"] == 1 and again.history[-1]["action"] == "repair"
    assert view.validate.validate_spec(again.spec) == [] if hasattr(view, "validate") else True
    assert ViewStore(path).spec["version"] == 1  # repaired once, not on every load


@pytest.mark.parametrize("start,end", [("2025", "2023"), ("2020-01", "2024"), ("0000", "2024")])
def test_bad_ranking_ranges_rejected(client, start, end):
    response = client.get("/insights/ranking", params={"by": "partner", "period": "2024", "start": start, "end": end})
    assert response.status_code == 400


# --------------------------------------------------------------------------- contract 1.5: a panel's own context
def panel_ctx(run_id: str, agent: str = "user", **fields) -> dict:
    return {"agent": {"name": agent, "run_id": run_id}, "ops": [{"op": "set_panel_context", **fields}]}


def own(spec: dict, pid: str):
    return next(p for p in spec["panels"] if p["id"] == pid).get("context")


def test_set_panel_context_applies_undoes_redoes_and_resets(client):
    r = post(client, panel_ctx("user-pc-1", id="peers", period="2020", product="85"))
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert own(spec, "peers") == {"period": "2020", "product": "85"}
    assert all(own(spec, p["id"]) is None for p in spec["panels"] if p["id"] != "peers")
    # the fields given are the whole own context: the goods follow the page again
    r = post(client, panel_ctx("user-pc-2", id="peers", period="2019"))
    assert own(r.json()["spec"], "peers") == {"period": "2019"}
    r = post(client, panel_ctx("user-pc-3", id="peers"))           # follow the page again
    assert own(r.json()["spec"], "peers") is None
    assert own(client.post("/view/undo").json()["spec"], "peers") == {"period": "2019"}
    assert own(client.post("/view/undo").json()["spec"], "peers") == {"period": "2020", "product": "85"}
    assert own(client.post("/view/redo").json()["spec"], "peers") == {"period": "2019"}
    assert "set_panel_context" in client.get("/view/history").json()["entries"][3]["op_kinds"]
    assert own(client.post("/view/reset").json()["spec"], "peers") is None


def test_the_page_context_moves_only_the_charts_that_follow_it(client):
    assert post(client, panel_ctx("user-pc-4", id="mix_export", period="2019")).status_code == 200
    r = post(client, set_context("viz-pc-5", agent="visualizer", period="2020"))
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert spec["context"]["period"] == "2020" and own(spec, "mix_export") == {"period": "2019"}


def test_set_panel_context_checks_the_data(client):
    for fields, path in [({"period": "2031"}, "/ops/0/period"), ({"product": "XYZ"}, "/ops/0/product")]:
        r = post(client, panel_ctx("user-pc-x" + path[-3:], id="peers", **fields))
        assert r.status_code == 422, r.text
        err = r.json()["errors"][0]
        assert err["code"] == "preflight" and err["path"] == path
    r = post(client, panel_ctx("user-pc-y", id="mix_export", product="85"))
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "no_placeholder"
    r = post(client, panel_ctx("user-pc-z", id="nope", period="2020"))
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "unknown_panel"
    assert client.get("/view").json()["version"] == 0


def test_a_mark_on_a_chart_with_its_own_year_is_preflighted_with_it(client):
    """The pre-flight and the provenance fill a mark's placeholders from its chart's own context."""
    assert post(client, panel_ctx("user-pc-6", id="peers", period="2020", product="85")).status_code == 200
    mark = {"kind": "highlight", "target": "peers", "note": "Luxembourg among them.", "params": {"codes": ["LU"]},
            "query": {"endpoint": "/insights/ranking", "params": {"by": "reporter", "period": "$period",
                                                                 "product": "$product", "flow": "EXPORT"}}}
    r = post(client, {"agent": {"name": "visualizer", "run_id": "viz-pc-7"}, "ops": [{"op": "add_mark", "mark": mark}]})
    assert r.status_code == 200, r.text
    m = r.json()["spec"]["marks"][-1]
    assert "/insights/ranking?by=reporter&period=2020&product=85&flow=EXPORT" in [s["url"] for s in m["provenance"]["sources"]]


def test_a_chart_with_its_own_year_preflights_a_new_panel_with_it(client):
    """A bump chart ending in 2024 with start $period: invalid on the 2025 page, valid with its own 2020."""
    panel = {**bump_panel(), "context": {"period": "2020"}}
    r = post(client, {"agent": {"name": "trend_finder", "run_id": "tf-pc-8"}, "ops": [{"op": "upsert_panel", "panel": panel}]})
    assert r.status_code == 200, r.text
    assert own(r.json()["spec"], "ranks") == {"period": "2020"}
    r = post(client, {"agent": {"name": "trend_finder", "run_id": "tf-pc-9"},
                      "ops": [{"op": "upsert_panel", "panel": {**bump_panel(), "id": "ranks3"}}]})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "preflight"


def test_an_agent_cannot_change_a_pinned_charts_year(client):
    assert post(client, {"agent": {"name": "user"}, "ops": [{"op": "pin_panel", "id": "peers", "pinned": True}]}).status_code == 200
    r = post(client, panel_ctx("viz-pc-10", agent="visualizer", id="peers", period="2020"))
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "pinned"
    assert post(client, panel_ctx("user-pc-11", id="peers", period="2020")).status_code == 200


def test_a_repaired_page_panel_keeps_its_own_context(backend, tmp_path):
    store_mod = sys.modules["view.store"]
    base = baseline()
    spec = copy.deepcopy(base)
    spec["version"] = 3
    peers = next(p for p in spec["panels"] if p["id"] == "peers")
    peers.update({"title": "An old title", "agent": "baseline", "context": {"period": "2020"}})
    fixed = store_mod.repair_page_panels(spec, base)
    assert fixed and own(fixed, "peers") == {"period": "2020"}
    assert next(p for p in fixed["panels"] if p["id"] == "peers")["title"] == next(p for p in base["panels"] if p["id"] == "peers")["title"]



