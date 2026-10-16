# SPDX-License-Identifier: MIT
"""The view-spec contract: schemas, examples, the backend's validator and the agent kit agree."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "contract"
EXAMPLES = CONTRACT / "examples"
VIEW_DIR = REPO / "app" / "backend" / "view"


def _load_view_package():
    """Import app/backend/view without putting app/backend on sys.path (its modules would shadow others)."""
    if "view" in sys.modules and getattr(sys.modules["view"], "__file__", "").startswith(str(VIEW_DIR)):
        return sys.modules["view"]
    spec = importlib.util.spec_from_file_location("view", VIEW_DIR / "__init__.py",
                                                  submodule_search_locations=[str(VIEW_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["view"] = module
    spec.loader.exec_module(module)
    return module


view = _load_view_package()
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from agents.kit import ViewClient, fact_api, fact_from, fact_op, get_path, query, source_url  # noqa: E402


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def initial() -> dict:
    return load("initial.json")


@pytest.fixture
def after03() -> dict:
    return load("spec_after_03.json")


def paths(errors: list[dict]) -> set[str]:
    return {e["path"] for e in errors}


def codes(errors: list[dict]) -> set[str]:
    return {e["code"] for e in errors}


# --------------------------------------------------------------------------- copies and examples
@pytest.mark.parametrize("name", ["view-spec.schema.json", "ops.schema.json"])
def test_backend_schema_copies_are_identical(name):
    assert (VIEW_DIR / "schema" / name).read_bytes() == (CONTRACT / name).read_bytes(), \
        f"app/backend/view/schema/{name} differs from contract/{name}: copy it again"


def test_backend_baseline_is_the_initial_example():
    assert (VIEW_DIR / "baseline.json").read_bytes() == (EXAMPLES / "initial.json").read_bytes()
    assert view.load_baseline()["version"] == 0


def test_specs_validate(initial, after03):
    assert view.validate_spec(initial) == []
    assert view.validate_spec(after03) == []
    assert {p["id"] for p in initial["panels"]} == {
        "kpi_imports", "kpi_balance", "kpi_exports", "headline", "crossroads", "trend", "mix_export",
        "specialisation", "peers"}


@pytest.mark.parametrize("name,base", [
    ("01_smartphone_thread.json", "initial.json"),
    ("02_events.json", "initial.json"),
    ("03_forecast.json", "initial.json"),
    ("04_keep_remove.json", "spec_after_03.json"),
    ("05_clear.json", "spec_after_03.json"),
    ("06_maps_and_waterfall.json", "initial.json"),
    ("06b_waterfall.json", "initial.json"),
    ("07_heatmap_and_breaks.json", "initial.json"),
    ("08_context_2020.json", "initial.json"),
    ("08_context_2020.json", "spec_after_03.json"),
    ("10_persona_journalist.json", "initial.json"),
])
def test_example_batches_validate(name, base):
    assert view.validate_ops(load(name), load(base)) == []


def test_validation_with_meta(initial):
    meta = {"dimensions": [
        {"id": "freq", "values": [{"code": "A"}, {"code": "M"}]},
        {"id": "reporter"}, {"id": "partner"}, {"id": "product"},
        {"id": "flow", "values": [{"code": "IMPORT"}, {"code": "EXPORT"}]},
        {"id": "indicators"}]}
    assert view.validate_spec(initial, meta) == []
    bad = copy.deepcopy(initial)
    bad["panels"][5]["queries"][0]["params"]["flow"] = "SMUGGLED"
    errors = view.validate_spec(bad, meta)
    assert paths(errors) == {"/panels/5/queries/0/params/flow"} and codes(errors) == {"bad_param"}


# --------------------------------------------------------------------------- negatives
def _batch(*ops, agent="visualizer"):
    return {"agent": {"name": agent, "run_id": "test-1"}, "ops": list(ops)}


def _bar(pid="extra", **over):
    panel = {"id": pid, "type": "bar", "title": "Extra",
             "queries": [{"key": "main", "endpoint": "/insights/ranking/records",
                          "params": {"by": "partner", "period": "2025", "flow": "EXPORT"}}]}
    panel.update(over)
    return panel


def _event(**params):
    return {"op": "add_mark", "mark": {"kind": "event", "target": "trend", "note": "A dated event.",
                                       "params": {"at": "2020-03", "label": "COVID-19", **params}}}


NEGATIVES = {
    "unknown endpoint": (
        _batch({"op": "upsert_panel", "panel": _bar(queries=[{"key": "main", "endpoint": "/insights/secret",
                                                                "params": {}}])}),
        "/ops/0/panel/queries/0/endpoint", "unknown_endpoint"),
    "data array inside a panel": (
        _batch({"op": "upsert_panel", "panel": _bar(data=[{"name": "France", "value": 3.1e9}])}),
        "/ops/0/panel/data", "unexpected"),
    "data inside params": (
        _batch({"op": "upsert_panel", "panel": _bar(queries=[{"key": "main", "endpoint": "/insights/series",
                                                                "params": {"rows": [1, 2, 3]}}])}),
        "/ops/0/panel/queries/0/params/rows", "schema"),
    "mark on unknown target": (
        _batch({"op": "add_mark", "mark": {"kind": "event", "target": "nowhere", "note": "x",
                                           "params": {"at": "2020-03", "label": "COVID-19"}}}),
        "/ops/0/mark/target", "unknown_target"),
    "bad period in mark params": (_batch(_event(at="2020-13")), "/ops/0/mark/params/at", "schema"),
    "bad period in query": (
        _batch({"op": "upsert_panel", "panel": _bar(queries=[{"key": "main", "endpoint": "/insights/ranking",
                                                                "params": {"by": "partner", "period": "25"}}])}),
        "/ops/0/panel/queries/0/params/period", "bad_period"),
    "51 ops": (_batch(*[{"op": "set_title", "title": "t"}] * 51), "/ops", "too_many_ops"),
    "missing agent": ({"ops": [{"op": "set_title", "title": "t"}]}, "/agent", "required"),
    "text panel without provenance": (
        _batch({"op": "upsert_panel", "panel": {"id": "note", "type": "text", "title": "Note", "text": "Words.",
                                                "queries": []}}),
        "/ops/0/panel/provenance", "required"),
    "forecast mark without query": (
        _batch({"op": "add_mark", "mark": {"kind": "forecast", "target": "trend", "series": "exports",
                                           "note": "A band.", "params": {}}}),
        "/ops/0/mark/query", "required"),
    "annotation with a y": (
        _batch({"op": "add_mark", "mark": {"kind": "annotation", "target": "trend", "note": "Here.",
                                           "params": {"x": "2020", "label": "Dip", "y": 12.1}}}),
        "/ops/0/mark/params/y", "unexpected"),
    "figure typed into a note": (
        _batch({"op": "add_mark", "mark": {"kind": "annotation", "target": "trend", "note": "Exports fell €1.2 bn.",
                                           "params": {"x": "2020", "label": "Dip"}}}),
        "/ops/0/mark/note", "figure_in_text"),
    "percentage typed into a text panel": (
        _batch({"op": "upsert_panel", "panel": {"id": "note", "type": "text", "title": "Note",
                                                "text": "France takes 86% of them.", "queries": [],
                                                "provenance": {"sources": []}}}),
        "/ops/0/panel/text", "figure_in_text"),
    "placeholder without fact": (
        _batch({"op": "upsert_panel", "panel": {"id": "note", "type": "text", "title": "Note",
                                                "text": "Exports were {exports}.", "queries": [],
                                                "provenance": {"sources": []}}}),
        "/ops/0/panel/text", "unknown_fact"),
    "unknown parameter": (
        _batch({"op": "upsert_panel", "panel": _bar(queries=[{"key": "main", "endpoint": "/insights/ranking",
                                                                "params": {"by": "partner", "period": "2025",
                                                                           "colour": "red"}}])}),
        "/ops/0/panel/queries/0/params/colour", "unknown_param"),
    "missing required parameter": (
        _batch({"op": "upsert_panel", "panel": _bar(queries=[{"key": "main", "endpoint": "/insights/movers",
                                                                "params": {"by": "partner", "period": "2025"}}])}),
        "/ops/0/panel/queries/0/params/previous", "missing_param"),
    "thread on a line panel": (
        _batch({"op": "add_mark", "mark": {"kind": "thread", "target": "trend", "note": "x",
                                           "params": {"in_code": "CN", "out_code": "FR", "label": "Phones"}}}),
        "/ops/0/mark/target", "wrong_target"),
    "unknown series": (
        _batch({"op": "add_mark", "mark": {"kind": "forecast", "target": "trend", "series": "re_exports",
                                           "note": "A band.", "params": {},
                                           "query": {"endpoint": "/insights/forecast", "params": {"horizon": 12}}}}),
        "/ops/0/mark/series", "unknown_series"),
    "target added later in the batch": (
        _batch({"op": "add_mark", "mark": {"kind": "highlight", "target": "extra", "note": "x",
                                           "params": {"codes": ["FR"]}}},
               {"op": "upsert_panel", "panel": _bar()}),
        "/ops/0/mark/target", "unknown_target"),
    "layout leaving a panel out": (
        _batch({"op": "set_layout", "rows": [{"panels": ["headline"]}]}), "/ops/0/rows", "not_in_layout"),
    "keep an unknown mark": (_batch({"op": "keep_mark", "id": "m99"}), "/ops/0/id", "unknown_mark"),
    "baseline is reserved": (_batch({"op": "set_title", "title": "t"}, agent="baseline"), "/agent/name",
                             "reserved_agent"),
    "fact from an unknown query": (
        _batch({"op": "upsert_panel", "panel": _bar(text="{x} in total",
                                                    facts={"x": {"query": "nope", "path": "total"}})}),
        "/ops/0/panel/facts/x/query", "unknown_query"),
}


# --------------------------------------------------------------------------- contract 1.1
def _q(endpoint, key="main", **params):
    return {"key": key, "endpoint": endpoint, "params": params}


def _panel(type_, *queries, pid="extra", **over):
    panel = {"id": pid, "type": type_, "title": "Extra", "queries": list(queries)}
    panel.update(over)
    return panel


MOVERS = dict(by="product", period="$period", previous="$previous", flow="EXPORT")
RANK_P = dict(by="partner", period="$period", flow="EXPORT")
GOOD_PANELS = {
    "waterfall": _panel("waterfall", _q("/insights/movers", **MOVERS), options={"limit": 8, "show_other": True}),
    "choropleth share": _panel("choropleth", _q("/insights/ranking", **RANK_P, max_items=60, threshold=0),
                               options={"measure": "share", "extent": "world"}),
    "choropleth balance": _panel("choropleth", _q("/insights/flows", period="2025"), options={"measure": "balance"}),
    "flowmap": _panel("flowmap", _q("/insights/flows", period="$period", previous="$previous", max_items=10),
                      options={"flow": "both", "extent": "europe"}),
    "heatmap month_year": _panel("heatmap", _q("/insights/series", flow="EXPORT", freq="M", start="1999-01"),
                                 _q("/trends/analyze", key="analysis", flow="EXPORT", freq="M", penalty=1.5),
                                 options={"kind": "month_year"}),
    "heatmap product_year": _panel("heatmap", _q("/insights/matrix", by="product", start="1999", end="$period",
                                                 flow="EXPORT", measure="share", max_items=12),
                                   options={"kind": "product_year"}),
    "heatmap seasonal": _panel("heatmap", _q("/trends/seasonal-heatmap", flow="EXPORT", freq="M", top=12),
                               options={"kind": "seasonal"}),
    "treemap": _panel("treemap", _q("/insights/tree", by="product", period="$period", flow="EXPORT", depth=2,
                                    threshold="auto")),
    "treemap numeric threshold": _panel("treemap", _q("/insights/tree", by="product", period="2025", depth="3",
                                                      threshold=0.02, partner="FR")),
    "butterfly": _panel("butterfly", _q("/insights/ranking", key="in", by="product", period="$period", flow="IMPORT"),
                        _q("/insights/ranking", key="out", by="product", period="$period", flow="EXPORT")),
    "bump": _panel("bump", _q("/insights/matrix", by="partner", start="2015", end="$period", flow="EXPORT",
                              measure="rank", max_items=10), options={"labels": ["FR", "DE"]}),
    "scatter": _panel("scatter", _q("/insights/specialisation", by="product", period="$period", flow="EXPORT"),
                      _q("/insights/movers", key="growth", **MOVERS),
                      options={"x": "rca", "y": "growth", "size": "value"}),
    "product placeholder": _bar(queries=[_q("/insights/ranking/records", by="partner", period="$period",
                                            product="$product", flow="EXPORT")]),
}


@pytest.mark.parametrize("case", sorted(GOOD_PANELS))
def test_new_panel_types_validate(case, initial):
    assert view.validate_ops(_batch({"op": "upsert_panel", "panel": GOOD_PANELS[case]}), initial) == []


def _with(case, **over):
    panel = copy.deepcopy(GOOD_PANELS[case])
    panel.update(over)
    return _batch({"op": "upsert_panel", "panel": panel})


P0 = "/ops/0/panel"
NEGATIVES_1_1 = {
    "waterfall on ranking": (_with("waterfall", queries=[_q("/insights/ranking", **RANK_P)]),
                             f"{P0}/queries/0/endpoint", "bad_queries"),
    "waterfall with two queries": (_with("waterfall", queries=[_q("/insights/movers", **MOVERS),
                                                               _q("/insights/movers", key="b", **MOVERS)]),
                                   f"{P0}/queries", "schema"),
    "choropleth unknown measure": (_with("choropleth share", options={"measure": "density"}),
                                   f"{P0}/options/measure", "schema"),
    "choropleth balance on ranking": (_with("choropleth share", options={"measure": "balance"}),
                                      f"{P0}/queries/0/endpoint", "bad_queries"),
    "choropleth by product": (_with("choropleth share", queries=[_q("/insights/ranking", by="product",
                                                                    period="2025")]),
                              f"{P0}/queries/0/params/by", "bad_param"),
    "flowmap bad extent": (_with("flowmap", options={"extent": "mars"}), f"{P0}/options/extent", "schema"),
    "heatmap without kind": (_with("heatmap month_year", options={"limit": 3}), f"{P0}/options/kind", "required"),
    "heatmap kind and endpoint disagree": (_with("heatmap month_year", options={"kind": "product_year"}),
                                           f"{P0}/queries/0/endpoint", "bad_queries"),
    "month_year heatmap on annual data": (_with("heatmap month_year",
                                                queries=[_q("/insights/series", flow="EXPORT", freq="A")]),
                                          f"{P0}/queries/0/params/freq", "bad_param"),
    "seasonal heatmap without freq": (_with("heatmap seasonal", queries=[_q("/trends/seasonal-heatmap", top=5)]),
                                      f"{P0}/queries/0/params/freq", "bad_param"),
    "matrix unknown measure": (_with("heatmap product_year",
                                     queries=[_q("/insights/matrix", by="product", start="1999", end="2025",
                                                 measure="mood")]),
                               f"{P0}/queries/0/params/measure", "bad_param"),
    "matrix without start": (_with("bump", queries=[_q("/insights/matrix", by="partner", end="2025",
                                                       measure="rank")]),
                             f"{P0}/queries/0/params/start", "missing_param"),
    "bump on values": (_with("bump", queries=[_q("/insights/matrix", by="partner", start="2015", end="2025",
                                                 measure="value")]),
                       f"{P0}/queries/0/params/measure", "bad_param"),
    "tree by partner": (_with("treemap", queries=[_q("/insights/tree", by="partner", period="2025")]),
                        f"{P0}/queries/0/params/by", "bad_param"),
    "tree depth 4": (_with("treemap", queries=[_q("/insights/tree", by="product", period="2025", depth=4)]),
                     f"{P0}/queries/0/params/depth", "bad_param"),
    "tree bad threshold": (_with("treemap", queries=[_q("/insights/tree", by="product", period="2025",
                                                        threshold="lots")]),
                           f"{P0}/queries/0/params/threshold", "bad_param"),
    "butterfly sides swapped": (_with("butterfly", queries=[
        _q("/insights/ranking", key="in", by="product", period="2025", flow="EXPORT"),
        _q("/insights/ranking", key="out", by="product", period="2025", flow="IMPORT")]),
        f"{P0}/queries/0/params/flow", "bad_param"),
    "butterfly sides disagree": (_with("butterfly", queries=[
        _q("/insights/ranking", key="in", by="product", period="2025", flow="IMPORT"),
        _q("/insights/ranking", key="out", by="partner", period="2025", flow="EXPORT")]),
        f"{P0}/queries", "bad_queries"),
    "butterfly without out": (_with("butterfly", queries=[
        _q("/insights/ranking", key="in", by="product", period="2025", flow="IMPORT"),
        _q("/insights/ranking", key="other", by="product", period="2025", flow="EXPORT")]),
        f"{P0}/queries/1/key", "bad_queries"),
    "scatter without y": (_with("scatter", options={"x": "rca"}), f"{P0}/options/y", "required"),
    "scatter growth on series": (_with("scatter", queries=[
        _q("/insights/specialisation", by="product", period="2025"),
        _q("/insights/series", key="growth", flow="EXPORT")]),
        f"{P0}/queries/1/endpoint", "bad_queries"),
    "unknown placeholder": (_batch({"op": "upsert_panel", "panel": _bar(queries=[
        _q("/insights/ranking", by="partner", period="$year")])}),
        f"{P0}/queries/0/params/period", "bad_placeholder"),
    "placeholder in the wrong param": (_batch({"op": "upsert_panel", "panel": _bar(queries=[
        _q("/insights/ranking", by="partner", period="$product")])}),
        f"{P0}/queries/0/params/period", "bad_placeholder"),
    "trends endpoint unknown param": (_with("heatmap seasonal", queries=[
        _q("/trends/seasonal-heatmap", freq="M", depth=2)]),
        f"{P0}/queries/0/params/depth", "unknown_param"),
}


@pytest.mark.parametrize("case", sorted(NEGATIVES_1_1))
def test_new_panel_types_are_checked(case, initial):
    envelope, path, code = NEGATIVES_1_1[case]
    errors = view.validate_ops(envelope, initial)
    assert path in paths(errors), f"{case}: {errors}"
    assert code in codes(errors), f"{case}: {errors}"


def _mark_on(case, mark, initial):
    """Errors of a batch that adds GOOD_PANELS[case] (id 'extra') and then the mark."""
    env = _batch({"op": "upsert_panel", "panel": GOOD_PANELS[case]}, {"op": "add_mark", "mark": mark})
    return view.validate_ops(env, initial)


ANALYZE = {"endpoint": "/trends/analyze", "params": {"flow": "EXPORT", "freq": "M", "start": "1999-01"}}


def _break(**params):
    cp = "changepoints.[period=2009-01]"
    return {"kind": "trend_break", "target": "extra", "note": "From {before} to {after}.", "query": ANALYZE,
            "params": {"at": "2009-01", "label": "Crisis", "before": "before", "after": "after", **params},
            "facts": {"before": {"query": "self", "path": f"{cp}.before_mean", "format": "eur"},
                      "after": {"query": "self", "path": f"{cp}.after_mean", "format": "eur"}}}


def test_new_marks_on_new_panels(initial):
    ok = [
        ("heatmap month_year", _break()),
        ("heatmap product_year", _break(at="2009", **{"from": "2005", "to": "2015"})),
        ("flowmap", {"kind": "thread", "target": "extra", "note": "Phones.", "period": "2025",
                     "params": {"in_code": "CN", "out_code": "FR", "label": "Phones", "product": "85171300"}}),
        ("waterfall", {"kind": "annotation", "target": "extra", "note": "Machinery.", "params": {"x": "85",
                                                                                                 "label": "HS 85"}}),
    ] + [(case, {"kind": "highlight", "target": "extra", "note": "These.", "params": {"codes": ["FR", "85"]}})
         for case in ("choropleth share", "flowmap", "treemap", "butterfly", "bump", "scatter")]
    for case, mark in ok:
        assert _mark_on(case, mark, initial) == [], case
    # a trend break on the baseline line, without a query of its own
    line_break = {"kind": "trend_break", "target": "trend", "series": "exports", "note": "A shift.",
                  "params": {"at": "2009"}}
    assert view.validate_ops(_batch({"op": "add_mark", "mark": line_break}), initial) == []


def test_new_marks_are_checked(initial):
    def check(case, mark, path, code):
        errors = _mark_on(case, mark, initial)
        assert path in paths(errors) and code in codes(errors), f"{mark}: {errors}"

    m = "/ops/1/mark"
    check("bump", _break(), f"{m}/target", "wrong_target")
    check("heatmap seasonal", _break(), f"{m}/target", "wrong_target")
    check("heatmap month_year", _break(before="level"), f"{m}/params/before", "unknown_fact")
    check("heatmap month_year", _break(**{"from": "2010-01"}), f"{m}/params/from", "bad_period")
    check("heatmap month_year", _break(to="2001-01"), f"{m}/params/to", "bad_period")
    check("heatmap month_year", _break(at="January 2009"), f"{m}/params/at", "schema")
    check("heatmap month_year", {**_break(), "query": {"endpoint": "/insights/trend", "params": {}}},
          f"{m}/query/endpoint", "schema")
    check("treemap", {"kind": "thread", "target": "extra", "note": "x",
                      "params": {"in_code": "CN", "out_code": "FR", "label": "Phones"}}, f"{m}/target", "wrong_target")
    check("treemap", {"kind": "highlight", "target": "extra", "note": "x", "params": {"from": "2020", "to": "2021"}},
          f"{m}/params", "wrong_target")
    check("scatter", {"kind": "annotation", "target": "extra", "note": "x", "params": {"x": "85", "label": "l"}},
          f"{m}/target", "wrong_target")
    check("waterfall", {"kind": "annotation", "target": "extra", "note": "x", "period": "25",
                        "params": {"x": "85", "label": "l"}}, f"{m}/period", "schema")


def test_page_context(initial):
    spec = copy.deepcopy(initial)
    spec["context"] = {"period": "2020", "product": "8517", "flow": "EXPORT"}
    assert view.validate_spec(spec) == []
    spec["context"] = {"period": "2020"}
    assert "/context/product" in paths(view.validate_spec(spec))
    for op, path in [({"op": "set_context"}, "/ops/0"),
                     ({"op": "set_context", "period": "20"}, "/ops/0/period"),
                     ({"op": "set_context", "flow": "SIDEWAYS"}, "/ops/0/flow"),
                     ({"op": "set_context", "period": "2020", "colour": "red"}, "/ops/0/colour")]:
        assert path in paths(view.validate_ops(_batch(op), initial)), op
    for op in ({"op": "set_context", "product": "TOTAL"}, {"op": "set_context", "period": "2024-03", "product": "85"},
               {"op": "set_context", "flow": "BOTH"}):
        assert view.validate_ops(_batch(op, agent="user"), initial) == []
    marked = _batch({"op": "add_mark", "mark": {"kind": "event", "target": "trend", "note": "x", "period": "2020-03",
                                                "params": {"at": "2020-03", "label": "COVID-19"}}})
    assert view.validate_ops(marked, initial) == []
    upd = _batch({"op": "update_mark", "id": "m3", "set": {"period": "2018"}})
    assert view.validate_ops(upd, load("spec_after_03.json")) == []


def test_persona_in_the_context(initial):
    """Contract 1.4: spec.context.persona and set_context {persona} (additive)."""
    spec = copy.deepcopy(initial)
    spec["context"] = {"period": "2025", "product": "TOTAL", "persona": "statec_analyst"}
    assert view.validate_spec(spec) == []
    for bad in ("Journalist", "x", "journalist!", "a" * 40):
        spec["context"]["persona"] = bad
        assert "/context/persona" in paths(view.validate_spec(spec)), bad
    for op in ({"op": "set_context", "persona": "journalist"},
               {"op": "set_context", "persona": "exporter", "period": "2025", "product": "40", "flow": "EXPORT"}):
        assert view.validate_ops(_batch(op, agent="user"), initial) == [], op
    assert "/ops/0/persona" in paths(view.validate_ops(_batch({"op": "set_context", "persona": "Citizen"}), initial))
    assert view.validate.context_of({"context": {"period": "2020", "product": "85", "persona": "citizen"}})["persona"] == "citizen"
    assert "persona" not in view.validate.context_of(initial)


def test_placeholders_are_filled_for_the_preflight():
    v = view.validate
    q = {"endpoint": "/insights/movers", "params": {"by": "product", "period": "$period", "previous": "$previous",
                                                    "product": "$product", "flow": "EXPORT"}}
    assert v.query_url(q) == "/insights/movers?by=product&period=2025&previous=2024&product=TOTAL&flow=EXPORT"
    ctx = {"period": "2020-03", "product": "8517"}
    assert v.query_url(q, ctx) == "/insights/movers?by=product&period=2020-03&previous=2019-03&product=8517&flow=EXPORT"
    assert v.previous_period("2000") == "1999" and v.previous_period("2025-01") == "2024-01"
    assert v.context_of({}) == {"period": "2025", "product": "TOTAL"}
    assert v.context_of({"context": {"period": "2020", "product": "85"}})["period"] == "2020"
    assert v.fill_params({"top": 12, "flow": "EXPORT"}, ctx) == {"top": 12, "flow": "EXPORT"}
    found = v.iter_queries(load("06_maps_and_waterfall.json"))
    assert all("$" not in v.query_url(q) and "%24" not in v.query_url(q) for _, q in found)
    assert any(q["params"].get("period") == "$period" for _, q in found)  # the spec keeps them as written


def test_new_endpoints_are_known():
    for endpoint in ("/insights/matrix", "/insights/tree", "/trends/analyze", "/trends/seasonal-heatmap"):
        assert endpoint in view.ENDPOINT_PARAMS
    schema_endpoints = set(json.loads((CONTRACT / "view-spec.schema.json").read_text(encoding="utf-8"))
                           ["$defs"]["endpoint"]["enum"])
    assert schema_endpoints == set(view.ENDPOINT_PARAMS)
    assert set(view.MARK_ON["highlight"]) >= {"choropleth", "flowmap", "treemap", "butterfly", "bump", "scatter"}


@pytest.mark.parametrize("case", sorted(NEGATIVES))
def test_negative_batches_are_rejected_with_paths(case, initial):
    envelope, path, code = NEGATIVES[case]
    errors = view.validate_ops(envelope, initial)
    assert errors, f"{case}: accepted"
    assert path in paths(errors), f"{case}: {errors}"
    assert code in codes(errors), f"{case}: {errors}"


def test_pinned_panel_and_marked_panel_cannot_be_removed(after03):
    pinned = copy.deepcopy(after03)
    pinned["panels"][0]["pinned"] = True
    errors = view.validate_ops(_batch({"op": "remove_panel", "id": pinned["panels"][0]["id"]}), pinned)
    assert codes(errors) == {"pinned"}
    errors = view.validate_ops(_batch({"op": "remove_panel", "id": "crossroads"}), after03)
    assert codes(errors) == {"has_marks"}
    assert view.validate_ops(_batch({"op": "remove_panel", "id": "crossroads", "force": True},
                                    {"op": "set_layout", "rows": [{"panels": ["headline"]}]}), after03)
    assert view.validate_ops(_batch({"op": "remove_panel", "id": "crossroads", "force": True}), after03) == []


def test_update_mark_is_revalidated(after03):
    ok = _batch({"op": "update_mark", "id": "m3", "set": {"params": {"at": "2018-07", "label": "US tariffs"}}})
    assert view.validate_ops(ok, after03) == []
    bad = _batch({"op": "update_mark", "id": "m3", "set": {"params": {"at": "July 2018", "label": "US tariffs"}}})
    assert "/ops/0/set/params/at" in paths(view.validate_ops(bad, after03))
    tmp = _batch(_event() | {"tmp_id": "covid"}, {"op": "keep_mark", "id": "covid"})
    assert view.validate_ops(tmp, after03) == []


def test_spec_level_checks(initial):
    bad = copy.deepcopy(initial)
    bad["marks"].append({"id": "m2", "n": 1, "kind": "event", "agent": "cause_finder", "target": "trend",
                         "note": "x", "params": {"at": "2020-03", "label": "COVID-19"}, "status": "proposed",
                         "provenance": {"agent": "cause_finder", "created_at": "2026-10-16T09:00:00Z", "sources": []}})
    bad["layout"]["rows"].pop()
    errors = view.validate_spec(bad)
    assert {"/marks/0/id", "/marks/0/n", "/layout/rows"} <= paths(errors)


# --------------------------------------------------------------------------- helpers
def test_query_helpers():
    q = {"endpoint": "/insights/series", "params": {"flow": "EXPORT", "adjust": True, "partner": "FR,DE"}}
    assert view.query_url(q) == "/insights/series?flow=EXPORT&adjust=true&partner=FR,DE"
    assert source_url(q["endpoint"], q["params"]) == view.query_url(q)
    found = view.iter_queries(load("01_smartphone_thread.json"))
    assert ("/ops/2/mark/facts/bought_cn" in {p for p, _ in found}) and len(found) == 9


def test_fact_paths():
    data = {"total": 10, "items": [{"code": "CN", "value": 6, "is_focus": False},
                                   {"code": "FR", "value": 3, "is_focus": True}], "peers": ["BE", "DE"]}
    for resolve in (view.resolve_path, get_path):
        assert resolve(data, "total") == 10
        assert resolve(data, "items.[code=FR].value") == 3
        assert resolve(data, "items.-1.code") == "FR"
        assert resolve(data, "items.[is_focus=true].#position") == 2
        assert resolve(data, "peers.#length") == 2
        assert resolve([{"value": 1}, {"value": 2}], "-1.value") == 2
        with pytest.raises(KeyError):
            resolve(data, "items.[code=XX].value")
        with pytest.raises(KeyError):
            resolve(data, "items.5")


# --------------------------------------------------------------------------- kit
def test_kit_builds_valid_ops(initial):
    v = ViewClient("http://localhost:1", "trend_finder")
    imports = {"by": "partner", "period": "2025", "flow": "IMPORT", "product": "85171300"}
    drill = {"id": "drill", "type": "bar", "title": "Inside HS 85",
             "queries": [query("/insights/ranking/records", {"by": "product", "parent": "85", "period": "2025",
                                                              "flow": "EXPORT"}, key="main", tone="exports")]}
    ops = [
        v.set_title("Where the export rise came from"),
        v.upsert_panel(drill, after="crossroads"),
        v.thread_mark("crossroads", "CN", "FR", "Smartphones, China to France", "{bought} bought from China.",
                      product="85171300", thread="phones", tmp_id="t1",
                      facts={"bought": fact_api("/insights/ranking", imports, "items.[code=CN].value", "eur")},
                      sources=["/insights/movers?by=product&period=2025&previous=2024&flow=EXPORT"]),
        v.highlight_mark("drill", "The smartphone heading.", codes=["8517"]),
        v.highlight_mark("trend", "The pandemic year.", span=("2020-03", "2020-12")),
        v.event_mark("trend", "2020-03", "COVID-19", "Lockdowns began.", until="2020-05", event_id="covid-19"),
        v.trend_mark("trend", query("/insights/trend", {"flow": "EXPORT", "start": "2015"}), "A rising line.",
                     series="exports"),
        v.forecast_mark("trend", query("/insights/forecast", {"flow": "EXPORT", "horizon": 12}),
                        "{value} next year.", series="exports",
                        facts={"value": fact_from("self", "rows.-1.value", "eur")}),
        v.annotation_mark("trend", "2020", "The dip", "Exports fell in the pandemic year."),
        v.update_mark("t1", note="Smartphones from China to France."),
        v.keep_mark("t1"),
        v.pin_panel("drill"),
        v.clear("marks", agent="trend_finder"),
        v.set_layout([["headline"], ["kpi_imports", "kpi_balance", "kpi_exports"], ["crossroads"], ["drill"],
                      {"panels": ["trend"]}, ["mix_export", "specialisation", "peers"]]),
    ]
    env = v.envelope(ops, message="test", base_version=0)
    assert v.validate(env) == []
    assert view.validate_ops(env, initial) == []
    env = v.envelope([v.remove_mark("m1"), v.remove_panel("phones_drill", force=True)])
    assert view.validate_ops(env, load("spec_after_03.json")) == []
    assert fact_op("diff", "a", "b", "eur") == {"op": "diff", "of": ["a", "b"], "format": "eur"}
    bad = v.envelope([v.add_mark("annotation", "trend", "x", {"x": "2020", "label": "l", "y": 3})])
    assert any(p.startswith("/ops/0/mark/params") for p in v.validate(bad))
    with pytest.raises(ValueError):
        v.send([v.add_mark("forecast", "trend", "x", {})])
    v.close()


def test_kit_builds_the_new_panels_and_marks(initial):
    from agents.kit import (PERIOD, bump_panel, butterfly_panel, choropleth_panel, fill_placeholders, flowmap_panel,
                            heatmap_panel, scatter_panel, treemap_panel, waterfall_panel)
    v = ViewClient("http://localhost:1", "visualizer")
    analyze = query("/trends/analyze", {"flow": "EXPORT", "freq": "M", "start": "1999-01", "penalty": 1.5})
    ops = [
        v.upsert_panel(waterfall_panel("w", "What changed", text="From {a}.",
                                       facts={"a": fact_from("main", "previous_total", "eur")})),
        v.upsert_panel(choropleth_panel("map_share", "Shares", extent="world")),
        v.upsert_panel(choropleth_panel("map_balance", "Balance", measure="balance")),
        v.upsert_panel(flowmap_panel("routes", "Routes", previous="$previous", sources=["/insights/flows?period=2025"])),
        v.upsert_panel(heatmap_panel("hm", "Months")),
        v.upsert_panel(heatmap_panel("hp", "Chapters", kind="product_year")),
        v.upsert_panel(heatmap_panel("hs", "Seasons", kind="seasonal", top=8)),
        v.upsert_panel(treemap_panel("tree", "Goods", depth=3, partner="FR")),
        v.upsert_panel(butterfly_panel("bf", "In and out", by="partner", product="$product")),
        v.upsert_panel(bump_panel("ranks", "Ranks", options={"labels": ["FR", "DE"]})),
        v.upsert_panel(scatter_panel("sc", "Strengths")),
        v.upsert_panel(scatter_panel("sc2", "Strengths over time", growth="trends", start="2015")),
        v.trend_break_mark("hm", "2009-01", "From {before} to {after}.", label="Crisis", query=analyze, thread="b"),
        v.trend_break_mark("trend", "2009", "A shift.", series="exports", period="2009"),
        v.highlight_mark("map_share", "Neighbours.", codes=["DE", "FR", "BE"]),
        v.thread_mark("routes", "CN", "FR", "Phones", "Phones.", product="85171300", period="2025"),
        v.annotation_mark("w", "85", "Machinery", "Machinery."),
        v.highlight_mark("tree", "Phones.", codes=["8517"]),
        v.set_context(period="2020"),
        v.set_context(product="85", flow="EXPORT"),
        v.set_context(persona="journalist"),
    ]
    env = v.envelope(ops, message="test", base_version=0)
    assert v.validate(env) == []
    assert view.validate_ops(env, initial, limit=50) == []      # twelve charts in a row: no replacement here
    assert ops[0]["panel"]["queries"][0]["params"]["period"] == PERIOD
    assert ops[12]["mark"]["params"]["before"] == "before" and "after" in ops[12]["mark"]["facts"]
    assert fill_placeholders({"period": "$period", "previous": "$previous", "product": "$product", "x": 1},
                             {"period": "2020", "product": "85"}) == {"period": "2020", "previous": "2019",
                                                                       "product": "85", "x": 1}
    with pytest.raises(ValueError):
        v.set_context()
    v.close()


# --------------------------------------------------------------------------- comparisons (contract 1.2)
def _series(key, label=None, **params):
    q = {"key": key, "endpoint": "/insights/series",
         "params": {"flow": "EXPORT", "freq": "A", "start": "2015", "end": "$period", **params}}
    if label:
        q["label"] = label
    return q


def _snap(key, period, endpoint="/insights/ranking", **params):
    return {"key": key, "endpoint": endpoint,
            "params": {"by": "partner", "period": period, "flow": "EXPORT", **params}}


def _cmp(queries, pid="extra", ptype="compare", **options):
    return {"id": pid, "type": ptype, "title": "A comparison", "queries": queries, "options": options}


C3 = [_series("lu", "Luxembourg", reporter="LU"), _series("de", "Germany", reporter="DE"),
      _series("be", "Belgium", reporter="BE")]
FLOWS = [_series("exports", "Exports"), {**_series("imports", "Imports"),
                                         "params": {**_series("imports")["params"], "flow": "IMPORT"}}]

GOOD_COMPARISONS = {
    "absolute": _cmp(C3),
    "index": _cmp(C3, compare="index", base_period="2015", focus="lu"),
    "share of the sum": _cmp(C3, compare="share"),
    "share of a reference": _cmp(C3 + [_series("eu", "EU", reporter="DE,FR")], compare="share", reference="eu"),
    "yoy of one series": _cmp([_series("fr", partner="FR")], compare="yoy"),
    "diff": _cmp(C3, compare="diff", reference="de"),
    "facet": _cmp(C3, facet=True),
    "months of two years": _cmp([_series("y2024", "2024", freq="M", start="2024-01", end="2024-12"),
                                 _series("y2025", "2025", freq="M", start="2025-01", end="2025-12")],
                                align="month", focus="y2025"),
    "balance": _cmp(FLOWS, balance=True),
    "two years by partner": _cmp([_snap("a", "2024"), _snap("b", "2025")], compare="diff", sort="delta", limit=8),
    "peers growth": _cmp([_snap("a", "2015", "/insights/compare", by="reporter", group="LU,DE,BE"),
                          _snap("b", "2025", "/insights/compare", by="reporter", group="LU,DE,BE")],
                         compare="yoy", focus="LU"),
    "two years, placeholders": _cmp([_snap("a", "$previous"), _snap("b", "$period")], compare="share"),
    "line with an index": _cmp(C3[:2], ptype="line", compare="index", base_period="2015"),
    "bar of two snapshots": _cmp([_snap("a", "2024"), _snap("b", "2025")], ptype="bar", compare="yoy", focus="FR"),
}


@pytest.mark.parametrize("case", sorted(GOOD_COMPARISONS))
def test_comparisons_validate(case, initial):
    env = _batch({"op": "upsert_panel", "panel": GOOD_COMPARISONS[case]})
    assert view.validate_ops(env, initial) == [], case


NEGATIVES_1_2 = {
    "unknown mode": (_cmp(C3, compare="per_capita"), f"{P0}/options/compare", "schema"),
    "seven series": (_cmp(C3 + [_series(k, reporter=k.upper()) for k in ("nl", "fr", "it", "es")]),
                     f"{P0}/queries", "schema"),
    "mixed endpoints": (_cmp([_series("lu"), _snap("b", "2025")]), f"{P0}/queries", "bad_queries"),
    "diff without reference": (_cmp(C3, compare="diff"), f"{P0}/options/reference", "required"),
    "reference not a query": (_cmp(C3, compare="diff", reference="fr"), f"{P0}/options/reference", "unknown_query"),
    "reference in index mode": (_cmp(C3, compare="index", reference="de"), f"{P0}/options/reference", "bad_param"),
    "focus not a query": (_cmp(C3, focus="LU"), f"{P0}/options/focus", "unknown_query"),
    "facet of one": (_cmp(C3[:1], facet=True), f"{P0}/options/facet", "bad_param"),
    "mixed freqs": (_cmp([C3[0], _series("m", freq="M", start="2015-01")]), f"{P0}/queries/1/params/freq",
                    "bad_param"),
    "align month on years": (_cmp(C3[:2], align="month"), f"{P0}/queries/0/params/freq", "bad_param"),
    "balance of two exports": (_cmp(C3[:2], balance=True), f"{P0}/queries", "bad_queries"),
    "balance as an index": (_cmp(FLOWS, balance=True, compare="index"), f"{P0}/options/balance", "bad_param"),
    "sort on series": (_cmp(C3, sort="delta"), f"{P0}/options/sort", "bad_param"),
    "snapshots on two endpoints": (_cmp([_snap("a", "2024"), _snap("b", "2025", "/insights/compare")]),
                                   f"{P0}/queries", "bad_queries"),
    "snapshots by different dims": (_cmp([_snap("a", "2024"), _snap("b", "2025", by="product")]),
                                    f"{P0}/queries", "bad_queries"),
    "the same snapshot twice": (_cmp([_snap("a", "2025"), _snap("b", "2025")]), f"{P0}/queries", "bad_queries"),
    "snapshots as an index": (_cmp([_snap("a", "2024"), _snap("b", "2025")], compare="index"),
                              f"{P0}/options/compare", "bad_param"),
    "facet on snapshots": (_cmp([_snap("a", "2024"), _snap("b", "2025")], facet=True), f"{P0}/options/facet",
                           "bad_param"),
    "snapshots on movers": (_cmp([_snap("a", "2024", "/insights/movers", previous="2023"),
                                  _snap("b", "2025", "/insights/movers", previous="2024")]),
                            f"{P0}/queries/0/endpoint", "bad_queries"),
    "line comparison off series": (_cmp([{"key": "t", "endpoint": "/insights/trend", "params": {}}], ptype="line",
                                        compare="index"), f"{P0}/queries/0/endpoint", "bad_queries"),
    "bar comparison without a and b": (_bar(options={"compare": "yoy"}), f"{P0}/queries", "bad_queries"),
    "bar compare index": (_cmp([_snap("a", "2024"), _snap("b", "2025")], ptype="bar", compare="index"),
                          f"{P0}/options/compare", "schema"),
}


@pytest.mark.parametrize("case", sorted(NEGATIVES_1_2))
def test_comparisons_are_checked(case, initial):
    panel_, path, code = NEGATIVES_1_2[case]
    errors = view.validate_ops(_batch({"op": "upsert_panel", "panel": panel_}), initial)
    assert path in paths(errors), f"{case}: {errors}"
    assert code in codes(errors), f"{case}: {errors}"


def test_marks_on_comparisons(initial):
    lines, pair = GOOD_COMPARISONS["index"], {**GOOD_COMPARISONS["two years by partner"], "id": "pair"}

    def run(*marks):
        return view.validate_ops(_batch({"op": "upsert_panel", "panel": lines},
                                        {"op": "upsert_panel", "panel": pair},
                                        *[{"op": "add_mark", "mark": m} for m in marks]), initial)

    ok = [{"kind": "highlight", "target": "extra", "note": "The pandemic.", "params": {"from": "2020", "to": "2021"}},
          {"kind": "event", "target": "extra", "note": "Lockdowns.", "params": {"at": "2020-03", "label": "COVID-19"}},
          {"kind": "annotation", "target": "extra", "series": "lu", "note": "Luxembourg.",
           "params": {"x": "2025", "label": "Luxembourg"}, "period": "2025"},
          {"kind": "highlight", "target": "pair", "note": "Neighbours.", "params": {"codes": ["FR", "DE"]}},
          {"kind": "annotation", "target": "pair", "note": "France.", "params": {"x": "FR", "label": "France"}}]
    assert run(*ok) == []
    bad = [({"kind": "highlight", "target": "extra", "note": "x", "params": {"codes": ["LU"]}}, "wrong_target"),
           ({"kind": "highlight", "target": "pair", "note": "x", "params": {"from": "2020", "to": "2021"}},
            "wrong_target"),
           ({"kind": "event", "target": "pair", "note": "x", "params": {"at": "2020", "label": "COVID-19"}},
            "wrong_target"),
           ({"kind": "annotation", "target": "extra", "note": "x", "params": {"x": "LU", "label": "LU"}},
            "bad_period"),
           ({"kind": "trend_break", "target": "extra", "note": "x", "params": {"at": "2009"}}, "wrong_target"),
           ({"kind": "annotation", "target": "extra", "series": "fr", "note": "x",
             "params": {"x": "2025", "label": "x"}}, "unknown_series")]
    for mark, code in bad:
        errors = run(mark)
        assert code in codes(errors), (mark, errors)
        assert all(e["path"].startswith("/ops/2/mark") for e in errors), errors


def test_compare_layout_and_is_comparison():
    assert view.validate.compare_layout(GOOD_COMPARISONS["index"]) == "series"
    assert view.validate.compare_layout(GOOD_COMPARISONS["two years by partner"]) == "pair"
    assert view.validate.compare_layout(_cmp([_series("lu"), _snap("b", "2025")])) is None
    assert view.validate.is_comparison(GOOD_COMPARISONS["line with an index"])
    assert not view.validate.is_comparison({"type": "line", "queries": [], "options": {"gap_fill": True}})
    assert not view.validate.is_comparison(_bar(options={"reference": 1}))


@pytest.mark.parametrize("name,base", [("09_comparisons.json", "initial.json"),
                                       ("09_comparisons.json", "spec_after_03.json"),
                                       ("09b_line_bar_comparisons.json", "initial.json")])
def test_comparison_examples_validate(name, base):
    assert view.validate_ops(load(name), load(base)) == []


def test_kit_builds_comparisons(initial):
    from agents.kit import COMPARE_MODES, compare_facts, compare_panel, series_key, series_query
    v = ViewClient("http://127.0.0.1:1", "visualizer")
    v.lookup_names = False
    ops = []
    ops += v.compare_partners(["DE", "FR", "BE"], mode="index", base_period="2015",
                              highlight=("2020", "2021"), note="The pandemic.")
    ops += v.compare_partners(["DE", "FR", "BE", "NL", "CN", "US"], flow="IMPORT", facet=True, id="facet")
    ops += v.compare_partners(["DE", "FR"], mode="diff", id="gap")
    ops += v.compare_partners(["FR"], mode="yoy", start="2016", id="yoy")
    ops += v.compare_products(["85", "84", "73"], mode="share", partner="FR")
    ops += v.compare_with_peers(["DE", "BE", "NL", "FR"])
    ops += v.compare_with_peers(["DE", "BE", "NL", "FR", "IE", "AT", "DK"], "imports", layout="bars", end="2025")
    ops += v.compare_years("2024", "2025", "partner", annotate=("FR", "France"), note="France.", period="2025")
    ops += v.compare_years(by="product", mode="diff", sort="delta", id="years_ctx")
    ops += v.compare_years("2024", "2025", "month")
    ops += v.compare_flows(partner="CN")
    env = v.envelope(ops, message="test", base_version=0)
    assert v.validate(env) == []
    assert view.validate_ops(env, initial) == []
    panels = {o["panel"]["id"]: o["panel"] for o in ops if o["op"] == "upsert_panel"}
    assert len(panels) == 11 and all(p["type"] == "compare" for p in panels.values())
    peers = panels["peers_lu_de_be_nl_fr"]
    assert peers["options"] == {"compare": "index", "base_period": "2015", "focus": "lu"}
    assert [q["params"]["reporter"] for q in peers["queries"]] == ["LU", "DE", "BE", "NL", "FR"]
    assert [q["label"] for q in peers["queries"]][:2] == ["Luxembourg", "Germany"]
    assert peers["facts"]["lu_change"] == {"op": "growth", "of": ["lu_now", "lu_then"], "format": "pct_signed"}
    assert panels["yoy"]["queries"][0]["params"]["start"] == "2015"  # a year before the first growth shown
    assert panels["gap"]["options"]["reference"] == "de"
    assert panels["years_ctx"]["queries"][0]["params"]["period"] == "$previous"
    assert panels["flows_cn"]["options"]["balance"] is True
    assert "balance" in panels["flows_cn"]["facts"]
    assert panels["compare_product_hs85_hs84_hs73"]["queries"][0]["label"] == "Electrical machinery"
    assert series_key("85") == "hs85" and series_key("2024") == "y2024" and series_key("DE") == "de"
    assert set(COMPARE_MODES) == {"absolute", "index", "share", "yoy", "diff"}
    assert compare_facts(["a"], "2015")["a_then"]["path"] == "[period=2015].value"
    assert compare_panel("x", "X", [series_query("a")], mode="index")["options"] == {"compare": "index"}
    with pytest.raises(ValueError):
        compare_panel("x", "X", [], mode="per_capita")
    with pytest.raises(ValueError):
        v.compare_years("$previous", "$period", "month")
    with pytest.raises(ValueError):
        v.compare_partners(["DE", "FR"], highlight=("2020", "2021"))  # a mark needs a note
    with pytest.raises(ValueError):
        v.compare_partners(["A%d" % i for i in range(7)])
    v.close()


# --------------------------------------------------------------------------- 1.3: forecast scenarios
def test_forecast_mark_may_draw_its_scenarios(initial):
    q = {"endpoint": "/insights/forecast", "params": {"flow": "EXPORT", "freq": "M", "horizon": 12, "aggregate": "A"}}

    def band(params):
        return _batch({"op": "add_mark", "mark": {"kind": "forecast", "target": "trend", "series": "exports",
                                                  "note": "If the smartphone line stops: {stops}.", "query": q,
                                                  "params": params,
                                                  "facts": {"stops": {"query": "self", "format": "eur",
                                                                      "path": "scenarios.[id=smartphones_stops]"
                                                                              ".rows.[period=2026].value"}}}},
                      agent="forecaster")
    assert view.validate_ops(band({"label": "2026 forecast", "scenarios": True}), initial) == []
    assert view.validate_ops(band({}), initial) == []
    assert "/ops/0/mark/params/scenarios" in paths(view.validate_ops(band({"scenarios": "yes"}), initial))
    assert ViewClient("visualizer").forecast_mark("trend", q, "x", scenarios=True)["mark"]["params"] == {"scenarios": True}
    assert "scenarios" not in ViewClient("visualizer").forecast_mark("trend", q, "x")["mark"]["params"]


# --------------------------------------------------------------------------- contract 1.5: a panel's own context
def test_panel_context_in_the_spec_and_the_op(initial):
    """A panel may carry its own {period?, start?, product?}; set_panel_context sets it, for what it follows."""
    spec = copy.deepcopy(initial)
    i = next(n for n, p in enumerate(spec["panels"]) if p["id"] == "peers")
    spec["panels"][i]["context"] = {"period": "2020", "product": "85"}
    assert view.validate_spec(spec) == []
    spec["panels"][i]["context"] = {"colour": "red"}
    assert f"/panels/{i}/context/colour" in paths(view.validate_spec(spec))
    spec["panels"][i]["context"] = {}
    assert codes(view.validate_spec(spec)) == {"schema"}       # an empty own context is no context: leave it out
    ok = [{"op": "set_panel_context", "id": "peers", "period": "2020", "product": "85"},
          {"op": "set_panel_context", "id": "mix_export", "period": "2019"},
          {"op": "set_panel_context", "id": "trend", "product": "85"},
          {"op": "set_panel_context", "id": "peers"},
          {"op": "set_panel_context", "id": "peers", "period": None, "product": "85"}]
    for op in ok:
        assert view.validate_ops(_batch(op, agent="user"), initial) == [], op
        assert view.validate_ops(_batch(op, agent="visualizer"), initial) == [], op
    bad = [({"op": "set_panel_context", "id": "nope", "period": "2020"}, "/ops/0/id", "unknown_panel"),
           ({"op": "set_panel_context", "id": "mix_export", "product": "85"}, "/ops/0/product", "no_placeholder"),
           ({"op": "set_panel_context", "id": "trend", "period": "2020"}, "/ops/0/period", "no_placeholder"),
           ({"op": "set_panel_context", "id": "peers", "period": "20"}, "/ops/0/period", "schema"),
           ({"op": "set_panel_context", "id": "peers", "flow": "EXPORT"}, "/ops/0/flow", "unexpected"),
           ({"op": "set_panel_context", "period": "2020"}, "/ops/0/id", "required")]
    for op, path, code in bad:
        errors = view.validate_ops(_batch(op, agent="user"), initial)
        assert path in paths(errors) and code in codes(errors), (op, errors)


def test_panel_context_ranges(initial):
    """start (From) only on a panel that follows $start, with a yearly period no earlier than it."""
    spec = copy.deepcopy(initial)
    ranged = next(p for p in spec["panels"] if p["id"] == "crossroads")
    for q in ranged["queries"]:
        q["params"].update({"start": "$start", "end": "$period"})
    assert view.validate_spec(spec) == []
    assert view.validate_ops(_batch({"op": "set_panel_context", "id": "crossroads", "start": "2015",
                                     "period": "2020"}, agent="user"), spec) == []
    for op, code in [({"start": "2021", "period": "2020"}, "bad_period"),
                     ({"start": "2015"}, "bad_period"),
                     ({"start": "2015", "period": "2020-03"}, "bad_period")]:
        errors = view.validate_ops(_batch({"op": "set_panel_context", "id": "crossroads", **op}, agent="user"), spec)
        assert "/ops/0/start" in paths(errors) and code in codes(errors), (op, errors)
    # the baseline crossroads follows no $start: no From for it
    errors = view.validate_ops(_batch({"op": "set_panel_context", "id": "crossroads", "start": "2015",
                                       "period": "2020"}, agent="user"), initial)
    assert "no_placeholder" in codes(errors)


def test_a_pinned_panel_keeps_its_context_from_agents(initial):
    spec = copy.deepcopy(initial)
    next(p for p in spec["panels"] if p["id"] == "peers")["pinned"] = True
    op = {"op": "set_panel_context", "id": "peers", "period": "2020"}
    assert "pinned" in codes(view.validate_ops(_batch(op), spec))
    assert view.validate_ops(_batch(op, agent="user"), spec) == []


def test_panel_context_fills_the_panels_queries_and_marks(initial):
    v = view.validate
    page = {"period": "2025", "product": "TOTAL", "start": "2015"}
    assert v.panel_context(page, None) == page
    assert v.panel_context(page, {"context": {"product": "85"}}) == {**page, "product": "85"}
    # a period of its own without a start of its own is a single year
    assert v.panel_context(page, {"context": {"period": "2020"}}) == {"period": "2020", "product": "TOTAL"}
    assert v.range_start(v.panel_context(page, {"context": {"period": "2020", "start": "2018"}})) == "2018"
    spec = copy.deepcopy(initial)
    spec["context"] = {"period": "2025", "product": "TOTAL"}
    next(p for p in spec["panels"] if p["id"] == "peers")["context"] = {"period": "2019", "product": "85"}
    mark = {"kind": "highlight", "target": "peers", "note": "Here.", "params": {"codes": ["LU"]},
            "query": {"endpoint": "/insights/ranking", "params": {"by": "reporter", "period": "$period",
                                                                 "product": "$product"}}}
    env = _batch({"op": "add_mark", "tmp_id": "h", "mark": mark},
                 {"op": "update_mark", "id": "h", "set": {"query": mark["query"]}},
                 {"op": "upsert_panel", "panel": _bar(queries=[{"key": "main", "endpoint": "/insights/ranking",
                                                               "params": {"by": "partner", "period": "$period"}}])},
                 {"op": "set_panel_context", "id": "extra", "period": "2018"})
    got = {ptr: v.query_url(q, ctx) for ptr, q, ctx in v.batch_query_contexts(env, spec)}
    assert got["/ops/0/mark/query"] == "/insights/ranking?by=reporter&period=2019&product=85"
    assert got["/ops/1/set/query"] == "/insights/ranking?by=reporter&period=2019&product=85"
    # the new panel is filled with the context it has after the batch
    assert got["/ops/2/panel/queries/0"] == "/insights/ranking?by=partner&period=2018"
    # set_context in the batch moves the page, not the panel with its own year
    env = _batch({"op": "set_context", "period": "2020"}, {"op": "add_mark", "mark": mark})
    assert [v.query_url(q, c) for _, q, c in v.batch_query_contexts(env, spec)] == \
        ["/insights/ranking?by=reporter&period=2019&product=85"]


def test_no_figures_in_agent_titles_and_labels(initial):
    """The page refuses figures an agent types in a title, subtitle, mark label or the page title; years are fine."""
    for title in ("Exports 1999–2025", "Where exports went in 2025", "Top 10 partners, 2015 to 2025",
                  "85 Electrical machinery", "Imports by partner, nominal euros"):
        assert view.validate_ops(_batch({"op": "upsert_panel", "panel": _bar(title=title, subtitle=title)}),
                                 initial) == [], title
        assert view.validate_ops(_batch({"op": "set_title", "title": title}), initial) == [], title
    bad = [({"op": "upsert_panel", "panel": _bar(title="Exports rose 12%")}, "/ops/0/panel/title"),
           ({"op": "upsert_panel", "panel": _bar(subtitle="€5bn of machinery")}, "/ops/0/panel/subtitle"),
           ({"op": "set_title", "title": "A 3 billion euro gap"}, "/ops/0/title"),
           ({"op": "add_mark", "mark": {"kind": "highlight", "target": "mix_export", "note": "Look.",
                                        "params": {"codes": ["85"], "label": "Up 40 per cent"}}},
            "/ops/0/mark/params/label")]
    for op, path in bad:
        errors = view.validate_ops(_batch(op), initial)
        assert path in paths(errors) and "figure_in_text" in codes(errors), (op, errors)
        # the person's own ops are not the assistant's words
        assert "figure_in_text" not in codes(view.validate_ops(_batch(op, agent="user"), initial)), op


def test_kit_builds_set_panel_context(initial):
    vc = ViewClient("http://localhost:0", "visualizer")
    op = vc.set_panel_context("peers", period="2020", product="85")
    assert op == {"op": "set_panel_context", "id": "peers", "period": "2020", "product": "85"}
    assert vc.set_panel_context("peers") == {"op": "set_panel_context", "id": "peers"}
    assert vc.validate(vc.envelope([op])) == []
    assert view.validate_ops(vc.envelope([op]), initial) == []


# --------------------------------------------------------------------------- contract 1.7: growth per year since a year
def _growth_bar(**over):
    from agents.kit import growth_bar_panel
    p = growth_bar_panel("growth_partners", "Fastest-growing export partners since 1999", by="partner",
                         start="1999", flow="EXPORT", limit=8)
    for k, v in over.items():
        if k == "params":
            p["queries"][0]["params"].update(v)
        else:
            p[k] = v
    return p


def test_growth_bar_validates(initial):
    """A bar of growth per year since a year (1.7) reads /insights/trends; the kit builds it."""
    p = _growth_bar()
    assert p["options"]["measure"] == "growth" and p["queries"][0]["endpoint"] == "/insights/trends"
    assert view.validate_ops(_batch({"op": "upsert_panel", "panel": p}), initial) == []
    vc = ViewClient("http://localhost:0", "trend_finder")
    env = vc.envelope([vc.upsert_panel(p), vc.highlight_mark(p["id"], "China grew fastest.", codes=["CN"])])
    assert vc.validate(env) == []
    assert view.validate_ops(env, initial) == []
    products = _growth_bar(params={"by": "product", "level": 1, "end": "2025"})
    assert view.validate_ops(_batch({"op": "upsert_panel", "panel": products}), initial) == []


GROWTH_NEGATIVES = {
    "no start": (_growth_bar(queries=[{"key": "main", "endpoint": "/insights/trends",
                                       "params": {"by": "partner", "flow": "EXPORT"}}]),
                 f"{P0}/queries/0/params/start", "missing_param"),
    "one year's ranking": (_growth_bar(queries=[_q("/insights/ranking/records", by="partner", period="2025")]),
                           f"{P0}/queries", "bad_queries"),
    "by reporter": (_growth_bar(params={"by": "reporter"}), f"{P0}/queries/0/params/by", "bad_param"),
    "slope": (_growth_bar(params={"method": "slope"}), f"{P0}/queries/0/params/method", "bad_param"),
    "start after end": (_growth_bar(params={"start": "2025", "end": "2015"}), f"{P0}/queries/0/params/start",
                        "bad_period"),
    "comparison option": (_growth_bar(options={"measure": "growth", "sort": "delta"}), f"{P0}/options/sort",
                          "bad_param"),
}


@pytest.mark.parametrize("case", sorted(GROWTH_NEGATIVES))
def test_growth_bar_is_checked(case, initial):
    panel_, path, code = GROWTH_NEGATIVES[case]
    errors = view.validate_ops(_batch({"op": "upsert_panel", "panel": panel_}), initial)
    assert path in paths(errors) and code in codes(errors), f"{case}: {errors}"


def test_bar_measure_is_growth_only(initial):
    """The schema knows one bar measure; a plain bar (no measure) is as before."""
    errors = view.validate_ops(_batch({"op": "upsert_panel", "panel": _bar(options={"measure": "share"})}), initial)
    assert "schema" in codes(errors)
    assert view.validate_ops(_batch({"op": "upsert_panel", "panel": _bar(options={"limit": 8})}), initial) == []
