# SPDX-License-Identifier: MIT
"""Goods named right (the 'nuclear' answer of 15 Oct 2026, which drew chapter 84 as "nuclear reactors").

- An HS2 chapter is named by its short name (HS_SHORT: "machinery (HS 84)"), never by its official title, in what
  the tools show the model and in what goes on the page.
- 'nuclear' maps to HS 8401 (nuclear reactors and fuel elements), not to chapter 84 whose official title starts
  with those words; when 8401 has no recorded trade, the answer says so plainly and draws no chart.
- A reply, title or note that gives chapter 84 the words "nuclear reactors" is rewritten once, then fixed in code.

No network, no LLM: a small fake API and the kit's in-memory page.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
pytest.importorskip("httpx")

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "app" / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents.kit import InsightsClient, InsightsError, source_url  # noqa: E402
from agents.kit.testing import LocalView, StubViewClient  # noqa: E402
from agents.kit.view import HS_SHORT, chapter_name  # noqa: E402
from lux_insights import products  # noqa: E402
from lux_insights.conversation import Conversation  # noqa: E402
from lux_insights.specialists import honest_in_code, honesty_issues, run_specialist  # noqa: E402
from lux_insights.tools import ROLES, Toolbox, summarize  # noqa: E402

OFFICIAL_84 = "NUCLEAR REACTORS, BOILERS, MACHINERY AND MECHANICAL APPLIANCES; PARTS THEREOF"
CODELIST = [
    {"code": "84", "label": OFFICIAL_84, "level": 1},
    {"code": "85", "label": "ELECTRICAL MACHINERY AND EQUIPMENT AND PARTS THEREOF; SOUND RECORDERS AND REPRODUCERS",
     "level": 1},
    {"code": "72", "label": "IRON AND STEEL", "level": 1},
    {"code": "8401", "label": 'Nuclear reactors; fuel elements "cartridges", non-irradiated, for nuclear reactors',
     "level": 2},
    {"code": "8428", "label": "Lifting, handling, loading or unloading machinery, e.g. lifts, escalators", "level": 2},
    {"code": "8471", "label": "Automatic data-processing machines and units thereof; magnetic or optical readers",
     "level": 2},
    {"code": "7208", "label": "Flat-rolled products of iron or non-alloy steel, of a width >= 600 mm", "level": 2},
    {"code": "840110", "label": "Nuclear reactors", "level": 3},
]
PHONES = {("85171200", "EXPORT"): {"2015": 2139116563.0, "2016": 1108234168.0, "2017": 140329797.0,
                                  "2018": 309065216.0, "2019": 1470073047.0, "2020": 32169632.0, "2021": 36217198.0},
          ("85171300", "EXPORT"): {"2022": 45419993.0, "2023": 38712921.0, "2024": 42293544.0, "2025": 898331987.0},
          ("85171200", "IMPORT"): {"2015": 2172406666.0, "2019": 1437105813.0, "2021": 148288795.0},
          ("85171300", "IMPORT"): {"2022": 165197048.0, "2025": 701316175.0}}
VALUES = {("84", "IMPORT"): 2885716872.0, ("84", "EXPORT"): 1.2e9, ("8428", "IMPORT"): 770902390.0,
          ("8471", "IMPORT"): 335000000.0, ("72", "EXPORT"): 3.1e9, ("7208", "EXPORT"): 1.1e9}


class FakeInsights(InsightsClient):
    """The few answers these tests need; an unknown call is a 404, as an unknown code would be."""

    def __init__(self):
        self.base_url, self.calls = "fake:", []

    def close(self) -> None:
        pass

    def get(self, endpoint: str, **params):
        url = source_url(endpoint, params)
        self.calls.append(url)
        if endpoint == "/product/search/":
            word = params["label"].lower()
            return [copy.deepcopy(r) for r in CODELIST
                    if word in r["label"].lower() and (params.get("level") is None or r["level"] == params["level"])], url
        if endpoint == "/insights/series":
            hist = PHONES.get((params.get("product"), params.get("flow")))
            if hist is not None:
                lo, hi = str(params.get("start") or "1999")[:4], str(params.get("end") or "2025")[:4]
                return [{"period": y, "value": v} for y, v in sorted(hist.items()) if lo <= y <= hi], url
            v = VALUES.get((params.get("product"), params.get("flow")))
            year = params.get("end") or "2025"
            return ([{"period": year, "value": v}] if v else []), url
        if endpoint == "/insights/forecast":
            if str(params.get("product") or "").startswith("8517"):
                raise InsightsError(422, "Not enough monthly history for a tested forecast: this series has 54 "
                                         "consecutive months (from 2022-01)", url)
            rows = [{"period": "2025", "value": 1.5e9},
                    {"period": "2026", "value": 1.6e9, "low": 1.4e9, "high": 1.8e9}]
            if int(params.get("horizon") or 12) > 12:
                rows.append({"period": "2027", "value": 1.7e9, "low": 1.3e9, "high": 2.1e9})
            return {"method": "ensemble", "method_label": "ensemble", "interval": 0.8, "rows": rows,
                    "scenarios": [{"id": "stops", "label": "The line stops",
                                   "rows": [{"period": "2026", "value": 1.2e9, "low": 1.1e9, "high": 1.3e9}]}]}, url
        if endpoint == "/insights/meta":
            return {"periods": {"A": [str(y) for y in range(1999, 2026)], "M": ["2026-06"]}}, url
        if endpoint == "/insights/ranking/records" and str(params.get("product") or "").startswith("8517"):
            return [], url      # no smartphone line before 2022
        if endpoint == "/insights/ranking":
            parent, flow = params.get("parent"), params.get("flow", "EXPORT")
            if params.get("by") == "product" and parent == "84":
                items = [{"code": c, "name": r["label"], "value": VALUES[(c, flow)], "share": 0.1}
                         for c in ("8428", "8471") for r in CODELIST if r["code"] == c and (c, flow) in VALUES]
                return {"period": params.get("period"), "total": VALUES.get(("84", flow)), "items": items}, url
            if params.get("by") == "partner":
                return {"period": params.get("period"), "total": 2885716872.0, "items": [
                    {"code": "JP", "name": "Japan", "value": 9.8e8, "share": 0.339},
                    {"code": "DE", "name": "Germany", "value": 6e8, "share": 0.2}]}, url
            return {"period": params.get("period"), "total": 1.0e10, "items": [
                {"code": "84", "name": OFFICIAL_84, "value": 2885716872.0, "share": 0.115}]}, url
        if endpoint == "/insights/movers":
            return {"period": "2025", "previous": "2024", "threshold": 1e-05, "total": 1.6e10, "previous_total": 1.5e10,
                    "up": [{"code": "85171300", "name": "Smartphones for wireless networks", "previous": 4.2e7,
                            "value": 8.98e8, "change": 8.56e8, "growth": 20.24},
                           {"code": "71081200", "name": "Gold", "previous": 1e7, "value": 1.3e8, "change": 1.2e8,
                            "growth": 12.0},
                           {"code": "72163390", "name": "H sections of iron or non-alloy steel", "previous": 5e8,
                            "value": 6e8, "change": 1e8, "growth": 0.2},
                           {"code": "99999999", "name": "New line", "previous": 0, "value": 5e7, "change": 5e7,
                            "growth": None}], "down": []}, url
        if endpoint == "/insights/ladder":
            return {"n_items": 6567, "ladder": [{"threshold": 0.001, "items": 165, "coverage": 0.76},
                                                {"threshold": 1e-05, "items": 2016, "coverage": 0.974}]}, url
        raise InsightsError(404, "not in the fake", url)


def boxes_for(api, store):
    out = {}
    for role in ROLES + ("general",):
        pen = role if role in ROLES else "visualizer"
        box = Toolbox("fake:", pen, api=api, view=StubViewClient(store, pen))
        box.view.lookup_names = False
        box._meta = {}                 # validate with the default dimensions; the last full year is 2025
        out[role] = box
    return out


@pytest.fixture
def world():
    api = FakeInsights()
    store = LocalView()
    return api, store, boxes_for(api, store)


def call(box, name, **args):
    tool = next(t for t in box.tools([name]))
    out = tool.call(args)
    return json.loads(out) if out.startswith(("{", "[")) else {"text": out}


# --------------------------------------------------------------------------- 1. chapters by their short names
def test_every_chapter_has_a_short_name_and_84_is_machinery():
    chapters = [f"{i:02d}" for i in range(1, 98) if i != 77]
    assert all(c in HS_SHORT for c in chapters)
    assert HS_SHORT["84"] == "Machinery" and HS_SHORT["85"] == "Electrical machinery"
    assert HS_SHORT["87"] == "Vehicles" and HS_SHORT["27"] == "Mineral fuels"
    assert chapter_name("84") == "machinery (HS 84)" and chapter_name("84", lower=False) == "Machinery (HS 84)"
    assert not any("nuclear" in v.lower() or "reactor" in v.lower() for v in HS_SHORT.values())


def test_the_tools_never_show_the_model_an_official_chapter_title():
    ranking = {"period": "2025", "total": 1e10, "items": [
        {"code": "84", "name": OFFICIAL_84, "value": 2.89e9, "share": 0.1},
        {"code": "85", "name": "ELECTRICAL MACHINERY AND EQUIPMENT ...; SOUND RECORDERS", "value": 2e9, "share": 0.1},
        {"code": "87", "name": "VEHICLES OTHER THAN RAILWAY OR TRAMWAY ROLLING STOCK", "value": 1e9, "share": 0.1},
        {"code": "28", "name": "INORGANIC CHEMICALS; ... OF RADIOACTIVE ELEMENTS", "value": 1e8, "share": 0.01}]}
    names = [i["name"] for i in summarize("/insights/ranking", ranking)["items"]]
    assert names == ["Machinery", "Electrical machinery", "Vehicles", "Inorganic chemicals"]
    movers = {"period": "2025", "previous": "2024", "up": [{"code": "84", "name": OFFICIAL_84, "previous": 1e9,
                                                            "value": 2e9, "change": 1e9, "growth": 1.0}], "down": []}
    assert summarize("/insights/movers", movers)["up"][0]["name"] == "Machinery"
    tree = {"period": "2025", "total": 1e10, "children": [{"code": "84", "name": OFFICIAL_84, "value": 2e9,
                                                           "share": 0.2, "children": []}]}
    assert summarize("/insights/tree", tree)["children"][0]["name"] == "Machinery"
    blob = json.dumps([summarize("/insights/ranking", ranking), summarize("/insights/movers", movers)]).lower()
    assert "nuclear" not in blob and "reactor" not in blob and "railway" not in blob


# --------------------------------------------------------------------------- 2. 'nuclear' is HS 8401
def test_nuclear_maps_to_8401_and_says_it_was_not_traded(world):
    api, store, boxes = world
    vz = boxes["visualizer"]
    vz.begin("I want a map of import of luxembourg of nuclear")
    out = call(vz, "find_codes", words="nuclear", kind="product", flow="IMPORT")
    assert out["best"] == "8401" and out["matches"][0]["imports_2025"] == "none recorded"
    assert out["matches"][0]["traded"] is False
    assert all(m["code"] != "84" for m in out["matches"])          # chapter 84 is not 'nuclear'
    assert out["not_this"][0]["code"] == "84" and "machinery" in out["not_this"][0]["name"]
    assert "recorded no imports of nuclear reactors or fuel elements (HS 8401) in 2025" in out["no_trade"]
    assert vz.turn.no_trade and vz.turn.no_trade[0]["codes"] == ["8401"]
    assert "nuclear reactors" not in json.dumps(out["not_this"]).split("why")[0].lower()


def test_goods_whose_chapter_says_them_map_to_the_chapter(world):
    _, _, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Where do steel exports go?")
    out = call(vz, "find_codes", words="steel", kind="product", flow="EXPORT")
    assert out["best"] == "72" and out["matches"][0]["name"] == "Iron and steel"
    assert out["matches"][0]["exports_2025"] == "€3.1 bn" and "no_trade" not in out


def test_a_question_about_untraded_goods_gets_the_plain_answer_and_no_chart(world):
    api, store, boxes = world

    class NoLLM:
        name = "none"

        def predict(self, *a, **k):
            raise AssertionError("the router LLM is not needed")

        def predict_with_tools(self, *a, **k):
            raise AssertionError("no specialist LLM is needed")

    before = copy.deepcopy(store.spec)
    c = Conversation("fake:", NoLLM(), boxes=boxes, router_llm=NoLLM())
    replies = list(c.answer("I want a map of import of luxembourg of nuclear"))
    assert len(replies) == 1 and replies[0].agent == "visualizer"
    text = replies[0].text
    assert text.startswith("**Visualizer.** Luxembourg recorded no imports of nuclear reactors or fuel elements "
                           "(HS 8401) in 2025, so there is nothing to draw on a map.")
    assert "Chapter 84, machinery, is a different thing" in text
    assert "lifting, handling, loading or unloading machinery (€771 m)" in text and "€2.89 bn in all" in text
    assert "_Next: Show imports of machinery by partner on a map?_" in text
    assert not replies[0].panels and not replies[0].marks
    assert store.spec["panels"] == before["panels"] and store.spec["marks"] == before["marks"]   # no chart


def test_traded_goods_with_a_heading_of_their_own_go_to_the_specialists_with_the_code(world):
    api, store, boxes = world
    VALUES[("8401", "IMPORT")] = 5e6
    try:
        c = Conversation("fake:", None, boxes=boxes)
        g = c.asked_goods("Show imports of nuclear reactors")
        assert g["codes"] == ["8401"] and not g["no_trade"] and g["chapter"] == "84"
    finally:
        del VALUES[("8401", "IMPORT")]


def test_a_forecast_year_is_checked_on_the_last_full_year(world):
    """'What can we expect ... in 2026?' (15 Oct: 'Luxembourg recorded no exports ... in 2026', no forecast): the
    goods are looked up in the last full year, never in the year to forecast."""
    api, store, boxes = world
    VALUES[("8401", "IMPORT")] = 5e6
    try:
        g = Conversation("fake:", None, boxes=boxes).asked_goods("What can we expect for imports of nuclear "
                                                                 "reactors in 2026?")
        assert g["year"] == "2025" and not g["no_trade"]
    finally:
        del VALUES[("8401", "IMPORT")]


def test_both_flows_mean_bought_or_sold(world):
    """find_codes(flow='BOTH') for goods Luxembourg sold (15 Oct: 'recorded no imports or exports of smartphones',
    because the data has no flow BOTH): traded."""
    _, _, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Where do steel exports go?")
    out = call(vz, "find_codes", words="steel", kind="product", flow="BOTH")
    assert out["best"] == "72" and "no_trade" not in out
    assert vz.traded("72", "BOTH", "2025") == vz.traded("72", "EXPORT", "2025") + (vz.traded("72", "IMPORT", "2025") or 0)


def test_the_flows_in_words_take_both():
    assert products.flows_words(["BOTH"]) == "imports or exports"
    assert products.flows_words(["IMPORT", "EXPORT", "BOTH"]) == "imports or exports"
    assert products.no_trade_sentence("smartphones", ["85171300"], ["BOTH"], "2025").startswith(
        "Luxembourg recorded no imports or exports of smartphones (CN8 85171300)")


# --------------------------------------------------------------------------- 3. the reply and page checks
USER_REPLY = ("Luxembourg imported €2.89 bn of nuclear reactors in 2025; Japan was the largest source, supplying "
              "33.9%. I marked Japan, Germany and Belgium on the import map [4].")


def test_the_check_catches_nuclear_reactors_for_chapter_84_only():
    found = products.misnamed(USER_REPLY, ["84"])
    assert [f["phrase"] for f in found] == ["nuclear reactors"] and found[0]["name"] == "machinery (HS 84)"
    assert products.rename(USER_REPLY, ["84"]).startswith("Luxembourg imported €2.89 bn of machinery (HS 84) in 2025")
    assert not products.misnamed(USER_REPLY, ["8401"])          # the heading that is about it
    assert not products.misnamed("Luxembourg recorded no imports of nuclear reactors in 2025.", ["84"])
    assert products.rename("Exports of vehicles other than railway or tramway rolling stock", ["87"]) == \
        "Exports of vehicles (HS 87)"
    assert products.rename("Imports of Nuclear reactors, boilers, machinery and mechanical appliances; parts thereof "
                           "by partner", ["84"]) == "Imports of machinery (HS 84) by partner"
    assert products.misnamed("Imports of sound recorders rose.", ["85"])
    assert not products.misnamed("Imports of machinery rose.", ["84"])


def test_a_reply_naming_84_nuclear_reactors_is_rewritten_once_then_fixed_in_code(world):
    api, store, boxes = world
    vz = boxes["visualizer"]
    turn = vz.begin("I want a map of import of luxembourg of machinery")
    vz.get("/insights/ranking", by="partner", period="2025", flow="IMPORT", product="84")
    assert turn.products == ["84"]
    issues = honesty_issues("visualizer", USER_REPLY, turn, vz)
    assert any("\"nuclear reactors\" is not what HS 84 holds" in i and "machinery (HS 84)" in i for i in issues)
    fixed = honest_in_code("visualizer", "q", USER_REPLY, turn, vz, None)
    assert "nuclear" not in fixed.lower() and "€2.89 bn of machinery (HS 84)" in fixed

    # through the loop: the model is asked once, keeps it, and the code fixes it
    class Stubborn:
        name = "stub"

        def __init__(self):
            self.asked = []

        def predict_with_tools(self, messages, tools, parameters=None, system_message=None):
            self.asked.append(messages[-1]["content"])
            from types import SimpleNamespace as NS
            if len(self.asked) == 1:
                return NS(text=None, tool_calls=[NS(id="c0", name="get_ranking", arguments={
                    "by": "partner", "period": "2025", "flow": "IMPORT", "product": "84"})])
            return NS(text=USER_REPLY.replace(" [4]", ""), tool_calls=None)

    llm = Stubborn()
    reply = run_specialist("general", "imports of machinery by partner", llm, boxes["general"], max_steps=6)
    assert any("is not what HS 84 holds" in a for a in llm.asked if isinstance(a, str))
    assert "nuclear" not in reply.text.lower() and "machinery (HS 84)" in reply.text


def test_the_page_never_names_84_nuclear_and_never_draws_untraded_goods(world):
    api, store, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Show imports of machinery by partner on a map")
    out = call(vz, "show_panel", type="choropleth", id="map_imports", title="Imports of nuclear reactors by partner",
               flow="IMPORT", product="84", measure="share")
    assert out["ok"], out
    p = next(p for p in store.spec["panels"] if p["id"] == "map_imports")
    assert p["title"] == "Imports of machinery (HS 84) by partner"
    assert "'nuclear reactors' became 'machinery (HS 84)'" in out["names"]
    t = call(vz, "set_title", title="Nuclear reactors: where Luxembourg buys them")
    assert t["ok"] and store.spec["title"].startswith("Machinery (HS 84): where Luxembourg buys them")

    vz.begin("I want a map of import of luxembourg of nuclear")
    call(vz, "find_codes", words="nuclear", kind="product", flow="IMPORT")
    refused = call(vz, "show_panel", type="choropleth", id="map_nuclear", title="Imports of nuclear reactors",
                   flow="IMPORT", product="8401", measure="share")
    assert refused["ok"] is False and "recorded no imports of nuclear reactors" in refused["error"]
    named = call(vz, "show_panel", type="choropleth", id="map_nuclear", title="Nuclear imports by partner",
                 flow="IMPORT", measure="share")
    assert named["ok"] is False and "Nothing on the page may be named after them" in named["error"]
    assert not any(p["id"] == "map_nuclear" for p in store.spec["panels"])


def test_a_reply_that_hides_untraded_goods_gets_the_plain_sentence(world):
    api, store, boxes = world
    vz = boxes["visualizer"]
    turn = vz.begin("imports of nuclear")
    call(vz, "find_codes", words="nuclear", kind="product", flow="IMPORT")
    text = "Here is the map of machinery imports (HS 84) [1]."
    assert any("Say plainly first" in i for i in honesty_issues("visualizer", text, turn, vz))
    fixed = honest_in_code("visualizer", "imports of nuclear", text, turn, vz, None)
    assert fixed.startswith("Luxembourg recorded no imports of nuclear reactors or fuel elements (HS 8401) in 2025.")


# --------------------------------------------------------------------------- 4. going deep
def test_movers_at_cn8_rank_by_growth_and_say_the_significance(world):
    _, _, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("Which CN8 lines grew fastest in exports in 2025?")
    out = call(tf, "get_movers", by="product", period="2025", previous="2024", flow="EXPORT", level=4,
               rank_by="growth", limit=3)
    # gold grew from €10 m, below 0.1% of the year before: not ranked; a new line has no growth rate
    assert [u["code"] for u in out["up"]] == ["85171300", "72163390"]
    assert out["up"][0]["growth"] == "21.2 times the 2024 level"   # never 'times higher' (read as N + 1)
    sig = out["significance"]
    assert sig["lines_in_all"] == 6567 and sig["lines_above_it"] == 2016 and sig["threshold"] == "0.001% of the flow"
    assert sig["they_cover"] == "97.4%"


def test_a_bar_is_never_titled_as_growth_and_a_treemap_never_one_chapter(world):
    _, store, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("Which CN8 lines grew fastest in exports in 2025?")
    bar = call(tf, "show_panel", type="bar", title="Fastest-growing CN8 export lines in 2025", by="product",
               flow="EXPORT", level=4)
    assert bar["ok"] is False and "waterfall" in bar["error"]
    vz = boxes["visualizer"]
    vz.begin("Which electrical goods does Luxembourg export?")
    tm = call(vz, "show_panel", type="treemap", title="Electrical machinery exports", flow="EXPORT", product="85")
    assert tm["ok"] is False and "parent='85'" in tm["error"]


# --------------------------------------------------------------------------- 5. codes that changed, forecasts, words
def test_a_young_code_says_where_its_goods_were_before(world):
    _, _, boxes = world
    vz = boxes["visualizer"]
    vz.begin("I want the data of smartphone in 2015")
    out = call(vz, "get_series", flow="EXPORT", freq="A", start="2015", end="2015", product="85171300")
    h = out["code_history"]
    assert h["code_starts"] == "2022" and "85171200" in h["say"]
    assert h["before_2022"]["values"]["2015"] == "€2.14 bn"


def test_never_an_empty_chart_and_a_chained_line_for_a_young_code(world):
    _, store, boxes = world
    vz = boxes["visualizer"]
    vz.begin("I want the data of smartphone in 2015")
    empty = call(vz, "show_panel", type="bar", title="Smartphone exports in 2015", by="partner", flow="EXPORT",
                 product="85171300", period="2015")
    assert empty["ok"] is False and "nothing to draw" in empty["error"] and "85171200" in empty["error"]
    vz.begin("show smartphone exports since 2015")
    out = call(vz, "show_panel", type="line", id="phones", title="Mobile phone exports since 2015", flow="EXPORT",
               product="85171300", start="2015")
    assert out["ok"], out
    p = next(p for p in store.spec["panels"] if p["id"] == "phones")
    assert [q["params"]["product"] for q in p["queries"]] == ["85171200", "85171300"]
    assert p["queries"][0]["params"]["end"] == "2021" and p["queries"][1]["params"]["start"] == "2022"
    assert "CN8 85171200" in p["queries"][0]["label"] and "from 2022" in p["queries"][1]["label"]
    mark = next(m for m in store.spec["marks"] if m["target"] == "phones")
    assert mark["kind"] == "annotation" and mark["params"]["x"] == "2022" and out["code_change"]["marks"]


def test_a_line_with_no_tested_forecast_falls_back_in_one_call(world):
    _, _, boxes = world
    fc = boxes["forecaster"]
    fc.begin("what are the trends of smartphone in 2025 and what is the plan for the 2 next year")
    out = call(fc, "get_forecast", flow="EXPORT", product="85171300")
    assert out["forecast_of"] == "electrical machinery (HS 85)" and "no tested forecast" in out["asked_for"]["no_forecast"]
    assert [f["period"] for f in out["forecast"]] == ["2026", "2027"]          # the next two years: horizon 24
    assert out["scenarios"] and "Do not call get_forecast again" in out["instead"]


def test_a_failing_tool_is_not_retried_and_the_last_step_answers(world):
    _, _, boxes = world
    from types import SimpleNamespace as NS

    class Looper:
        name = "loop"

        def __init__(self):
            self.tools_offered, self.seen = [], []

        def predict_with_tools(self, messages, tools, parameters=None, system_message=None):
            self.tools_offered.append(len(tools))
            if messages[-1]["role"] == "tool":
                self.seen.append(messages[-1]["content"])
            if tools:
                return NS(text=None, tool_calls=[NS(id=f"c{len(self.seen)}", name="get_flows",
                                                    arguments={"period": "2025"})])
            return NS(text="I could not read that series; here is what I have.", tool_calls=None)

    llm = Looper()
    reply = run_specialist("general", "q", llm, boxes["general"], max_steps=5)
    assert llm.tools_offered[-1] == 0 and "ran out of steps" not in reply.text
    assert "already failed twice" in llm.seen[-1]


def test_replies_use_multiples_names_and_plain_words():
    from lux_insights import fmt
    from lux_insights.specialists import plain_words
    assert fmt.house_style("from €42 m to €898 m (+2024%)") == "from €42 m to €898 m (×21.2)"
    assert fmt.house_style("exports rose +8.6%") == "exports rose 8.6%"   # the verb says the direction once
    text = plain_words("Bought from China (CN), sold to FR. Source: /insights/series?flow=EXPORT&product=85171300.")
    assert text == "Bought from China, sold to France. Source: Eurostat trade data (ds-045409)."


def test_goods_asked_for_before_their_code_existed_get_the_earlier_code(world):
    _, _, boxes = world
    CODELIST.append({"code": "85171300", "label": "Smartphones for wireless networks", "level": 4})
    try:
        vz = boxes["visualizer"]
        vz.begin("I want the data of smartphone in 2015")
        out = call(vz, "find_codes", words="smartphone", kind="product", period="2015", flow="EXPORT")
        m = next(m for m in out["matches"] if m["code"] == "85171300")
        assert m["code_starts"] == "2022" and m["in_2015"]["filed_under"].startswith("CN8 85171200")
        assert m["in_2015"]["exports"] == "€2.14 bn"
        assert "no_trade" not in out and not vz.turn.no_trade          # never "no trade" for a code not yet made
    finally:
        CODELIST.pop()


def test_a_band_of_the_chapter_never_sits_on_the_line_of_the_product(world):
    _, store, boxes = world
    fc = boxes["forecaster"]
    fc.begin("what is the plan for smartphones next year")
    assert call(fc, "show_panel", type="line", id="phones", title="Mobile phone exports", flow="EXPORT",
                product="85171300", start="2015")["ok"]
    call(fc, "get_forecast", flow="EXPORT", product="85171300")
    named = call(fc, "add_forecast_band", flow="EXPORT", product="85171300", target="phones",
                 note="Smartphone exports in 2026: {value}.")
    assert named["ok"] is False and "not of smartphones" in named["error"]      # the band is chapter 85's
    band = call(fc, "add_forecast_band", flow="EXPORT", product="85171300", target="phones",
                note="Electrical machinery exports in 2026: {value}.")
    assert band["ok"], band
    mark = next(m for m in store.spec["marks"] if m["kind"] == "forecast")
    line = next(p for p in store.spec["panels"] if p["id"] == mark["target"])
    assert mark["target"] != "phones" and [q["params"]["product"] for q in line["queries"]] == ["85"]
    assert line["title"].startswith("Electrical machinery (HS 85)")


def test_a_reply_about_smartphones_says_their_earlier_history(world):
    _, _, boxes = world
    tf = boxes["trend_finder"]
    turn = tf.begin("what are the trends of smartphone in 2025")
    tf.get("/insights/series", flow="EXPORT", freq="A", start="2022", end="2025", product="85171300")
    text = honest_in_code("trend_finder", "what are the trends of smartphone in 2025",
                          "Smartphone exports rose to €898 m in 2025 [1].", turn, tf, None)
    assert "not unprecedented" in text and "€2.14 bn in 2015 and €1.47 bn in 2019" in text and "CN8 85171200" in text
    # a question about who sold or bought them is not about their level: no history sentence
    q = "Draw a map of partners for smartphones (CN8 85171300) in 2025, with the thread: bought from China, sold to France"
    turn = tf.begin(q)
    tf.get("/insights/series", flow="EXPORT", freq="A", start="2022", end="2025", product="85171300")
    text = honest_in_code("trend_finder", q, "China supplied 87.5% of the smartphones bought in 2025 [1].", turn, tf, None)
    assert "not unprecedented" not in text


def test_one_next_line_and_no_analyst_disclaims_the_forecast(world):
    from lux_insights.specialists import fix_next
    t = "**LuxInsights.** In 2015 they were filed elsewhere. _Next: How did they change?_\n\n_Next: Why did exports jump?_"
    out = fix_next(t, "general", ["I want the data of smartphone in 2015"])
    assert out.count("_Next:") == 1 and "\n\n_Next:" in out
    _, _, boxes = world
    tf = boxes["trend_finder"]
    turn = tf.begin("what are the trends of smartphone in 2025 and what is the plan for the 2 next year")
    text = honest_in_code("trend_finder", "q", "Exports rose in 2024 [1]. I can't give a reliable plan or forecast for "
                          "the next two years from these figures.", turn, tf, None)
    assert "forecast" not in text and text.startswith("Exports rose in 2024 [1].")
