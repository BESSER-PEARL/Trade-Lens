# SPDX-License-Identifier: MIT
"""The real agent (app/agents/lux_insights), without a network and without an LLM.

- /insights and /trends answers come from app/agents/lux_insights/testdata/insights.json, recorded from a
  live backend with ``python tests/test_lux_insights_agent.py --record http://localhost:8100``.
- The page is the kit's in-memory store (agents/kit/testing.py), which validates every batch with the
  backend's own validator and pre-flights every query, as POST /view/ops does.
- The LLM is scripted: the router's plan, and each specialist's tool calls and final text.

What is checked: routing to each specialist (LLM plan, keyword fallback, the owner of a cited number);
every act tool builds ops that the contract accepts, in the agent's own pen, without duplicates; a 422 and
a 409 reach the model as data; the reply checks (title, cited numbers, no figure the tools did not show);
the registration payloads; the settings never leak the key; and, when .venv-agent exists, that the BAF
agent builds with its states and transitions.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

pytest.importorskip("jsonschema")
pytest.importorskip("httpx")

REPO = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO / "app" / "agents"
for p in (str(REPO), str(AGENTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents.kit.testing import LocalView, StubViewClient  # noqa: E402
from agents.kit import AGENTS, InsightsClient, InsightsError, ViewError, source_url  # noqa: E402
from lux_insights import fmt, page, registry, router, settings  # noqa: E402
from lux_insights.conversation import Conversation, mode_change  # noqa: E402
from lux_insights.specialists import (cite_numbers, clean_citations, run_specialist,  # noqa: E402
                                      unverified_figures, with_title)
from lux_insights.tools import ROLE_TOOLS, ROLES, Toolbox, contract, pick_facts  # noqa: E402

TESTDATA = AGENTS_DIR / "lux_insights" / "testdata" / "insights.json"


# --------------------------------------------------------------------------- recorded API
class ReplayInsights(InsightsClient):
    """Answers from the recording; an unknown URL is a 404, as an unknown code would be."""

    def __init__(self, responses: dict):
        self.base_url, self.responses, self.calls = "replay:", responses, []

    def close(self) -> None:
        pass

    def get(self, endpoint: str, **params):
        url = source_url(endpoint, params)
        self.calls.append(url)
        if url not in self.responses:
            raise InsightsError(404, "not in the recording", url)
        return copy.deepcopy(self.responses[url]), url


class RecordingInsights(InsightsClient):
    def __init__(self, base_url: str):
        super().__init__(base_url)
        self.responses: dict[str, Any] = {}

    def get(self, endpoint: str, **params):
        data, url = super().get(endpoint, **params)
        self.responses[url] = copy.deepcopy(data)
        return data, url


def make_boxes(api: InsightsClient, store: LocalView) -> dict[str, Toolbox]:
    boxes = {}
    for role in ROLES:
        box = Toolbox("replay:", role, api=api, view=StubViewClient(store, role))
        box.view.lookup_names = False   # comparisons name codes without asking the backend
        box._meta = {}                 # validate with the default dimensions
        boxes[role] = box
    return boxes


# --------------------------------------------------------------------------- the scenarios (also recorded)
def call(box: Toolbox, name: str, **args) -> dict:
    """Call a tool the way the model does: through its schema, getting JSON text back."""
    tool = next(t for t in box.tools([name]))
    out = tool.call(args)
    return json.loads(out) if out.startswith(("{", "[")) else {"text": out}


def scenario_trend(boxes) -> dict:
    tf = boxes["trend_finder"]
    tf.begin()
    out = {"drill": call(tf, "drill_down", flow="EXPORT", period="2025", previous="2024")}
    leaf = out["drill"]["path"][-1]["code"]
    buyer, supplier = out["drill"]["sold_to"][0]["code"], out["drill"]["bought_from"][0]["code"]
    out["title"] = call(tf, "set_title", title="Luxembourg's trade in goods: where the 2025 export rise came from")
    out["headline"] = call(tf, "write_headline", flow="EXPORT", period="2025", previous="2024", product=leaf,
                           partner=buyer, text="Exports rose by {change} in 2025, {growth}. One line went from "
                                               "{line_before} to {line_now}, {line_share} of the change; without it "
                                               "exports changed by {growth_without}.")
    out["thread"] = call(tf, "add_thread", in_code=supplier, out_code=buyer, product=leaf, storyline="rise_2025",
                         label="Smartphones through Luxembourg", note="{bought} bought from there, {sold} sold on.")
    out["thread_again"] = call(tf, "add_thread", in_code=supplier, out_code=buyer, product=leaf, storyline="rise_2025",
                               label="Smartphones through Luxembourg", note="{bought} bought from there, {sold} sold on.")
    out["waterfall"] = call(tf, "show_panel", type="waterfall", title="What changed in exports, 2024 to 2025",
                            flow="EXPORT", period="2025", previous="2024")
    out["annotation"] = call(tf, "add_annotation", target=out["waterfall"].get("panel", "x"), x="85",
                             label="Electrical machinery", note="The biggest step: {value}.", storyline="rise_2025")
    out["analysis"] = call(tf, "analyze_trend", flow="EXPORT")
    out["breaks"] = call(tf, "add_trend_breaks", flow="EXPORT", breaks=[
        {"at": "2009-01", "label": "Step down, 2009", "note": "A typical month went from {before} to {after}."},
        {"at": "2021-12", "label": "Step up, 2021", "note": "A step of {change}."}])
    return out


def scenario_cause(boxes) -> dict:
    cf = boxes["cause_finder"]
    cf.begin()
    return {"events": call(cf, "get_events", start="1999"),
            "drawn": call(cf, "add_events", events=[
                {"event_id": "us-steel-tariffs-2018", "label": "US steel tariffs",
                 "note": "US tariffs on steel; those exports to the US moved {line_change} that year."},
                {"at": "2020-03", "label": "COVID-19", "note": "Lockdowns; exports {exp_change} that year."}]),
            "invented": call(cf, "add_events", events=[
                {"at": "2009-01", "label": "Financial crisis", "event_id": "gfc-2009",
                 "note": "The financial crisis; exports {exp_change} that year."}])}


def scenario_forecast(boxes) -> dict:
    fc = boxes["forecaster"]
    fc.begin()
    out = {"forecast": call(fc, "get_forecast", flow="EXPORT"),
           "band": call(fc, "add_forecast_band", flow="EXPORT",
                        note="Exports in 2026: {value}, between {low} and {high}; off by {error} when tested. "
                             "If the smartphone line stops: {stops}.",
                        outlook="Exports should land between {low} and {high}, against {last}. The method is {method}.")}
    out["band_marks"] = [dict(m) for m in fc.view.spec().get("marks", [])]
    out["removed"] = call(fc, "remove_marks", kind="forecast")
    return out


VISUALS = [
    dict(type="choropleth", title="Where the goods go", flow="EXPORT", measure="share"),
    dict(type="flowmap", title="The routes through Luxembourg"),
    dict(type="heatmap", title="Exports month by month", kind="month_year", flow="EXPORT"),
    dict(type="heatmap", title="The typical year", kind="seasonal", flow="EXPORT"),
    dict(type="heatmap", title="Chapters by year", kind="product_year", flow="EXPORT", start="2015"),
    dict(type="treemap", title="What Luxembourg sells", flow="EXPORT"),
    dict(type="butterfly", title="Bought and sold"),
    dict(type="bump", title="Who buys most", flow="EXPORT"),
    dict(type="scatter", title="Strong and growing", flow="EXPORT"),
    dict(type="line", title="Trade with Germany", partner="DE", flow="BOTH"),
    dict(type="bar", title="Main buyers", by="partner", flow="EXPORT"),
    dict(type="bar", title="Among the 27", by="reporter", flow="EXPORT"),
    dict(type="line", title="Luxembourg and Belgium", reporters=["LU", "BE"], flow="EXPORT"),
]


def scenario_visual(boxes) -> dict:
    vz = boxes["visualizer"]
    vz.begin()
    out = {"panels": [call(vz, "show_panel", **v) for v in VISUALS]}
    out["highlight"] = call(vz, "highlight", target="viz_map_exports", codes=["DE", "FR"],
                            note="The two largest buyers: {de_share} and {fr_share}.", label="Largest buyers")
    out["compare"] = call(vz, "compare", what="peers", codes=["BE"], mode="index", base_period="2015")
    out["context"] = call(vz, "set_context", period="2020")
    vz.begin("Clear the page")
    out["clear"] = call(vz, "clear_page")
    return out


def scenario_review(boxes) -> dict:
    """The review's findings on the real agent: each call that went wrong, and what it does now."""
    out: dict = {}
    vz = boxes["visualizer"]
    vz.begin()
    out["context"] = call(vz, "set_context", period="2020")
    out["crossroads"] = call(vz, "highlight", target="crossroads", codes=["BE", "DE", "FR"],
                             note="Bought from Belgium {be_bought_share}, Germany {de_bought_share}; "
                                  "sold to Germany {de_sold_share}.")
    out["crossroads_value"] = call(vz, "highlight", target="crossroads", codes=["BE"], note="Belgium {be_share}.")
    out["back"] = call(vz, "set_context", period="2025")
    out["flows_cn"] = call(vz, "compare", what="flows", partner="CN", period_a="2024", period_b="2025",
                           text="Exports to China {exports_yoy} and imports from China {imports_yoy} on the year; "
                                "{exports_change} for exports since 2015.")
    out["flows_wrong_verb"] = call(vz, "compare", what="flows", partner="CN",
                                   text="Imports fell by {imports_change}.")   # they rose since 1999
    out["years"] = call(vz, "compare", what="years", period_a="2024", period_b="2025", by="partner",
                        highlight_codes=["FR"], note="France: {fr_a} to {fr_b}, {fr_diff}.",
                        text="Exports {a_total} then {b_total}, {change}.")
    out["treemap"] = call(vz, "show_panel", type="treemap", title="What Luxembourg sells", id="goods_exports",
                          flow="EXPORT")
    out["treemap_mark"] = call(vz, "highlight", target="goods_exports", codes=["84"],
                               note="Machinery: {hs84_value}, {hs84_share} of exports.")
    out["keep"] = call(vz, "keep_marks", numbers=[1])
    cf = boxes["cause_finder"]
    cf.begin()
    out["steel_total"] = call(cf, "add_events", events=[
        {"event_id": "us-steel-232-2025", "note": "Exports of iron and steel fell by {exp_change}."}])
    out["steel_caused"] = call(cf, "add_events", events=[
        {"event_id": "covid-19-2020", "note": "The pandemic caused a fall: exports {exp_change}."}])
    out["steel_with_total"] = call(cf, "add_events", events=[
        {"event_id": "us-steel-232-2025", "note": "Steel exports to the US moved {line_change}; all exports {exp_change}."}])
    out["steel_line"] = call(cf, "add_events", events=[
        {"event_id": "us-steel-232-2025", "product": "72", "flow": "EXPORT",
         "note": "Iron and steel exports moved {line_diff} ({line_change})."}])
    out["steel_scope"] = call(cf, "add_events", storyline="scoped", events=[
        {"event_id": "us-steel-232-2025", "label": "US steel tariffs 2025",
         "note": "Steel and aluminium exports to the US moved {line_change}."},
        {"event_id": "eu-uk-tca-2021", "note": "Exports to the UK moved {line_exp_change}, imports {line_imp_change}."},
        {"event_id": "russia-sanctions-2022", "note": "Exports to Russia moved {line_change}."}])
    out["scoped_events"] = call(cf, "get_events", start="2021")
    out["scoped_record"] = list(cf.turn.scoped)
    vz.begin("Compare Luxembourg with Belgium and the Netherlands since 1999")
    out["peers_1999"] = call(vz, "compare", what="peers", codes=["BE", "NL"], mode="index", start="2015",
                             base_period="2015", title="Luxembourg, Belgium and the Netherlands since 2015",
                             text="Exports since 2015: Luxembourg {lu_change}, Belgium {be_change}.")
    vz.begin("Compare Luxembourg with Belgium")
    out["peers_default"] = call(vz, "compare", what="peers", codes=["BE"], mode="index")
    vz.begin("Where did the goods go in 2024?")
    out["map"] = call(vz, "show_panel", type="choropleth", title="Where the goods go", id="map_exports",
                      flow="EXPORT", period="2024")
    out["map_mark"] = call(vz, "highlight", target="map_exports", codes=["DE"], note="Germany {de_share}.")
    tf = boxes["trend_finder"]
    tf.begin()
    tf.analyze_trend("EXPORT")
    out["breaks_on_trend"] = call(tf, "add_trend_breaks", flow="EXPORT", target="trend", breaks=[
        {"at": "2009-01", "label": "Step down, 2009", "note": "From {before} to {after}."}])
    fc = boxes["forecaster"]
    fc.begin()
    out["skill_wrong"] = call(fc, "add_forecast_band", flow="EXPORT", note="{value}; beats the naive forecast by "
                                                                           "{skill_3m} at 1 month.")
    out["skill_right"] = call(fc, "add_forecast_band", flow="EXPORT", note="{value}; beats the naive forecast by "
                                                                           "{skill_1m} at 1 month and {skill_6m} at 6 "
                                                                           "months.")
    return out


def scenario_round_b(boxes) -> dict:
    """Fix round B (review): what each specialist may and may not do, in code."""
    out: dict = {}
    vz = boxes["visualizer"]
    vz.begin("Show me 2025")
    out["show_year_clear"] = call(vz, "clear_page")
    out["show_year_panel"] = call(vz, "show_panel", type="treemap", title="What Luxembourg sells in 2025")
    vz.begin("Show the top 10 export partners as a table")
    out["table"] = call(vz, "show_panel", type="bar", title="Top 10 export partners in 2025", by="partner",
                        flow="EXPORT", period="2025", max_items=10)
    out["table_mark"] = call(vz, "highlight", target=out["table"].get("panel", "x"), codes=["DE"],
                             note="Germany {de_value}, {de_share} of exports.")
    vz.begin("Where do the goods go?")
    out["map"] = call(vz, "show_panel", type="choropleth", title="Export destinations in 2025", id="map_exports",
                      flow="EXPORT", period="2025")
    out["routes"] = call(vz, "show_panel", type="flowmap", title="Export routes in 2025", id="routes",
                         flow="EXPORT", period="2025")
    vz.begin("Show the year-on-year growth of exports to France and Germany since 2010")
    out["yoy_line"] = call(vz, "show_panel", type="line", title="Exports to France", partner="FR", flow="EXPORT")
    out["yoy"] = call(vz, "compare", what="partners", codes=["FR"])
    vz.begin("Bought vs sold, partner by partner")
    out["two_ways_compare"] = call(vz, "compare", what="flows")
    out["two_ways"] = call(vz, "show_panel", type="butterfly", title="Bought and sold, partner by partner",
                           id="two_ways_partner", by="partner")
    out["two_ways_mark"] = call(vz, "highlight", target="two_ways_partner", codes=["BE"],
                                note="{be_bought} bought from Belgium, {be_sold} sold to it.")
    vz.begin("Show the gap between exports and imports")
    out["gap"] = call(vz, "compare", what="flows", text="The balance in the last year: {balance}.")
    vz.begin("Exports month by month since 1999")
    out["heatmap"] = call(vz, "show_panel", type="heatmap", title="Exports month by month", id="exports_heat",
                          kind="month_year", flow="EXPORT")
    out["heatmap_analysis"] = call(vz, "analyze_trend", flow="EXPORT")
    out["heatmap_breaks"] = call(vz, "add_trend_breaks", flow="EXPORT", target="exports_heat", breaks=[
        {"at": "2009-01", "label": "Step down, 2009", "note": "From {before} to {after}."}])
    out["seasonal"] = call(vz, "show_panel", type="heatmap", title="The typical year", id="seasons",
                           kind="seasonal", flow="EXPORT")
    out["seasonal_breaks"] = call(vz, "add_trend_breaks", flow="EXPORT", target="seasons", breaks=[
        {"at": "2009-01", "label": "Step down, 2009", "note": "From {before} to {after}."}])
    tf = boxes["trend_finder"]
    tf.begin("Why did exports jump in 2025?")
    out["drill"] = call(tf, "drill_down", flow="EXPORT", period="2025", previous="2024")
    leaf = out["drill"]["path"][-1]["code"]
    out["thread"] = call(tf, "add_thread", in_code="CN", out_code="FR", product=leaf, label="Smartphones, China to "
                         "France", note="making up {bought_share} of imports from China and {sold_share} of exports "
                                        "to France")
    out["headline_base"] = call(tf, "write_headline", flow="EXPORT", period="2025", previous="2024", product=leaf,
                                partner="FR", text="France took {partner_share} of exports.")
    long_text = ("Exports rose from {total_before} to {total_now}, a {growth} increase. Smartphones for wireless "
                 "networks contributed {line_change} to this change, accounting for {line_share} of the total "
                 "increase. Without this line, growth would have been {growth_without}.")
    out["headline_long"] = call(tf, "write_headline", flow="EXPORT", period="2025", previous="2024", product=leaf,
                                text=long_text)
    out["headline_long_again"] = call(tf, "write_headline", flow="EXPORT", period="2025", previous="2024",
                                      product=leaf, text=long_text)
    out["title_long"] = call(tf, "set_title", title="Luxembourg's trade in goods: " + "where the export rise came "
                                                    "from, " * 5)
    out["shares"] = list(tf.turn.shares)
    tf.analyze_trend("IMPORT")
    cf = boxes["cause_finder"]
    cf.begin("Did any events line up with the breaks in the series?")
    out["events"] = call(cf, "add_events", events=[
        {"event_id": "covid-19-2020", "note": "Exports moved {exp_change}, imports {imp_change}."},
        {"event_id": "eu-uk-tca-2021", "note": "Exports to the UK moved {line_exp_change}."}])
    out["event_breaks"] = list(cf.turn.event_breaks)
    fc = boxes["forecaster"]
    fc.begin("Which forecasting method works best here?")
    out["backtest"] = call(fc, "get_backtest", flow="EXPORT")
    out["band"] = call(fc, "add_forecast_band", flow="EXPORT", note="Exports in 2026: {value}.",
                       outlook="{method}. {summary}. 12 months ahead it was off by {error_12m}.")
    out["outlook"] = copy.deepcopy(page.panel(fc.view.spec(), "outlook"))
    return out


def record(base_url: str) -> None:
    api = RecordingInsights(base_url)
    store = LocalView(insights=api)
    boxes = make_boxes(api, store)
    for role in ROLES:
        boxes[role]._meta = None
    api.meta()
    for scenario in (scenario_trend, scenario_cause, scenario_forecast, scenario_visual, scenario_review,
                     scenario_round_b):
        print(scenario.__name__, json.dumps(scenario(boxes), ensure_ascii=False)[:300])
    TESTDATA.parent.mkdir(parents=True, exist_ok=True)
    TESTDATA.write_text(json.dumps(api.responses, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(api.responses)} responses in {TESTDATA}")


# --------------------------------------------------------------------------- fixtures
needs_recording = pytest.mark.skipif(not TESTDATA.exists(), reason="no recording; run with --record")


@pytest.fixture
def world():
    api = ReplayInsights(json.loads(TESTDATA.read_text(encoding="utf-8")))
    store = LocalView(insights=api)
    return api, store, make_boxes(api, store)


# --------------------------------------------------------------------------- scripted LLM
@dataclass
class Call:
    id: str
    name: str
    arguments: dict


@dataclass
class Resp:
    text: Optional[str] = None
    tool_calls: Optional[list] = None


@dataclass
class ScriptedLLM:
    """BAF's LLM interface (predict, predict_with_tools), answering from scripts."""
    plans: dict = field(default_factory=dict)       # question -> plan JSON (or an Exception to raise)
    scripts: dict = field(default_factory=dict)     # role -> list of steps: [(tool, args), ...] or final text
    offered: dict = field(default_factory=dict)     # role -> tool names it was offered
    prompts: dict = field(default_factory=dict)     # role -> system prompts seen

    def predict(self, message, parameters=None, session=None, system_message=None):
        plan = self.plans.get(message, '{"plan": []}')
        if isinstance(plan, Exception):
            raise plan
        return plan

    def predict_with_tools(self, messages, tools, parameters=None, system_message=None):
        role = next(r for r, a in {**{k: v["title"] for k, v in AGENTS.items()}, "general": "LuxInsights"}.items()
                    if f"You are the {a} of LuxInsights" in system_message)
        self.offered[role] = {t["function"]["name"] for t in tools}
        self.prompts.setdefault(role, []).append(system_message)
        step = self.scripts[role].pop(0)
        if isinstance(step, str):
            return Resp(text=step)
        return Resp(tool_calls=[Call(f"c{i}", name, args) for i, (name, args) in enumerate(step)])


# --------------------------------------------------------------------------- routing
def test_plan_parsing_keeps_known_agents_in_order():
    plan = router.parse_plan('{"plan": [{"agent": "forecaster", "task": "b"}, {"agent": "nobody"}, '
                             '{"agent": "trend_finder", "task": "a"}, {"agent": "visualizer"}]}')
    assert plan.agents == ["trend_finder", "forecaster", "visualizer"]
    assert router.parse_plan("not json") is None
    assert router.parse_plan('{"plan": []}') is None
    assert router.parse_plan('{"plan": ["general", "visualizer"]}').agents == ["visualizer"]


@pytest.mark.parametrize("question, plan, expected", [
    ("Why did exports jump in 2025?", ["trend_finder"], ["trend_finder"]),
    ("Which events line up with the changes since 1999?", ["trend_finder", "cause_finder"],
     ["trend_finder", "cause_finder"]),
    ("What do we expect for exports next year?", ["forecaster"], ["forecaster"]),
    ("Show me where the goods go on a map", ["visualizer"], ["visualizer"]),
    ("Why did exports jump in 2025, and what comes next?", ["forecaster", "trend_finder", "cause_finder"],
     ["trend_finder", "cause_finder", "forecaster"]),
    ("Hello", ["general"], ["general"]),
])
def test_router_follows_the_llm_plan(question, plan, expected):
    llm = ScriptedLLM(plans={question: json.dumps({"plan": [{"agent": a, "task": "t"} for a in plan]})})
    assert router.route(question, llm).agents == expected


@pytest.mark.parametrize("question, expected", [   # frequent questions are planned in code (common_plan)
    ("Why did exports jump so much in 2025?", "trend_finder"),
    ("Did COVID-19 change trade?", "cause_finder"),
    ("What should we expect for exports next year?", "forecaster"),
    ("Where do the goods go, on a map?", "visualizer"),
])
def test_router_falls_back_to_keywords(question, expected):
    llm = ScriptedLLM(plans={question: RuntimeError("no network")})
    plan = router.route(question, llm)
    assert plan.source == "keywords" and expected in plan.agents


def test_a_cited_number_goes_to_the_agent_who_drew_it():
    spec = {"marks": [{"id": "m1", "n": 1, "agent": "trend_finder", "kind": "thread"},
                      {"id": "m2", "n": 2, "agent": "forecaster", "kind": "forecast"}]}
    llm = ScriptedLLM(plans={})  # never asked
    assert router.route("Tell me more about [2]", llm, spec=spec).agents == ["forecaster"]
    assert router.route("Explain mark 1", llm, spec=spec).agents == ["trend_finder"]


def test_keep_and_remove_by_number_are_done_at_once_as_the_user():
    for q, task in (("keep 1", "keep:1"), ("Keep [1] and [3]", "keep:1,3"), ("remove 2", "remove:2"),
                    ("Remove mark 1", "remove:1")):
        plan = router.route(q, ScriptedLLM(plans={}))
        assert plan.source == "direct" and plan.steps[0].task == task, q
    assert router.direct_plan("Remove the forecast") is None and router.direct_plan("Keep going with 2025") is None


# --------------------------------------------------------------------------- act tools
def _all_marks(store):
    return store.spec["marks"]


@needs_recording
def test_trend_finder_tools_draw_valid_marks_in_its_pen(world):
    api, store, boxes = world
    out = scenario_trend(boxes)
    for key in ("title", "headline", "thread", "waterfall", "annotation", "breaks"):
        assert out[key]["ok"], (key, out[key])
    assert out["thread_again"]["changed"] is False and out["thread_again"]["marks"][0]["already_there"]
    assert out["thread"]["marks"][0]["number"] == 1
    assert out["annotation"]["marks"][0]["number"] == 1           # same storyline as the thread
    assert {m["number"] for m in out["breaks"]["marks"]} == {2}   # breaks share one number
    figures = out["headline"]["figures_on_page"]["panel headline"]
    assert figures["growth"].startswith("+") and "€" in figures["line_now"]
    kinds = sorted(m["kind"] for m in _all_marks(store))
    assert kinds == ["annotation", "thread", "trend_break", "trend_break"]
    # a note on the 2025 waterfall is about 2025 (dimmed on another year); trend breaks are about no one year
    by_kind = {m["kind"]: m for m in _all_marks(store)}
    assert by_kind["annotation"]["period"] == "2025" and by_kind["thread"]["period"] == "2025"
    assert "period" not in by_kind["trend_break"]
    assert {m["agent"] for m in _all_marks(store)} == {"trend_finder"}
    assert store.spec["title"].startswith("Luxembourg's trade in goods: where")
    assert contract().validate_spec(store.spec) == []
    assert any(p["type"] == "heatmap" for p in store.spec["panels"])   # the breaks got their heatmap


@needs_recording
def test_cause_finder_events_say_dates_are_not_proof(world):
    api, store, boxes = world
    out = scenario_cause(boxes)
    assert out["drawn"]["ok"], out["drawn"]
    events = [m for m in _all_marks(store) if m["kind"] == "event"]
    assert len(events) == 2 and all(m["agent"] == "cause_finder" for m in events)
    assert all("not proof of cause" in m["note"] for m in events)
    # on the trend line if it reaches back to 2009, else on a line since 1999 that add_events adds
    (target,) = {m["target"] for m in events}
    starts = [q["params"].get("start") for q in page.panel(store.spec, target)["queries"]]
    assert target in ("trend", "history") and min(s for s in starts if s) <= "2018"
    assert out["invented"]["ok"] is False and "curated list" in out["invented"]["error"]
    assert contract().validate_spec(store.spec) == []


@needs_recording
def test_forecaster_band_outlook_and_removal(world):
    api, store, boxes = world
    out = scenario_forecast(boxes)
    assert out["band"]["ok"], out["band"]
    bt = out["forecast"]["backtest"]
    assert bt["two_tests"].startswith("Tested at the ") and "summary" not in bt   # the method is named once
    assert bt["last_full_year"]["beat_naive"] in (True, False)
    assert out["forecast"]["methods_tested"] and any(m["chosen"] for m in out["forecast"]["methods_tested"])
    assert {s["id"] for s in out["forecast"]["scenarios"]} == {"smartphones_stops", "smartphones_continues"}
    band = next(m for m in out["band_marks"] if m["kind"] == "forecast")
    assert band["params"].get("scenarios") is True and "stops" in band["facts"]
    assert "period" not in band          # a forecast is never dimmed as "about another year"
    assert "method" not in band["query"]["params"]   # the backend's default, the backtest's winner
    assert out["removed"]["ok"] and out["removed"]["removed_numbers"] == [1]
    assert not _all_marks(store)
    assert not any(p["id"].startswith("outlook") for p in store.spec["panels"])   # the outlook went with it


@needs_recording
def test_visualizer_shows_every_chart_type(world, monkeypatch):
    monkeypatch.setenv("VIEW_MAX_AGENT_PANELS", "50")   # many charts in a row: no replacement here
    api, store, boxes = world
    out = scenario_visual(boxes)
    for v, result in zip(VISUALS, out["panels"]):
        assert result["ok"], (v, result)
        assert result["shows"], v
    types = {p["type"] for p in store.spec["panels"]}
    assert out["highlight"]["ok"], out["highlight"]
    assert out["context"]["ok"] and out["context"]["context_now"]["period"] == "2020"
    assert out["clear"]["ok"]
    assert not _all_marks(store) and store.spec["title"] == contract().load_baseline()["title"]
    if out["compare"].get("ok") is not None and "not available" not in str(out["compare"]):
        assert out["compare"]["ok"], out["compare"]
    assert len(out["panels"]) == len(VISUALS) and types  # every type went through the validator


@needs_recording
def test_the_review_findings_on_the_real_agent(world, monkeypatch):
    monkeypatch.setenv("VIEW_MAX_AGENT_PANELS", "50")   # many charts in a row: no replacement here
    api, store, boxes = world
    out = scenario_review(boxes)
    marks = {m["id"]: m for m in _all_marks(store)}
    # 1. the crossroads has two sides: a code's import and export shares, in the year the page shows
    assert out["crossroads"]["ok"], out["crossroads"]
    figs = out["crossroads"]["figures_on_page"]
    (shown,) = figs.values()
    # /insights/flows?period=2020: Belgium 33.9% of imports, Germany 27.3%
    assert shown["be_bought_share"] == "33.9%" and shown["de_bought_share"] == "27.3%"
    assert shown["de_sold_share"] != shown["de_bought_share"]
    cross = next(m for m in marks.values() if m["target"] == "crossroads")
    assert cross.get("period") == "2020"          # steps back when the page moves on
    assert out["crossroads_value"]["ok"] is False and "{be_bought_share}" in out["crossroads_value"]["error"]
    # 3. a span change is not a change on the year; a verb that contradicts its sign is refused
    assert out["flows_cn"]["ok"], out["flows_cn"]
    meaning = out["flows_cn"]["facts_available"]
    assert meaning["exports_yoy"] == "growth 2024 to 2025" and "1999 to 2025" in meaning["exports_change"]
    assert "since 1999" in page.panel(store.spec, out["flows_cn"]["panel"])["text"]   # the caption follows the chart
    assert out["flows_wrong_verb"]["ok"] is False and "disagree" in " ".join(out["flows_wrong_verb"]["errors"])
    assert out["years"]["ok"], out["years"]
    years_mark = next(m for m in marks.values() if m["target"] == "years_partner_export")
    assert set(years_mark["facts"]) >= {"fr_a", "fr_b", "fr_diff"}
    assert out["treemap_mark"]["ok"], out["treemap_mark"]
    # 5. keep, as the user
    assert out["keep"]["ok"] and out["keep"]["kept"] == [1]
    assert all(m["status"] == "kept" for m in page.marks_of_number(store.spec, 1))
    # 2. the cause finder: no total under a product's name with the wrong verb, no causal words, the line's figures
    # an event about one line never quotes all trade: {exp_change} is refused on it, even next to the line's figure
    for k in ("steel_total", "steel_with_total"):
        assert out[k]["ok"] is False and "not all trade" in out[k]["error"] and "{line_change}" in out[k]["error"]
    assert out["steel_caused"]["ok"] is False and "caused" in out["steel_caused"]["error"]
    assert out["steel_line"]["ok"], out["steel_line"]
    events = {m["params"]["label"]: m for m in _all_marks(store) if m["kind"] == "event"}
    steel = events["US steel tariffs 2025"]
    assert "line_change" in steel["facts"] and not any(k.startswith(("exp_", "imp_")) for k in steel["facts"])
    assert "not proof of cause" in steel["note"]
    # its line comes from the event's own scope: the three chapters exported to the US, summed
    leaves = [f["params"] for f in steel["facts"].values() if "params" in f]
    assert {p.get("partner") for p in leaves} == {"US"} and {p.get("product") for p in leaves} == {"72", "73", "76"}
    assert {p.get("flow") for p in leaves} == {"EXPORT"}
    assert out["steel_scope"]["figures_on_page"]["mark m4"]["line_change"] != "+8.6%"
    uk = events["EU-UK trade agreement"]
    assert {f["params"].get("partner") for f in uk["facts"].values() if "params" in f} == {"GB"}
    assert {"line_exp_change", "line_imp_change"} <= set(uk["facts"])
    ru = events["Sanctions on Russia"]
    assert {(f["params"].get("partner"), f["params"].get("product")) for f in ru["facts"].values() if "params" in f}         == {("RU", None)}
    rec = next(r for r in out["scoped_record"] if "72, 73, 76" in r["line"])
    assert rec["totals"][0] == "+8.6%" and rec["line_figures"]["exports"]["change"] not in rec["totals"]
    steel_row = next(e for e in out["scoped_events"] if e["id"] == "us-steel-232-2025")
    assert "United States" in steel_row["about_line"] and steel_row["line_that_year"]["exports"]["change"]
    assert not any("about_line" in e for e in out["scoped_events"] if e["id"] == "ever-given-2021")
    # 1. the period asked for, else the first year of the data: never a default 2015
    peers = page.panel(store.spec, out["peers_1999"]["panel"])
    assert {q["params"]["start"] for q in peers["queries"]} == {"1999"} and peers["options"]["base_period"] == "1999"
    assert peers["title"].endswith("since 1999") and "since 1999" in peers["text"]
    default = page.panel(store.spec, out["peers_default"]["panel"])
    assert {q["params"]["start"] for q in default["queries"]} == {"1999"}
    # 3. a highlight on a map of 2024 is about 2024
    assert next(m for m in _all_marks(store) if m["target"] == "map_exports")["period"] == "2024"
    # 5. monthly breaks never go on the yearly trend line, and say 'a month'
    assert out["breaks_on_trend"]["ok"], out["breaks_on_trend"]
    br = next(m for m in _all_marks(store) if m["kind"] == "trend_break")
    assert br["target"] != "trend" and "a month" in br["note"] and "yearly totals" in out["breaks_on_trend"]["note"]
    # 4. the skill placeholder matches the horizon written next to it
    assert out["skill_wrong"]["ok"] is False and "{skill_1m}" in out["skill_wrong"]["error"]
    assert out["skill_right"]["ok"], out["skill_right"]
    assert contract().validate_spec(store.spec) == []


def test_house_style_and_direction_checks():
    from lux_insights.tools import ToolProblem, direction_problems, horizon_problem, no_causal_words
    assert fmt.house_style("Exports €1.5 billion, imports €613 million.") == "Exports €1.5 bn, imports €613 m."
    assert fmt.no_causal_claims("The pandemic led to a drop. Due to it, exports fell; not proof of cause.") == \
        "The pandemic came with a drop. Alongside it, exports fell; not proof of cause."
    assert direction_problems("Exports fell by {x}.", {"x": "+8.6%"})
    assert direction_problems("Imports surged by {y}.", {"y": "−69%"})
    assert not direction_problems("Exports rose to {now}; moved {x}.", {"now": "€16.3 bn", "x": "+8.6%"})
    assert not direction_problems("Exports fell {x}.", {"x": "−3.5%"})
    no_causal_words("The dates line up; that is not proof of cause.", "event")
    with pytest.raises(ToolProblem):
        no_causal_words("Lockdowns led to a fall.", "event")
    horizon_problem("{skill_12m} at 12 months, {skill_1m} one month ahead")
    with pytest.raises(ToolProblem):
        horizon_problem("beats it by {skill_3m} at 1 month")


@needs_recording
def test_a_typed_figure_and_an_unknown_placeholder_come_back_as_errors(world):
    api, store, boxes = world
    tf = boxes["trend_finder"]
    tf.begin()
    tf.show_panel("waterfall", "What changed in exports", id="change_2025", flow="EXPORT", period="2025",
                  previous="2024")
    bad = call(tf, "add_annotation", target="change_2025", x="85", label="x", note="{value}: it rose 85% to €1.2 bn.")
    assert bad["ok"] is False and any("figure_in_text" in e for e in bad["errors"])
    unknown = call(tf, "add_annotation", target="change_2025", x="85", label="x", note="{nope}")
    assert unknown["ok"] is False and "{value}" in unknown["error"]
    assert not _all_marks(store)


@needs_recording
def test_a_backend_422_and_409_reach_the_model(world):
    api, store, boxes = world

    class Flaky(LocalView):
        def __init__(self, fail):
            super().__init__(insights=api)
            self.fail = list(fail)

        def post_ops(self, envelope):
            if self.fail:
                status = self.fail.pop(0)
                body = {"detail": "x", "version": self.spec["version"], "spec": copy.deepcopy(self.spec)}
                if status == 422:
                    body["errors"] = [{"path": "/ops/0", "code": "preflight", "message": "the API said no"}]
                raise ViewError(status, body)
            return super().post_ops(envelope)

    box = Toolbox("replay:", "visualizer", api=api, view=StubViewClient(Flaky([422]), "visualizer"))
    box._meta = {}
    out = call(box, "set_title", title="A new title")
    assert out["ok"] is False and out["status"] == 422 and "preflight" in out["errors"][0]
    retry = Flaky([409])
    box = Toolbox("replay:", "visualizer", api=api, view=StubViewClient(retry, "visualizer"))
    box._meta = {}
    assert call(box, "set_title", title="A new title")["ok"] is True   # rebuilt once after the 409
    assert retry.spec["title"] == "A new title"


def test_pick_facts_takes_what_the_text_needs_with_dependencies():
    vocab = {"a": {"endpoint": "/x", "params": {}, "path": "0"}, "b": {"endpoint": "/x", "params": {}, "path": "1"},
             "g": {"op": "growth", "of": ["a", "b"]}, "unused": {"endpoint": "/x", "params": {}, "path": "2"}}
    assert set(pick_facts("Up {g}.", vocab)) == {"g", "a", "b"}
    with pytest.raises(Exception, match="unknown placeholder"):
        pick_facts("{zzz}", vocab)


def test_every_role_has_only_tools_that_exist():
    box = Toolbox("http://localhost:1", "visualizer")
    for role, names in ROLE_TOOLS.items():
        tools = box.tools(names)
        assert [t.name for t in tools] == list(names)
        for t in tools:
            schema = t.openai_schema["function"]
            assert schema["description"] and schema["parameters"]["type"] == "object"
            assert set(schema["parameters"]["required"]) <= set(schema["parameters"]["properties"])
    assert "add_forecast_band" in ROLE_TOOLS["forecaster"] and "add_forecast_band" not in ROLE_TOOLS["trend_finder"]
    assert "add_events" in ROLE_TOOLS["cause_finder"] and "set_context" in ROLE_TOOLS["visualizer"]


# --------------------------------------------------------------------------- the loop and the reply checks
def test_reply_checks():
    assert with_title("Trend finder: exports rose.", "Trend finder") == "**Trend finder.** exports rose."
    assert with_title("**Trend finder.** Fine.", "Trend finder") == "**Trend finder.** Fine."
    assert with_title("Exports rose.", "Forecaster") == "**Forecaster.** Exports rose."
    assert "[3]" in cite_numbers("Text.\n\n_Next: why?_", [3]) and cite_numbers("See [3].", [3]) == "See [3]."
    assert clean_citations("Marked [2, 3, 4].", [1, 2]) == "Marked [2]."
    assert clean_citations("See [9].", [1, 2]) == "See."
    seen = [("eur", 1.29e9), ("pct", 0.086)]
    assert unverified_figures("Up €1.29 bn (+8.6%).", seen) == []
    assert unverified_figures("Up €2.5 bn, 40%.", seen) == ["€2.5 bn", "40%"]
    assert fmt.value(0.086, "pct_signed") == "+8.6%" and fmt.value(1.29e9, "eur") == "€1.29 bn"


@needs_recording
def test_a_specialist_reads_draws_and_replies_with_checked_figures(world):
    api, store, boxes = world
    llm = ScriptedLLM(scripts={"trend_finder": [
        [("drill_down", {"flow": "EXPORT", "period": "2025", "previous": "2024"})],
        "Exports rose €1.29 bn.",                                        # nothing drawn yet: pushed back
        [("add_thread", {"in_code": "CN", "out_code": "FR", "product": "85171300", "label": "Smartphones",
                         "note": "{bought} in, {sold} out."})],
        "Exports rose €9.99 bn, all smartphones.",                        # a figure no tool showed: rewrite
        "Exports rose €1.29 bn (+8.6%), two thirds of it smartphones.\n\n_Next: what comes next?_",
    ]})
    reply = run_specialist("trend_finder", "Why did exports jump in 2025?", llm, boxes["trend_finder"])
    assert reply.text.startswith("**Trend finder.** Exports rose €1.29 bn (+8.6%)")
    assert "[1]" in reply.text and reply.numbers == [1] and reply.unverified == []
    assert llm.offered["trend_finder"] == set(ROLE_TOOLS["trend_finder"])
    assert "Marks: none yet." in llm.prompts["trend_finder"][0]           # it saw the page
    assert [m["agent"] for m in store.spec["marks"]] == ["trend_finder"]


@needs_recording
def test_a_conversation_runs_the_plan_in_order_each_in_its_own_pen(world):
    api, store, boxes = world
    q = "Why did exports jump in 2025, which events line up, and what comes next?"
    plan = json.dumps({"plan": [{"agent": "trend_finder", "task": "the rise"}, {"agent": "cause_finder", "task": "events"},
                                {"agent": "forecaster", "task": "next year"}]})
    router_llm = ScriptedLLM(plans={q: plan})          # the plan comes from the small fast model
    llm = ScriptedLLM(
        scripts={"trend_finder": [[("add_thread", {"in_code": "CN", "out_code": "FR", "product": "85171300",
                                                   "label": "Smartphones", "note": "{bought} in."})],
                                  "One line did it."],
                 "cause_finder": [[("add_events", {"events": [{"event_id": "covid-19-2020",
                                                               "note": "Exports moved {exp_change}."}]})],
                                  "The pandemic lines up."],
                 "forecaster": [[("add_forecast_band", {"flow": "EXPORT", "note": "Next year: {value}."})],
                                "A band, not a promise."]})
    c = Conversation("replay:", llm, boxes=boxes, router_llm=router_llm)
    replies = list(c.answer(q))
    assert [r.agent for r in replies] == ["trend_finder", "cause_finder", "forecaster"]   # the plan's order
    assert replies[0].text.startswith("**Trend finder.**") and replies[2].text.startswith("**Forecaster.**")
    assert {(m["agent"], m["kind"]) for m in store.spec["marks"]} == {("trend_finder", "thread"),
                                                                       ("cause_finder", "event"),
                                                                       ("forecaster", "forecast")}
    # the cause finder builds on the trend finder, so it waited and read its answer; the forecaster did not wait
    assert "One line did it." in llm.prompts["cause_finder"][0]
    assert "One line did it." not in llm.prompts["forecaster"][0]
    assert [h["role"] for h in c.history] == ["user", "assistant", "assistant", "assistant"]
    assert [h["content"][:15] for h in c.history[1:]] == ["**Trend finder.", "**Cause finder.", "**Forecaster.**"]
    # one at a time when asked: every specialist reads the ones before it
    llm2 = ScriptedLLM(scripts={"trend_finder": ["One line did it."] * 2, "forecaster": ["A band."] * 2})  # nudged once
    c2 = Conversation("replay:", llm2, boxes=boxes, parallel=False,
                      router_llm=ScriptedLLM(plans={"q": json.dumps({"plan": ["trend_finder", "forecaster"]})}))
    list(c2.answer("q"))
    assert "One line did it." in llm2.prompts["forecaster"][0]
    assert mode_change("forecaster")["agent"] == "forecaster" and mode_change(None)["mode"] == "idle"


# --------------------------------------------------------------------------- registration, settings
def test_registration_payloads():
    for name in ROLES:
        body = registry.registration(name)
        assert body["title"] == AGENTS[name]["title"] and body["color"] == AGENTS[name]["color"]
        assert body["capabilities"][0]["description"] == AGENTS[name]["can"]
        assert len(body["examples"]) == 2 and all(q.endswith("?") or q[0].isupper() for q in body["examples"])
        for cap in body["capabilities"]:
            assert set(cap) == {"kind", "produces", "description", "inputs"}
    store = LocalView()
    view = StubViewClient(store, "forecaster")
    view.register(**registry.registration("forecaster"))
    assert store.agents["forecaster"]["color"] == "#C17F12"


def test_settings_read_the_key_from_dotenv_without_exposing_it(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nOPENAI_API_KEY="sk-test-123"\nLUX_AGENT_PORT=8866\n', encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LUX_AGENT_PORT", raising=False)
    names = settings.load_dotenv(env)
    assert names == ["OPENAI_API_KEY", "LUX_AGENT_PORT"] and settings.openai_key() == "sk-test-123"
    monkeypatch.setenv("OPENAI_API_KEY", "YOUR-API-KEY")
    assert settings.openai_key() is None
    config = (AGENTS_DIR / "lux_insights" / "config.yaml").read_text(encoding="utf-8")
    assert "api_key: YOUR-API-KEY" in config and "sk-" not in config


AGENT_PY = REPO / ".venv-agent" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@pytest.mark.skipif(not AGENT_PY.exists(), reason="no .venv-agent (scripts/setup_agent.sh)")
def test_the_baf_agent_builds_with_its_states():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([str(AGENT_PY), str(AGENTS_DIR / "lux_insights" / "lux_insights_agent.py"), "--check",
                        "--port", "8899", "--api", "http://localhost:1"], capture_output=True, text=True,
                       timeout=180, env=env, cwd=REPO)
    assert r.returncode == 0, r.stderr[-2000:]
    lines = dict(line.split(" -> ") for line in r.stdout.splitlines() if " -> " in line)
    assert set(lines["router (initial)"].split(", ")) == {*ROLES, "general", "router"}
    assert "router" in lines["forecaster"].split(", ")
    assert "sk-" not in r.stdout + r.stderr


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--record":
        record(sys.argv[2])
    else:
        print("usage: python tests/test_lux_insights_agent.py --record http://localhost:8100")


# --------------------------------------------------------------------------- personas (contract 1.4)
def _page_for(persona):
    return {"version": 3, "title": "t", "context": {"period": "2025", "product": "TOTAL", "persona": persona},
            "panels": [], "marks": []}


def test_the_router_caps_the_agents_per_question_for_the_reader():
    """The journalist gets one voice, the analyst up to three: enforced in code, whatever the LLM plans."""
    q = "Why did exports jump in 2025, and what comes next?"
    llm = ScriptedLLM(plans={q: json.dumps({"plan": [{"agent": a, "task": "t"}
                                                      for a in ("trend_finder", "cause_finder", "forecaster")]})})
    assert router.route(q, llm, spec=_page_for("journalist")).agents == ["trend_finder"]
    assert router.route(q, llm, spec=_page_for("policy_adviser")).agents == ["trend_finder", "cause_finder"]
    assert router.route(q, llm, spec=_page_for("statec_analyst")).agents == ["trend_finder", "cause_finder", "forecaster"]
    assert router.route(q, llm, spec=_page_for("everyone")).agents == ["trend_finder", "cause_finder", "forecaster"]
    fallback = ScriptedLLM(plans={q: RuntimeError("no network")})
    assert len(router.route(q, fallback, spec=_page_for("citizen")).agents) == 1
    assert "at most 1 agent" in router.reader_rules(router.reader_of(_page_for("journalist")))


def test_each_specialist_is_told_who_is_asking():
    from lux_insights.specialists import system_prompt
    from agents.kit.personas import profile
    journalist = system_prompt("trend_finder", "page", [], "", None, reader=profile("journalist"))
    assert "## Who is asking: Journalist" in journalist and "at most 3 sentences" in journalist
    assert "no HS or CN8 codes" in journalist and "Source: Eurostat ds-045409" in journalist
    assert "say \"smartphones\" for \"CN8 85171300\"" in journalist
    analyst = system_prompt("trend_finder", "page", [], "", None, reader=profile("statec_analyst"))
    assert "write HS/CN8 codes next to names" in analyst and "at most 6 sentences" in analyst
    plain = system_prompt("trend_finder", "page", [], "", None)
    assert "Who is asking" not in plain
    # The rules on figures are the same for every reader.
    rules = [line for line in plain.splitlines() if "figure" in line.lower()]
    assert rules and all(line in journalist for line in rules)


def test_the_page_summary_names_the_reader():
    assert "read by: journalist (Journalist" in page.summary(_page_for("journalist"))
    assert "read by" not in page.summary({**_page_for("x"), "context": {"period": "2025", "product": "TOTAL"}})


# --------------------------------------------------------------------------- the first fix round
def test_the_period_asked_for_is_read_from_the_question():
    from lux_insights.tools import asked_span
    assert asked_span("Compare Luxembourg with Belgium and the Netherlands since 1999") == {"start": "1999"}
    assert asked_span("Exports from 2010 to 2020") == {"start": "2010", "end": "2020"}
    assert asked_span("What changed between 2008 and 2012?") == {"start": "2008", "end": "2012"}
    assert asked_span("Index with 2005 = 100 since 2000") == {"start": "2000", "base": "2005"}
    assert asked_span("Why did exports jump in 2025?") == {} and asked_span("Compare 2025 with 2024") == {}
    assert asked_span("since 1995") == {}       # before 1999 Luxembourg reported inside BLEU


def test_every_specialist_knows_the_data_starts_in_1999():
    from lux_insights.specialists import system_prompt
    for role in ("trend_finder", "cause_finder", "forecaster", "visualizer"):
        prompt = system_prompt(role, "page", [], "", None, coverage={"first_year": "1999", "last_year": "2025"})
        assert "Annual 1999 to 2025" in prompt and "Never default to 2015" in prompt
        assert 'base_period="2015"' not in prompt
    assert "never \"since 2015\"" in router.PROMPT


def test_replies_fit_the_reader_in_code():
    from agents.kit.personas import profile
    from lux_insights.specialists import fit_reader, length_problem, reader_limits, without_codes
    long = ("**Trend finder.** Exports rose from €15 bn to €16.3 bn in 2025, +8.6%. Smartphones (CN8 85171300) led "
            "the rise, from €42 m to €898 m. Without them growth was +2.9%. France bought most. I marked it [1].\n\n"
            "Source: Eurostat ds-045409, data produced by STATEC\n\n_Next: What comes next?_")
    journalist = reader_limits(profile("journalist"))
    assert journalist == {"max_sentences": 3, "max_words": None, "show_codes": False}
    assert "5 sentences" in length_problem(long, journalist)
    short = fit_reader(long, journalist)
    assert short.startswith("**Trend finder.** Exports rose") and "85171300" not in short and "CN8" not in short
    assert short.count(". ") + 1 >= 3 and "France bought most" not in short
    assert "ds-045409" in short and short.endswith("_Next: What comes next?_")
    assert length_problem(short, journalist) is None
    citizen = reader_limits(profile("citizen"))
    assert citizen["max_words"] and citizen["show_codes"] is False
    assert without_codes("Electrical machinery (HS 85), mostly smartphones (85171300). HS2 chapter 84 came next.") \
        == "Electrical machinery, mostly smartphones. Machinery came next."
    assert reader_limits(profile("statec_analyst")) is None and reader_limits(None) is None


@needs_recording
def test_the_journalist_gets_three_sentences_even_when_the_model_writes_more(world):
    api, store, boxes = world
    store.spec["context"] = {"period": "2025", "product": "TOTAL", **store.spec.get("context", {}),
                             "persona": "journalist"}
    long =("Exports rose in 2025, mostly smartphones. Smartphones did most of it. France bought them. China sold "
            "them. The rest grew a little.\n\n_Next: what comes next?_")
    llm = ScriptedLLM(scripts={"trend_finder": [
        [("add_thread", {"in_code": "CN", "out_code": "FR", "product": "85171300", "label": "Smartphones",
                         "note": "{bought} in, {sold} out."})],
        long, long]})                                       # asked to shorten once, it does not: cut in code
    reply = run_specialist("trend_finder", "Why did exports jump in 2025?", llm, boxes["trend_finder"])
    from lux_insights.specialists import split_reply
    _, body, tail = split_reply(reply.text)
    assert len(body) == 3 and body[0].startswith("Exports rose in 2025") and "The rest" not in reply.text
    assert "[1]" in reply.text and any(t.startswith("_Next") for t in tail)
    assert len(llm.prompts["trend_finder"]) == 3          # thread, long reply, the one rewrite


def test_a_total_quoted_for_an_event_about_one_line_is_caught():
    from lux_insights.specialists import scoped_total_problems
    scoped = [{"label": "US steel tariffs 2025", "line": "iron and steel exports to the United States", "year": "2025",
               "totals": ["+8.6%", "+4.2%"], "line_figures": {"exports": {"change": "−22.1%"}}}]
    bad = ("**Cause finder.** The US steel tariffs of March 2025 line up with exports moving +8.6%. COVID-19 lines up "
           "with exports falling, +8.6% the year after.")
    (p,) = scoped_total_problems(bad, scoped)
    assert p["event"] == "US steel tariffs 2025" and p["totals"] == ["+8.6%"]
    assert scoped_total_problems("The US steel tariffs line up with steel exports to the US moving −22.1%.", scoped) == []


# --------------------------------------------------------------------------- fix round B (review)
@needs_recording
def test_round_b_tools_in_code(world, monkeypatch):
    monkeypatch.setenv("VIEW_MAX_AGENT_PANELS", "50")   # many charts in a row: no replacement here
    api, store, boxes = world
    out = scenario_round_b(boxes)
    # 1. "Show me 2025" never clears the page and adds no chart; only an explicit clear does
    assert out["show_year_clear"]["ok"] is False and "clear" in out["show_year_clear"]["error"]
    assert out["show_year_panel"]["ok"] is False
    # 5. "as a table" is a table panel; marks go on it
    assert out["table"]["ok"] and page.panel(store.spec, out["table"]["panel"])["type"] == "table"
    assert out["table_mark"]["ok"], out["table_mark"]
    # 6. maps follow the page year when the user named none, and their titles name none
    for pid in ("map_exports", "routes"):
        p = page.panel(store.spec, pid)
        assert p["queries"][0]["params"]["period"] == "$period" and "2025" not in p["title"], p
    assert page.panel(store.spec, out["table"]["panel"])["queries"][0]["params"]["period"] == "$period"
    # 5. year-on-year of France and Germany: one compare chart, both partners, yoy mode, never a line of values
    assert out["yoy_line"]["ok"] is False and "yoy" in out["yoy_line"]["error"]
    yoy = page.panel(store.spec, out["yoy"]["panel"])
    assert yoy["options"]["compare"] == "yoy" and {q["params"]["partner"] for q in yoy["queries"]} == {"FR", "DE"}
    # 3. bought vs sold, partner by partner: a butterfly by partner, never a compare chart
    assert out["two_ways_compare"]["ok"] is False and "butterfly" in out["two_ways_compare"]["error"]
    bf = page.panel(store.spec, "two_ways_partner")
    assert bf["type"] == "butterfly" and {q["params"]["by"] for q in bf["queries"]} == {"partner"}
    assert out["two_ways_mark"]["ok"], out["two_ways_mark"]
    # 5. the gap between exports and imports: exports against imports with the balance
    gap = page.panel(store.spec, out["gap"]["panel"])
    assert gap["type"] == "compare" and gap["options"].get("balance") is True
    # 5. the visualizer marks a month-by-month heatmap with trend breaks; a seasonal one takes none, said plainly
    assert out["heatmap_breaks"]["ok"], out["heatmap_breaks"]
    assert "trend breaks" in out["heatmap"]["marks_it_takes"] and "no marks" in out["seasonal"]["marks_it_takes"]
    assert out["seasonal_breaks"]["ok"] is False and "say so plainly" in out["seasonal_breaks"]["error"]
    # 2. the thread's note is fixed: each share with its base, whatever the model wrote
    thread = next(m for m in _all_marks(store) if m["kind"] == "thread")
    assert thread["note"].startswith("China supplied {bought_share} of the smartphones Luxembourg bought")
    assert "of everything Luxembourg bought from China" in thread["note"] and "imports from China" not in thread["note"]
    shown = out["thread"]["figures_on_page"]["thread Smartphones: bought from China, sold to France"]
    assert shown["bought_share"] == "87.5%" and shown["sold_share"] == "85.7%" and shown["in_share"] == "80.6%"
    assert out["headline_base"]["ok"] is False and "share of the product line" in out["headline_base"]["error"]
    # 10. the headline holds two lines (160 characters as shown), the title two lines at its size
    assert out["headline_long"]["ok"] is False and "at most 160" in out["headline_long"]["error"]
    assert out["headline_long_again"]["ok"], out["headline_long_again"]
    head = page.panel(store.spec, "headline")["text"]
    assert head.startswith("Exports moved {growth} in 2025; smartphones made up {line_share} of the change.")
    assert out["title_long"]["ok"] is False
    # 7. events are measured against the trend breaks: none within 12 months here
    assert [e["lines_up_with_a_break"] for e in out["event_breaks"]] == [False, False]
    assert out["events"]["trend_breaks"]["events"][0]["months_apart"] == 134
    # 4. the backtest of every method, the BESSER network and the naive benchmark included
    table = "\n".join(out["backtest"]["table"])
    assert out["backtest"]["table"][0].startswith("- The ensemble of Holt-Winters and gradient boosting (the default)")
    assert "neural network generated by BESSER" in table and "seasonal naive forecast" in table
    assert " at 12 months" in table and out["backtest"]["default_method"].startswith("The ensemble")
    # 8. the outlook in sentence case, no '..'
    # {summary} names the method again ("The forecast uses X. X beats ..."): with {method} in the text it goes
    # the outlook leads with the figure and its interval, the method in one plain clause, the scenarios as the line
    text = out["outlook"]["text"]
    assert text.startswith("Exports in 2026: {value}, likely between {low} and {high} (the {interval} interval).")
    assert "{summary}" not in text and "{method}" not in text and "one-off" not in text
    assert text.endswith("The forecast uses an average of two standard forecasting methods (named in the model "
                         "card). 12 months ahead it was off by {error_12m}.")
    assert contract().validate_spec(store.spec) == []


def test_round_b_router_plans_in_code():
    assert router.route("Show me 2025").steps[0].agent == "visualizer"
    assert "never clear" in router.route("Show me 2025").steps[0].task
    assert router.route("Bought vs sold, partner by partner").agents == ["visualizer"]
    assert "butterfly" in router.route("Bought vs sold by chapter").steps[0].task
    general = ScriptedLLM(plans={"Which forecasting method works best here?": '{"plan": [{"agent": "general"}]}'})
    assert router.route("Which forecasting method works best here?", general).agents == ["forecaster"]
    assert router.asks_method("How good is the forecast?") and not router.asks_method("What comes next?")


def test_round_b_reply_checks():
    from lux_insights import specialists as sp
    from lux_insights.tools import line_share, misattributed_shares, share_base_problems, tidy_outlook
    # 3. tool talk never reaches the reader
    leak = ("**Visualizer.** I attempted to draw a chart. However, the tool requires specific placeholders for "
            "figures, and the current setup exceeded the allowed properties. Germany bought €3.99 bn.")
    assert len(sp.leaked(leak)) == 1 and not sp.leaked("**Forecaster.** It was off by 9.8% 12 months ahead.")
    # 5. claims of marks not drawn
    assert sp.unbacked_claims("I highlighted these areas on the scatter plot.", set(), False)
    assert sp.unbacked_claims("I highlighted them on the scatter plot [1].", {"choropleth"}, True)
    assert not sp.unbacked_claims("I highlighted Belgium on the map [1].", {"choropleth"}, True)
    # 2. a share of one line written as a share of a whole flow
    shares = [line_share(0.875, "China", "smartphones", "IMPORT"), line_share(0.857, "France", "smartphones", "EXPORT")]
    bad = "Smartphones grew, making up 87.5% of imports from China and 85.7% of exports to France."
    assert [p["written"] for p in misattributed_shares(bad, shares)] == ["87.5%", "85.7%"]
    assert not misattributed_shares("China supplied 87.5% of the smartphones Luxembourg bought.", shares)
    assert share_base_problems("France took {sold_share} of its exports.") and \
        not share_base_problems("France took {sold_share} of the line's exports; {line_share} of the rise.")
    # 7. alignment with breaks is measured, and the list's start is said
    eb = [{"label": "COVID-19", "at": "2020-03", "nearest_break": "2021-12", "months_apart": 21,
           "lines_up_with_a_break": False},
          {"label": "EU-UK trade agreement", "at": "2021-01", "nearest_break": "2021-12", "months_apart": 11,
           "lines_up_with_a_break": True}]
    text = ("**Cause finder.** The EU-UK trade agreement lines up with the break of 2021. The dates of these events "
            "align with the breaks in the series, but this alignment does not prove causation.")
    assert sp.alignment_problems(text, eb) == ["The dates of these events align with the breaks in the series, but "
                                               "this alignment does not prove causation."]
    assert sp.alignment_sentence(eb).startswith("Only EU-UK trade agreement falls within 12 months")
    assert not sp.alignment_problems("No break lies within 12 months of COVID-19.", eb)
    said = sp.list_start_sentence("Which events line up with the changes since 1999?", "x", "2017",
                                  ["2001-02", "2009-01", "2021-12"])
    assert said == ("The curated list of events starts in 2017, so no event in it covers the earlier changes "
                    "(2001 and 2009).")
    assert sp.list_start_sentence("Why did our exports fall in 2025? Show the months since 2023 with the 2025 tariff "
                                  "events", "x", "2017", ["2001-02", "2009-01"]) is None   # a span after the list
    # 8. the outlook text
    assert tidy_outlook("{method} beats the naive forecast. {summary}.") == \
        "The method, {method}, beats the naive forecast."
    assert tidy_outlook("{summary} The band held {band_held}.") == "{summary} The band held {band_held}."
    # 9. the clear reply and the follow-up chip
    assert "except the mark you kept ([1])" in sp.clear_reply("Visualizer", {"kept": [1], "pinned": []}, "x")
    assert "all charts" not in sp.clear_reply("Visualizer", {"kept": [1, 2], "pinned": []}, "")
    fixed = sp.fix_next("**Visualizer.** Moved.\n\n_Next: Show me 2025?_", "visualizer", ["Show me 2025"], "2025")
    assert "Show me 2025" not in fixed and fixed.endswith("_Next: Where do the goods go?_")
    # a Next line above the source line goes last, and only one (15 Oct: two chips' lines in one reply)
    fixed = sp.fix_next("**Trend finder.** Body.\n\n_ Next: Which product groups drove the export rise?_\n\n"
                        "Source: Eurostat ds-045409.", "trend_finder", ["q"])
    assert fixed.count("Next:") == 1 and fixed.endswith("_Next: Which product groups drove the export rise?_")
    assert "Source: Eurostat ds-045409." in fixed
    kept = sp.fix_next("**Visualizer.** Moved.\n\n_Next: What does Luxembourg sell?_", "visualizer", ["Show me 2025"])
    assert kept.endswith("_Next: What does Luxembourg sell?_")


@needs_recording
def test_round_b_a_leaking_reply_is_rewritten_then_cut(world):
    api, store, boxes = world
    llm = ScriptedLLM(scripts={"visualizer": [
        [("compare", {"what": "flows"})],
        "I could not draw it: the tool requires specific placeholders and exceeded the allowed properties.",
        "The tool refused the placeholders again.",                       # nothing drawn: pushed back once
        "Belgium sells Luxembourg the most. The tool refused the placeholders again.\n\n_Next: Bought vs sold, partner by "
        "partner?_",                                                       # tool talk: one rewrite, then cut in code
    ]})
    reply = run_specialist("visualizer", "Bought vs sold, partner by partner", llm, boxes["visualizer"],
                           asked=["Bought vs sold, partner by partner"])
    assert "tool" not in reply.text.lower() and "placeholder" not in reply.text.lower()
    assert "could not" not in reply.text and "this time" not in reply.text   # never the attempt (15 Oct)
    assert "Belgium sells Luxembourg the most." in reply.text
    assert "_Next: Bought vs sold" not in reply.text          # never the question just asked


@needs_recording
def test_round_b_the_forecaster_answers_the_method_question_with_the_backtest(world):
    api, store, boxes = world
    llm = ScriptedLLM(scripts={"forecaster": [
        [("get_backtest", {"flow": "EXPORT"})],
        [("add_forecast_band", {"flow": "EXPORT", "note": "Exports in 2026: {value}.",
                                "outlook": "{method}. {summary}."})],
        "The default is the ensemble of Holt-Winters and gradient boosting.\n- Something general.\n\n"
        "_Next: Which forecasting method works best here?_"]})
    reply = run_specialist("forecaster", "Which forecasting method works best here?", llm, boxes["forecaster"])
    assert "- The ensemble of Holt-Winters and gradient boosting (the default): +49.2% at 1 month" in reply.text
    assert "- The neural network generated by BESSER:" in reply.text and "Something general" not in reply.text
    assert "Which forecasting method works best here?" not in reply.text.split("_Next")[-1]
    assert reply.unverified == []


@needs_recording
def test_round_b_a_clear_reply_says_what_stayed(world):
    api, store, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Where do the goods go?")
    vz.show_panel("choropleth", "Where the goods go", id="map_exports", flow="EXPORT")
    vz.highlight("map_exports", "Germany {de_share}.", codes=["DE"])
    vz.keep_marks([1])
    llm = ScriptedLLM(scripts={"visualizer": [[("clear_page", {})],
                                              "The page is now cleared of all charts and data."]})
    reply = run_specialist("visualizer", "Clear the page", llm, vz)
    assert "all charts" not in reply.text and "except the mark you kept ([1])" in reply.text
    assert [m["status"] for m in store.spec["marks"]] == ["kept"]


@needs_recording
def test_round_b_typed_figures_in_a_break_or_event_note_get_the_house_note(world):
    api, store, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("When did exports shift for good?")
    tf.analyze_trend("EXPORT")
    out = call(tf, "add_trend_breaks", flow="EXPORT", breaks=[
        {"at": "2009-01", "label": "Step down, 2009", "note": "From €1.46 bn to €1.27 bn a month, a −12.4% change."}])
    assert out["ok"], out
    br = next(m for m in _all_marks(store) if m["kind"] == "trend_break")
    assert br["note"] == "A typical month went from {before} to {after}, {change}."
    cf = boxes["cause_finder"]
    cf.begin("What did COVID-19 do to trade?")
    ev = call(cf, "add_events", events=[{"event_id": "covid-19-2020", "note": "Exports fell 17.4% that year."}])
    assert ev["ok"], ev
    covid = next(m for m in _all_marks(store) if m["kind"] == "event")
    assert covid["note"].startswith("Exports moved {exp_change} and imports {imp_change} that year.")


@needs_recording
def test_round_b_the_method_answer_gets_the_table_even_when_the_model_skips_the_tool(world):
    api, store, boxes = world
    llm = ScriptedLLM(scripts={"forecaster": [
        [("add_forecast_band", {"flow": "EXPORT", "note": "Exports in 2026: {value}."})],
        "The ensemble of Holt-Winters and gradient boosting is the default."]})
    reply = run_specialist("forecaster", "Which forecasting method works best here?", llm, boxes["forecaster"])
    assert "- The neural network generated by BESSER:" in reply.text and "- The seasonal naive forecast" in reply.text


def test_the_forecasters_two_tests_are_said_apart():
    from lux_insights import specialists as sp
    from lux_insights.tools import tested_span, two_tests
    bt = {"origins": 20, "first_origin": "2021-06", "last_origin": "2026-03", "skill_h1": 0.4919, "skill_h12": 0.0299,
          "period": "2025", "mape": 0.0986, "naive_mape": 0.0792}
    assert tested_span(bt) == "the 20 quarter ends from June 2021 to March 2026"
    assert two_tests(bt) == (
        "Tested at the 20 quarter ends from June 2021 to March 2026, it beats the seasonal naive forecast (the same "
        "month a year earlier) by 49.2% one month ahead and by 3% twelve months ahead. Tested on the single year 2025, "
        "forecast from the December before, it was off by 9.9%, against 7.9% for repeating 2024: on that one year it "
        "did not beat the naive guess.")
    assert "not at all twelve months ahead" in two_tests({**bt, "skill_h12": -0.1})
    assert "did not beat" not in two_tests({**bt, "mape": 0.05})
    # the prompt names the method once and says the two tests apart
    role = sp.ROLE["forecaster"]
    assert "never add {summary}" in role and "two_tests" in role and "The forecast uses {method}. {summary}" not in role


def test_the_general_agent_says_where_a_question_goes():
    from lux_insights import specialists as sp
    text = sp.ROLE["general"]
    assert "OpenAI's API under the team's key" in text and "summary of the page" in text
    assert "never a user" in text and "aggregated official statistics" in text
    assert 'Never say the question is "not stored", "not shared"' in text
    for q in ("Is any personal data used here?", "Where does my question go?"):   # planned in code, never a map
        assert router.route(q).agents == ["general"] and router.route(q).source == "rule"
    assert router.route("Where do the goods go?").agents == ["visualizer"]


def test_other_partners_are_never_called_confidential():
    from lux_insights import specialists as sp
    from lux_insights.tools import summarize, unspecified_problems
    data = {"by": "partner", "period": "2025", "total": 16.25e9,
            "items": [{"code": "DE", "name": "Germany", "value": 4.0e9, "share": 0.246}],
            "other": {"name": "Other (187)", "value": 3.78e9, "share": 0.232, "count": 187},
            "unallocated": {"value": 253.5e6, "share": 0.0156}}
    out = summarize("/insights/ranking", data)
    assert "other" not in out and out["other_partners"]["value"].startswith("€3.78")
    assert "the other 187 partners together" in out["other_partners"]["say"]
    assert "QV, QW, QS" in out["partner_not_specified"]["say"]
    seen = [("eur", 253.5e6), ("pct", 0.0156)]
    bad = "Germany took 24.6%. A further €3.78 bn is confidential or not specified by partner."
    assert unspecified_problems(bad, seen) == ["A further €3.78 bn is confidential or not specified by partner."]
    assert not unspecified_problems("Trade with no partner specified (QV, QW, QS) was €254 m, 1.6%.", seen)
    assert not unspecified_problems("Exports were €16.3 bn in 2025. Some trade is confidential.", seen)
    assert not unspecified_problems("The other partners together bought €3.78 bn.", seen)
    assert unspecified_problems("€254 m was not specified by partner.", [])   # the codes were not read this turn
    assert '"other partners", never "confidential" or "not specified"' in sp.COMMON.replace("\n  ", " ")


# --------------------------------------------------------------------------- the agents' slots
def test_the_agent_keeps_three_charts_and_says_what_it_replaced(world, monkeypatch):
    """Four chart questions in a row: the fourth chart takes the place (and the slot) of the first; asking for a
    kind already drawn re-draws it in place; a pinned chart is never targeted, and the reply says what went."""
    from lux_insights.specialists import with_replaced
    monkeypatch.setenv("VIEW_MAX_AGENT_PANELS", "3")
    api, store, boxes = world
    vz = boxes["visualizer"]
    asked = [VISUALS[0], VISUALS[1], VISUALS[5], VISUALS[6]]       # map, routes, treemap, butterfly
    outs, slots = [], []
    for v in asked:
        vz.begin(f"Show {v['title']}")
        outs.append(call(vz, "show_panel", **v))
        assert outs[-1]["ok"], outs[-1]
        slots.append([i for i, r in enumerate(store.spec["layout"]["rows"]) if outs[-1]["panel"] in r["panels"]])
    charts, limit = page.agent_charts(store.spec)
    assert limit == 3 and [p["type"] for p in charts] == ["flowmap", "treemap", "butterfly"]
    assert outs[-1]["replaced"] == [{"chart": "Where the goods go", "type": "choropleth"}]
    assert "never write about replacing" in outs[-1]["note_on_replaced"]
    assert slots[3] == slots[0]                                   # the butterfly sits where the map was
    assert [r["id"] for r in vz.turn.replaced] == [outs[0]["panel"]]
    assert "Agent charts: 3 of the 3" in page.summary(store.spec)

    vz.begin("Show what Luxembourg sells, as tiles")              # a kind already there: re-drawn in place
    rows = copy.deepcopy(store.spec["layout"]["rows"])
    again = call(vz, "show_panel", type="treemap", title="The goods Luxembourg sells", flow="EXPORT")
    assert again["ok"] and again["panel"] == outs[2]["panel"] and "replaced" not in again
    assert store.spec["layout"]["rows"] == rows

    butterfly = outs[3]["panel"]                                  # the user pins the butterfly
    store.post_ops({"agent": {"name": "user"}, "ops": [{"op": "pin_panel", "id": butterfly, "pinned": True}]})
    assert "PINNED by the user" in page.summary(store.spec)
    vz.begin("Bought and sold again")
    before = store.spec["version"]
    same = call(vz, "show_panel", id=butterfly, **VISUALS[6])     # the pinned chart is this very chart: kept as is
    assert same["ok"] and same["panel"] == butterfly and same.get("changed") is False
    other = call(vz, "show_panel", id=butterfly, **VISUALS[7])   # another chart under the pinned id
    assert other["ok"] and other["panel"] != butterfly, other     # never a refusal: the new chart gets its own id
    assert page.panel(store.spec, butterfly)["pinned"] and page.panel(store.spec, butterfly)["type"] == "butterfly"
    store.post_ops({"agent": {"name": "user"}, "ops": [{"op": "remove_panel", "id": other["panel"], "force": True}]})
    before = store.spec["version"]
    out = call(vz, "show_panel", **VISUALS[6])                   # the very same chart: the page shows it already
    assert out["ok"] and out["panel"] == butterfly and out.get("changed") is False
    assert store.spec["version"] == before and page.panel(store.spec, butterfly)["pinned"]

    reply = with_replaced("**Visualizer.** Bought and sold, side by side [1].\n\n_Next: What does it sell?_",
                          [{"id": "viz_map_exports", "title": "Where the goods go"}])
    assert "takes the place of “Where the goods go”" in reply and reply.rstrip().endswith("_")
    # the model's own words about replacing never stay: the sentence comes from the page's answer only
    said = "**Visualizer.** Bought and sold [1]. The butterfly replaces the treemap."
    assert with_replaced(said, [{"title": "Where the goods go"}]) == \
        "**Visualizer.** Bought and sold [1]. The new chart takes the place of “Where the goods go”; pin a chart to keep it."
    assert with_replaced(said, []) == "**Visualizer.** Bought and sold [1]."


def test_follow_up_with_a_doubled_ending_and_a_one_year_chart_without_a_span():
    """The chip asks 'X?', never 'X?_?' (a model's '_Next: X?_?_'); a chart of one year that follows the page's
    year drops a span of years from its title ('…, 1999–2025' over a 2024-to-2025 waterfall)."""
    import lux_insights.specialists as sp
    from lux_insights.tools import _drop_span
    doubled = sp.fix_next("**Trend finder.** Done.\n\n_Next: What products drove the export rise?_?_", "trend_finder", [])
    assert doubled.endswith("_Next: What products drove the export rise?_")
    assert _drop_span("Products behind the export rise, 1999–2025") == "Products behind the export rise"
    assert _drop_span("Exports by chapter (2024 to 2025)") == "Exports by chapter"
    assert _drop_span("What changed between 2008 and 2012 in exports") == "What changed in exports"
    assert _drop_span("Where exports go in 2025") == "Where exports go in 2025"   # one year: _drop_year's job


def test_the_page_summary_says_a_chart_has_its_own_year():
    """Contract 1.5: a chart with its own year or goods is flagged, so the agent does not read it as the page's."""
    store = LocalView()
    view = StubViewClient(store, "user")
    view.send([{"op": "set_panel_context", "id": "peers", "period": "2020", "product": "85"}, view.set_panel_context("mix_export", period="2019")])
    lines = page.summary(store.spec).splitlines()
    assert "OWN CONTEXT period 2020, product 85" in next(l for l in lines if l.startswith("- peers "))
    assert "OWN CONTEXT period 2019" in next(l for l in lines if l.startswith("- mix_export "))
    assert "OWN CONTEXT" not in next(l for l in lines if l.startswith("- trend "))
    view.send([view.set_panel_context("peers")])
    assert "OWN CONTEXT" not in next(l for l in page.summary(store.spec).splitlines() if l.startswith("- peers "))
