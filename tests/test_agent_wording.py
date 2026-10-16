# SPDX-License-Identifier: MIT
"""The agent's wording and speed, from the review of 15 Oct 2026 (30 questions, every figure right).

1. A code removed for a reader who reads plain names leaves no "(, which ..."; the journalist's reply always ends
   with a quotable sentence and the source line.
2. The insights client caches the data's GET answers for the whole process (a warm-up).
3. The forecast's scenarios are said as the smartphone line's level, never "one-off".
4. A year beyond the 12 months tested is said to be less certain; the horizon never exceeds what the API allows.
5. Ratios never "N times higher"; no double signs ("fell −17.4%", "rose +8.6%"); thousands separators.
6. The earlier mobile-phone code is all mobile phones, never "smartphone exports".
7. Events next to the breaks: months in words, no whole-year change next to a broad event.
8. Ranks in a reply are checked against the rankings of the turn.
9. Small ones: a line after a bullet, odd follow-ups, "I drew" what was already there, the French reply.

No network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("httpx")
import httpx  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "app" / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents.kit import insights as kit_insights  # noqa: E402
from agents.kit import InsightsClient, InsightsError, source_url  # noqa: E402
from agents.kit.personas import SOURCE_LINE, profile  # noqa: E402
from agents.kit.testing import LocalView, StubViewClient  # noqa: E402
from lux_insights import fmt, products, record, router  # noqa: E402
from lux_insights import specialists as sp  # noqa: E402
from lux_insights.tools import (Toolbox, Turn, plain_signs, ranks_shown, scenarios_words,  # noqa: E402
                                title_about)

BLOCKER = ("**Trend finder.** Exports rose +8.6% in 2025, and smartphones accounted for 66.5% of the increase. "
           "The 2025 level is not unprecedented: under the earlier code (CN8 85171200, which held smartphones with "
           "other mobile phones until 2021), mobile-phone exports were €2.14 bn in 2015 and €1.47 bn in 2019.\n\n"
           "_Next: Which partners drove the smartphone export rise?_")


# --------------------------------------------------------------------------- 1. codes, quote, source
def test_a_removed_code_leaves_no_empty_bracket():
    out = sp.without_codes(BLOCKER)
    assert "(," not in out and "( ," not in out and "85171200" not in out
    assert "(which held smartphones with other mobile phones until 2021)" in out
    for bad, good in [("x (, y)", "x (y)"), ("x ( , y)", "x (y)"), ("x (; y)", "x (y)"), ("x (y ,)", "x (y)"),
                      ("x ( )", "x"), ("x (y, )", "x (y)")]:
        assert sp.tidy_brackets(bad) == good, bad


def test_the_journalist_always_gets_a_quote_and_the_source_line():
    journalist = profile("journalist")
    limits = sp.reader_limits(journalist)
    text = ("**Trend finder.** Exports rose 8.6% in 2025, with smartphones accounting for 66.5% of the increase; "
            "I marked the route [1]. The 2025 level is not unprecedented.\n\n_Next: Which partners drove it?_")
    out = sp.with_citation(sp.fit_reader(text, limits), journalist)
    assert "“Exports rose 8.6% in 2025, with smartphones accounting for 66.5% of the increase.”" in out
    assert out.count(SOURCE_LINE) == 1
    assert out.index(SOURCE_LINE) < out.index("_Next:")          # the source line, then the follow-up
    # the model's own quote and an italic source line are kept, the line said exactly once
    own = ("**Trend finder.** Exports rose 8.6% in 2025. “Smartphones drove the rise.”\n\n"
           "_Source: Eurostat ds-045409, data produced by STATEC._\n\n_Next: What next?_")
    out = sp.with_citation(own, journalist)
    assert out.count("“") == 1 and out.count("Source:") == 1 and SOURCE_LINE in out
    # an analyst gets neither; a reply without figures gets no source line
    assert sp.with_citation(text, profile("statec_analyst")) == text
    plain = "**Visualizer.** I cleared the page.\n\n_Next: Where do the goods go?_"
    assert sp.with_citation(plain, journalist) == plain


# --------------------------------------------------------------------------- 2. the process-wide cache
def _client(calls: list, status: int = 200, base: str = "http://cache-test"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if status >= 400:
            return httpx.Response(status, json={"detail": "nope"})
        return httpx.Response(200, json={"items": [{"code": "DE", "value": 1.0}]})
    c = InsightsClient(base)
    c._http = httpx.Client(base_url=base, transport=httpx.MockTransport(handler))
    return c


def test_the_insights_client_caches_reads_for_the_process():
    kit_insights.clear_cache()
    calls: list = []
    a, b = _client(calls), _client(calls)
    d1, url1 = a.get("/insights/movers", by="product", period="2025", previous="2024", parent="85")
    d1["items"].append("edited by a caller")                    # the cache keeps its own copy
    d2, url2 = b.get("/insights/movers", parent="85", previous="2024", period="2025", by="product")
    assert len(calls) == 1 and d2 == {"items": [{"code": "DE", "value": 1.0}]}   # another client, params reordered
    assert url1 != url2   # the source URL still says the call as made
    a.get("/insights/movers", by="product", period="2025", previous="2024", parent="8517")
    assert len(calls) == 2                                      # other params: a new call
    assert kit_insights.cacheable("/insights/forecast") and kit_insights.cacheable("/trends/analyze")
    assert not kit_insights.cacheable("/view") and not kit_insights.cacheable("/view/ops")
    kit_insights.clear_cache()


def test_errors_are_never_cached():
    kit_insights.clear_cache()
    calls: list = []
    bad = _client(calls, status=400, base="http://cache-errors")
    for _ in range(2):
        with pytest.raises(InsightsError):
            bad.get("/insights/forecast", horizon=60)
    assert len(calls) == 2
    kit_insights.clear_cache()


def test_entries_expire(monkeypatch):
    kit_insights.clear_cache()
    calls: list = []
    c = _client(calls, base="http://cache-expire")
    c.get("/insights/meta")
    monkeypatch.setattr(kit_insights, "CACHE_SECONDS", 0.0)
    assert not kit_insights.cacheable("/insights/meta")         # 0 turns the cache off
    c.get("/insights/meta")
    assert len(calls) == 2
    kit_insights.clear_cache()


# --------------------------------------------------------------------------- 3. scenarios as the line's level
SCENARIOS = [{"id": "smartphones_stops", "case": "stops", "line_label": "the smartphone line (CN 8517 13 00)",
              "since": "2025-09", "recent_extra": 50673700.15, "rows": [{"period": "2026", "value": 16733310271.2}]},
             {"id": "smartphones_continues", "case": "continues",
              "line_label": "the smartphone line (CN 8517 13 00)", "since": "2025-09", "recent_extra": 50673700.15,
              "rows": [{"period": "2026", "value": 17037352472.1}]}]


def test_the_scenarios_are_said_as_the_lines_level():
    said = scenarios_words(SCENARIOS, "2026", 17344395327.1)
    assert said == ("If the smartphone line, which started in September 2025, stays at its recent level of about "
                    "€51 m a month, the forecast for 2026 is €17 bn; if it stops, €16.7 bn. The model's central "
                    "€17.3 bn is above both: it assumes the line's recent rise goes on.")
    assert "between the two" in scenarios_words(SCENARIOS, "2026", 16.9e9)
    assert scenarios_words(SCENARIOS[:1], "2026", 17e9) is None


def test_the_forecasters_reply_drops_one_off_and_says_the_scenarios_after_the_interval():
    turn = Turn("forecaster")
    turn.scenarios = scenarios_words(SCENARIOS, "2026", 17344395327.1)
    text = ("**Forecaster.** Exports are forecast at €17.3 bn in 2026, with an 80% interval of €16.6 bn to €18.1 bn. "
            "The forecast averages two methods. The model partly repeats the one-off smartphone line; if it stops, "
            "the scenario is €16.7 bn, and if it continues, €17 bn. Those scenarios are a range of plausible values, "
            "not a prediction.\n\n_Next: Which method works best?_")
    out = sp.forecast_said_right(text, turn)
    assert "one-off" not in out and "plausible" not in out
    assert ("€18.1 bn. If the smartphone line, which started in September 2025, stays at its recent level of about "
            "€51 m a month") in out
    assert out.index("€51 m") < out.index("The forecast averages")


# --------------------------------------------------------------------------- 4. years beyond the tested horizon
class FakeForecast(InsightsClient):
    def __init__(self):
        self.base_url, self.calls = "fake:", []

    def close(self) -> None:
        pass

    def get(self, endpoint, **params):
        url = source_url(endpoint, params)
        self.calls.append((endpoint, dict(params)))
        if endpoint == "/insights/meta":
            return {"periods": {"A": [str(y) for y in range(1999, 2026)], "M": ["2026-05", "2026-06"]}}, url
        if endpoint == "/insights/forecast":
            h = int(params["horizon"])
            if h > 36:
                raise InsightsError(400, "horizon is a number of months from 1 to 36", url)
            years = [str(y) for y in range(2026, 2026 + (6 + h) // 12)]
            rows = [{"period": "2025", "value": 16.25e9}] + [
                {"period": y, "value": 17e9 + i * 1e9, "low": 16e9, "high": 18e9 + i * 1e9} for i, y in enumerate(years)]
            return {"method": "ensemble", "method_label": "ensemble", "interval": 0.8, "rows": rows,
                    "scenarios": SCENARIOS}, url
        raise InsightsError(404, "unknown", url)


def _box(agent: str = "forecaster") -> tuple[Toolbox, FakeForecast]:
    api = FakeForecast()
    return Toolbox("fake:", agent, api=api, view=StubViewClient(LocalView(), agent)), api


def test_a_year_far_ahead_caps_the_horizon_and_is_said_less_certain():
    box, api = _box()
    box.begin("What will exports be in 2030?")
    out = box.get_forecast("EXPORT", horizon=60)
    horizons = [p["horizon"] for e, p in api.calls if e == "/insights/forecast"]
    assert horizons == [36]                                     # capped in code: never a 400
    assert "2028 at most" in out["furthest_year"] and "not 2030" in out["furthest_year"]
    assert "12 months ahead only" in out["tested_up_to"] and box.turn.beyond_tested
    text = sp.forecast_said_right("**Forecaster.** Exports in 2028: €19 bn.\n\n_Next: What else?_", box.turn)
    assert "tested up to 12 months ahead only, so the later years are less certain" in text
    # 'What should we expect for 2026?': one year, no such sentence
    box.begin("What should we expect for 2026?")
    box.get_forecast("EXPORT")
    assert not box.turn.beyond_tested
    assert "If the smartphone line, which started in September 2025" in box.turn.scenarios


def test_the_band_names_the_year_it_shows():
    box, _ = _box()
    box.begin("What will exports be in 2030?")
    box.get_forecast("EXPORT", horizon=36)
    spec = {"version": 1, "marks": [], "panels": [{"id": "trend", "type": "line", "queries": [
        {"key": "exports", "endpoint": "/insights/series", "params": {"flow": "EXPORT", "freq": "A"}}]}]}
    sent = {}

    def commit(build, message, check_facts=True):
        sent["ops"], sent["items"] = build(spec)
        return {"ok": True, "message": message}
    box.commit = commit
    out = box.add_forecast_band("EXPORT", note="Exports in 2028: {value}, between {low} and {high}.",
                                outlook="{method}. It was tested against the seasonal naive forecast.")
    assert out["ok"] and "2028 band" in out["message"]
    mark = next(op["mark"] for op in sent["ops"] if op["op"] == "add_mark")
    assert mark["params"]["label"] == "2028 forecast"
    assert mark["facts"]["value"]["path"] == "rows.[period=2028].value"
    text = next(op["panel"]["text"] for op in sent["ops"] if op["op"] == "upsert_panel")
    assert text.startswith("Exports in 2028: {value}, likely between {low} and {high} (the {interval} interval).")
    assert "Holt-Winters" not in text and "seasonal naive" not in text
    with pytest.raises(Exception, match="not 2030"):
        box.add_forecast_band("EXPORT", note="Exports in 2030: {value}.")


# --------------------------------------------------------------------------- 5. ratios, signs, separators
def test_ratios_signs_and_thousands():
    assert fmt.eur(1563.4e9) == "€1,563.4 bn" and fmt.eur(898e6) == "€898 m"
    assert fmt.growth(20.24) == "×21.2" and fmt.growth(20.24, base="2024") == "21.2 times the 2024 level"
    hs = fmt.house_style
    assert hs("€898 m in 2025, 21 times higher than in 2024") == "€898 m in 2025, 21 times the 2024 level"
    assert hs("exports grew 6.1 times higher and imports 14 times larger") == "exports grew ×6.1 and imports ×14"
    assert hs("exports fell −17.4%; imports fell by −3.5%, dropped −€3 m, declined −2%") == \
        "exports fell 17.4%; imports fell 3.5%, dropped €3 m, declined 2%"
    assert hs("Exports rose +8.6%, grew by +2.9%, a change of +€1.29 bn") == \
        "Exports rose 8.6%, grew 2.9%, a change of +€1.29 bn"
    assert hs("Germany €1563.4 bn") == "Germany €1,563.4 bn"
    assert hs("the 2021-12 break, the 2011-12 season") == "the December 2021 break, the 2011-12 season"
    assert [round(v) for _, _, v in fmt.figures_in("€1,563.4 bn and €2,14 bn")] == [1563400000000, 2140000000]


def test_notes_on_the_page_get_no_double_sign():
    mark = {"note": "Exports rose {growth}; imports fell {imp}; balance {bal}.",
            "facts": {"growth": {"op": "growth", "of": ["a", "b"], "format": "pct_signed"},
                      "imp": {"op": "growth", "of": ["c", "d"], "format": "pct_signed"},
                      "bal": {"query": "self", "path": "x", "format": "eur_signed"}}}
    plain_signs(mark)
    assert mark["note"] == "Exports rose {growth_u}; imports fell {imp_abs}; balance {bal}."
    assert mark["facts"]["growth_u"]["format"] == "pct" and mark["facts"]["imp_abs"] == \
        {"op": "abs", "of": ["imp"], "format": "pct"}


# --------------------------------------------------------------------------- 6. the earlier code is mobile phones
def test_the_earlier_code_is_called_mobile_phones():
    pre = products.predecessor("85171300")
    assert pre["before_name"] == "mobile phones (the earlier code, which held smartphones with other mobile phones)"
    assert "never 'smartphones exports'" in pre["say"]
    out = products.earlier_as_now("Smartphone exports were €2.14 bn in 2015 and €36 m in 2021. Smartphone exports "
                                  "were €898 m in 2025.")
    assert out.startswith("Mobile-phone exports (the earlier code, which held smartphones with other mobile phones) "
                          "were €2.14 bn in 2015")
    assert out.endswith("Smartphone exports were €898 m in 2025.")


# --------------------------------------------------------------------------- 7. events next to the breaks
def test_a_broad_event_gets_no_whole_year_change():
    turn = Turn("cause_finder")
    turn.event_breaks = [{"label": "Suez Canal blockage", "at": "2021-03"},
                         {"label": "EU-UK trade agreement", "at": "2021-01"}]
    turn.scoped = [{"label": "EU-UK trade agreement"}]
    text = ("Five events line up with the December 2021 export break: the EU-UK trade agreement (exports to the UK "
            "−14.4%), the Suez Canal blockage (exports +17.3%, imports +19.7%).")
    out = sp.broad_event_totals(text, turn)
    assert "(exports to the UK −14.4%)" in out and "+17.3%" not in out
    assert out.endswith("the Suez Canal blockage.")


# --------------------------------------------------------------------------- 8. ranks
def test_ranks_are_checked_against_the_rankings_of_the_turn():
    ranks = ranks_shown({"rows": [{"code": "GB", "name": "United Kingdom", "1999": 4, "2025": 9},
                                  {"code": "NL", "name": "Netherlands", "1999": 6, "2025": 4}]})
    ok = "The United Kingdom fell from fourth to ninth, while the Netherlands rose from sixth to fourth."
    assert sp.unverified_ranks(ok, ranks) == []
    bad = "China climbed from 13th to seventh in the ranking."
    assert sp.unverified_ranks(bad, ranks) == ["13th", "seventh"]
    assert sp.ranks_in("It was the first time in a decade.") == []
    chk = record.checked(ok, [], [], 0)
    assert chk["in_reply"] == 4 and chk["verified"] == 4 and "4 ranks among them" in chk["words"]


# --------------------------------------------------------------------------- 9. small ones
def test_a_line_after_a_bullet_goes_on_its_own_line():
    out = sp.add_before_tail("Intro:\n- a: 1\n- b: 2\n\n_Next: x?_", "The new chart takes the place of “X”.")
    assert "- b: 2\n\nThe new chart" in out


def test_odd_or_repeated_follow_ups_are_replaced():
    text = "**LuxInsights.** No personal data is used.\n\n_Next: Where does my question go?_"
    assert "my question" not in sp.fix_next(text, "general", ["Is any personal data used here?"])
    text = "**Visualizer.** Cleared.\n\n_Next: What would you like to explore?_"
    assert "would you like" not in sp.fix_next(text, "visualizer", ["Clear the page"])
    long = "_Next: " + " ".join(["word"] * 14) + "?_"
    assert "word word" not in sp.fix_next("**T.** x.\n\n" + long, "trend_finder", ["q"])
    asked = ["When did exports shift for good?"]
    out = sp.fix_next("**T.** x.\n\n_Next: When did exports shift for good since 1999?_", "trend_finder", asked)
    assert "shift for good" not in out


def test_already_there_is_said_so():
    turn = Turn("trend_finder")
    turn.unchanged, turn.drawn_kinds = ["waterfall", "thread"], ["headline"]
    text = "**Trend finder.** Exports rose 8.6%. I drew the waterfall of the change and the smartphone thread [1]."
    out = sp.already_there(text, turn)
    assert "The page already shows the waterfall of the change and the smartphone thread [1]." in out
    turn.drawn_kinds = ["waterfall"]
    assert sp.already_there(text, turn) == text


def test_the_replaced_chart_line_speaks_the_replys_language():
    fr = ("**Trend finder.** Les exportations ont augmenté de 8.6% en 2025, et la hausse vient des smartphones "
          "qui sont allés en France.\n\n_Next: Et les importations?_")
    out = sp.with_replaced(fr, [{"title": "Smartphone exports since 2015"}])
    assert "The new chart" not in out and "Le nouveau graphique remplace “Smartphone exports since 2015”" in out
    en = "**Visualizer.** In 2025 the largest destination was Germany.\n\n_Next: x?_"
    assert "The new chart takes the place of “A”" in sp.with_replaced(en, [{"title": "A"}])


def test_no_routes_through_luxembourg():
    out = sp.no_routes("I marked the smartphone route from China to France [1] and the China-to-France smartphone "
                       "route, through Luxembourg. I drew a routes chart.")
    assert "route" not in out.replace("routes", "") and "through Luxembourg" not in out
    assert "the smartphone thread (bought mostly from China, sold mostly to France) [1]" in out
    assert "a map of partners" in out
    keep = "The data cannot show the route goods take."
    assert sp.no_routes(keep) == keep


def test_the_frequent_questions_are_planned_in_code():
    for q, agent in [("Why did exports jump in 2025?", "trend_finder"),
                     ("When did exports shift for good?", "trend_finder"),
                     ("What should we expect for 2026?", "forecaster"), ("Where do the goods go?", "visualizer")]:
        plan = router.route(q, llm=None)
        assert plan.source == "rule" and plan.agents == [agent], q
    assert router.common_plan("Why did exports jump in 2025, and what comes next?") is None


def test_the_title_is_about_the_waterfall():
    title = "Luxembourg's trade in goods: where the 2025 export rise came from"
    assert title_about(title, "What drove the 2025 export rise")
    assert not title_about(title, "Where Luxembourg's goods go")


def test_the_trend_text_uses_the_analysis_summary():
    class Analysis(FakeForecast):
        def get(self, endpoint, **params):
            if endpoint == "/trends/analyze":
                return {"trend_summary": "Over the span the trend rose.", "seasonality_summary": "Peaks in October.",
                        "direction": "rising", "shape": "steady", "relative_change": None, "changepoints": []}, "u"
            return super().get(endpoint, **params)
    box = Toolbox("fake:", "trend_finder", api=Analysis(), view=StubViewClient(LocalView(), "trend_finder"))
    box.begin("When did exports shift for good?")
    text, facts = box.trend_text({"endpoint": "/trends/analyze", "params": {"flow": "EXPORT"}})
    assert text == "{trend_summary} {seasonality}" and facts["trend_summary"]["path"] == "trend_summary"
    out = box.analyze_trend("EXPORT")
    assert out["change_over_span"] is None and out["trend_summary"] == "Over the span the trend rose."


# --------------------------------------------------------------------------- the flows reviewed on 15 Oct 2026
class FakeSteel(FakeForecast):
    """Exports of iron and steel to the United States, as the API answers: a parent two levels above the level
    asked has no children (a CN8 line's parent is its HS6 line)."""

    def get(self, endpoint, **params):
        url = source_url(endpoint, params)
        self.calls.append((endpoint, dict(params)))
        lines = {"721633": {"72163390": 42045316.0, "72163310": 1.2e6}, "721650": {"72165091": 2.4e5}}
        if endpoint == "/insights/ranking" and params.get("by") == "product":
            parent, level = str(params.get("parent") or ""), int(params.get("level") or 1)
            if parent == "7216" and level == 3:
                items = [{"code": c, "name": f"line {c}", "value": sum(v.values()), "share": 0.1}
                         for c, v in lines.items()]
            elif parent in lines and level == 4:
                items = [{"code": c, "name": "H sections of iron or non-alloy steel" if c == "72163390" else f"line {c}",
                          "value": v, "share": 0.05} for c, v in lines[parent].items()]
            else:
                items = []     # parent=7216 with level=4: empty, as the API answers
            return {"period": params.get("period"), "total": 43835551.0, "items": items}, url
        if endpoint == "/insights/meta":
            return {"periods": {"A": ["2024", "2025"], "M": ["2026-06"]}}, url
        raise InsightsError(404, "unknown", url)


def test_the_cn8_lines_of_a_heading_are_reached_with_the_partner_kept():
    api = FakeSteel()
    box = Toolbox("fake:", "trend_finder", api=api, view=StubViewClient(LocalView(), "trend_finder"))
    box.begin("Drill down from iron and steel to the 8-digit H-section line")
    out = box.get_ranking("product", "2025", "EXPORT", level=4, parent="7216", partner="US")
    assert [i["code"] for i in out["items"]] == ["72163390", "72163310", "72165091"]
    assert out["items"][0]["value"] == "€42 m" and "United States" in out["how"]
    asked = [p for e, p in api.calls if e == "/insights/ranking"]
    assert all(p.get("partner") == "US" for p in asked)          # the partner filter is never dropped
    # a chart of the CN8 lines under 7216: told which HS6 line to draw, never an empty chart
    with pytest.raises(Exception, match="721633"):
        box.show_panel("bar", "H-section lines", by="product", flow="EXPORT", level=4, parent="7216", partner="US")


def test_steel_h_sections_map_to_their_own_line():
    hit = products.specific_in("What has happened to our exports of steel H-sections to the United States?")
    assert hit and hit[0]["headings"] == ["721633", "72163390"]


class FakePeers(FakeForecast):
    """/insights/compare by reporter with a partner, as the backend answers once each country's slice is read
    (``lost``: the countries it could not read from Eurostat)."""

    def __init__(self, lost=()):
        super().__init__()
        self.lost = set(lost)

    def get(self, endpoint, **params):
        url = source_url(endpoint, params)
        if endpoint == "/insights/compare":
            self.calls.append((endpoint, dict(params)))
            rows = [{"code": c, "name": c, "value": 1e9, "share": 0.25} for c in params["group"].split(",")
                    if c not in self.lost]
            return rows + [{"code": c, "name": c, "value": None, "share": None, "loaded": False}
                           for c in sorted(self.lost)], url
        if endpoint == "/insights/series":
            self.calls.append((endpoint, dict(params)))
            return [{"period": str(y), "value": 1e9 + 1e7 * (y - 1999)} for y in range(1999, 2026)], url
        if endpoint == "/insights/meta":
            meta, _ = super().get(endpoint, **params)
            dims = [{"id": d} for d in ("freq", "reporter", "partner", "product", "flow", "indicators")]
            return {**meta, "dimensions": dims}, url
        return super().get(endpoint, **params)


def test_luxembourg_against_other_countries_is_never_a_partner_comparison():
    box, _ = _box("visualizer")
    box.begin("Is this specific to Luxembourg? Compare with Belgium, the Netherlands and Germany")
    with pytest.raises(Exception) as err:
        box.compare("partners", codes=["US", "BE", "NL", "DE"], partner="US")
    assert "peers, not partners" in str(err.value) and "what='peers'" in str(err.value)
    assert "partner=" in str(err.value)
    # each country's own exports to the United States, read (or fetched) by the backend: one line per country
    api = FakePeers()
    box = Toolbox("fake:", "visualizer", api=api, view=StubViewClient(LocalView(), "visualizer"))
    box.view.lookup_names = False   # names from the kit's own list, not the backend
    box.begin("Is this specific to Luxembourg? Compare with Belgium, the Netherlands and Germany")
    out = box.compare("peers", codes=["BE", "NL", "DE"], partner="US")
    asked = [p for e, p in api.calls if e == "/insights/compare"]
    assert asked and asked[0]["partner"] == "US" and asked[0]["group"] == "LU,BE,NL,DE"
    p = next(p for p in box.view.spec()["panels"] if p["id"] == out["panel"])
    assert [q["params"].get("reporter") for q in p["queries"]] == ["LU", "BE", "NL", "DE"]
    assert all(q["params"].get("partner") == "US" for q in p["queries"]) and "to the United States" in p["title"]
    # the backend could not read one country: the plain refusal, total trade instead
    api = FakePeers(lost={"NL"})
    box = Toolbox("fake:", "visualizer", api=api, view=StubViewClient(LocalView(), "visualizer"))
    box.begin("Is this specific to Luxembourg? Compare with Belgium, the Netherlands and Germany")
    with pytest.raises(Exception, match="could not be read"):
        box.compare("peers", codes=["BE", "NL", "DE"], partner="US")
    from lux_insights.tools import asks_peers
    assert asks_peers("Is this specific to Luxembourg? Compare with Belgium, the Netherlands and Germany")
    assert not asks_peers("Compare exports to Germany and France")


def test_a_french_reply_gets_no_english_sentence_and_no_mark_talk():
    assert sp.reply_language("Les exportations ont augmenté de 8.6% en 2025, et la hausse vient des smartphones.") == "fr"
    assert sp.leaked("**Trend finder.** I couldn’t add a numbered mark this time, so there’s no new mark to cite. "
                     "Exports rose 8.6%.")


def test_a_follow_up_on_other_countries_trade_with_one_partner_is_kept():
    # the data holds each country's own trade with one partner now: a real next question
    text = "**Visualizer.** Total exports.\n\n_Next: Compare exports to the United States across these countries?_"
    out = sp.fix_next(text, "visualizer", ["Is this specific to Luxembourg? Compare with Belgium"])
    assert "across these countries" in out
    assert sp.plain_words("Germany (HS 72163390) was the largest market; Singapore (72163390) grew.") == \
        "Germany was the largest market; Singapore grew."


def test_a_hanging_router_falls_back_to_the_questions_words(monkeypatch):
    import time as _time

    class Hanging:
        name = "slow"

        def predict(self, *a, **k):
            _time.sleep(5)
            return "{}"
    monkeypatch.setattr(router, "ROUTER_SECONDS", 0.2)
    t0 = _time.monotonic()
    plan = router.route("Why did exports jump so much in 2025?", Hanging())
    assert plan.source == "keywords" and _time.monotonic() - t0 < 2


def test_the_specialists_llm_client_is_bounded():
    class Client:
        def with_options(self, **kw):
            self.kw = kw
            return self

    class LLM:
        client = Client()
    llm = LLM()
    sp.bounded(llm)
    assert llm.client.kw == {"timeout": sp.LLM_SECONDS, "max_retries": 1} and llm._lux_bounded


def test_a_sentence_written_twice_is_said_once():
    from lux_insights.specialists import no_repeated_sentences
    text = ("**Forecaster.** Exports in 2026: €17.3 bn. The central figure assumes its recent rise goes on. "
            "If the line stops: €16.7 bn. The central figure assumes the recent rise goes on.\n\n"
            "_“Exports in 2026: €17.3 bn.”_\n\nSource: Eurostat ds-045409, data produced by STATEC.")
    out = no_repeated_sentences(text)
    assert out.count("assumes") == 1 and "If the line stops" in out
    assert "_“Exports in 2026: €17.3 bn.”_" in out and out.endswith("data produced by STATEC.")


def test_a_short_question_without_a_subject_follows_the_last_one():
    from lux_insights.conversation import Conversation, has_subject
    assert not has_subject("Are there trends") and not has_subject("why?")
    assert has_subject("Show our exports to the United States since 2023") and has_subject("and machinery?")
    c = Conversation("http://localhost:1", llm=None)
    c._subject_q = "Show our exports to the United States? Show the months since 2023"
    c.last_panels = ["viz_line_us_2023"]
    spec = {"panels": [{"id": "viz_line_us_2023", "type": "line"}], "marks": []}
    hint = c.follow_up_hint("Are there trends", spec)
    assert "United States" in hint and "viz_line_us_2023" in hint and "no new chart" in hint
    assert c.follow_up_hint("Which partners grew fastest since 1999?", spec) == ""   # a subject of its own
    assert "viz_line_us_2023" not in c.follow_up_hint("why?", {"panels": [], "marks": []})   # the chart is gone


def test_follow_ups_pick_their_specialists():
    from lux_insights.conversation import Conversation
    c = Conversation("http://localhost:1", llm=None)
    c._subject_q = "Show our exports to the United States? Show the months since 2023"
    c.last_panels = ["us_exports_monthly"]
    spec = {"panels": [{"id": "us_exports_monthly", "type": "line"}], "marks": []}
    c.box("visualizer").spec = lambda: spec   # no backend: the page as it is
    picked = {q: [s.agent for s in c.plan(q).steps] for q in
              ["Is there a trend and why did it happen?", "Why?", "Is there a seasonal pattern?",
               "What can we expect for the next six months?"]}
    assert picked["Is there a trend and why did it happen?"] == ["trend_finder", "cause_finder"]
    assert picked["Why?"] == ["cause_finder"] and picked["Is there a seasonal pattern?"] == ["trend_finder"]
    assert picked["What can we expect for the next six months?"] == ["forecaster"]
    assert "us_exports_monthly" in c.plan("Why?").steps[0].task


def test_process_talk_never_reaches_the_reader():
    """'... I could not add a note to the chart this time.' (15 Oct, 'how many car we exported')."""
    from lux_insights.specialists import leaked
    text = ("**Visualizer.** In 2025 Luxembourg sold €827 m of cars abroad. I could not add a note to the chart this "
            "time.\n\n_Next: Where do the goods go?_")
    assert leaked(text) == ["I could not add a note to the chart this time."]
    assert leaked("**Visualizer.** I couldn’t add the highlight.") and leaked("**Visualizer.** It is drawn this time.")
    assert not leaked("**Visualizer.** The data cannot show why exports rose.")


def test_how_many_is_said_in_euros_only_when_no_quantity_is_published():
    from lux_insights.specialists import units_sentence
    from lux_insights.tools import Turn
    reply = "**Visualizer.** In 2025 Luxembourg sold €827 m of cars abroad (HS 8703)."
    none = Turn("visualizer", quantities=[{"product": "8703", "use": None, "unit": None}])
    assert units_sentence("I want to see how many car we exported", reply, none) == \
        "No number of cars is published: the data has their value in euros only."
    assert units_sentence("How many tonnes of steel did we sell?", reply, none) == \
        "No quantity is published for these goods: the data has their value in euros only."
    assert units_sentence("What does Luxembourg sell?", reply, none) is None
    said = ("**Visualizer.** The page measures trade in euros, not the number of cars: in 2025 Luxembourg sold €827 m "
            "of cars abroad.")
    assert units_sentence("How many cars did we export?", said, none) is None
    # a count was found (get_quantities): no 'euros only' sentence; nor when the tool was not asked
    counted = Turn("visualizer", quantities=[{"product": "8703", "use": "units", "unit": "items"}])
    assert units_sentence("How many cars did we export?", reply, counted) is None
    assert units_sentence("How many cars did we export?", reply, Turn("visualizer")) is None


class FakeQuantities(FakeForecast):
    """/insights/series with measure=all, as the backend answers for cars (HS 8703) and for steel to the US."""

    def get(self, endpoint, **params):
        url = source_url(endpoint, params)
        if endpoint == "/insights/series" and params.get("measure") == "all":
            self.calls.append((endpoint, dict(params)))
            if params.get("product") == "8703":
                rows = [{"period": "2025", "value": 826875433.0, "weight": 122464.298, "units": 37654.0,
                         "unit": "items", "eur_per_tonne": 6751.97, "eur_per_unit": 21959.83}]
            else:
                note = "The goods of chapter 72 are counted in many different units"
                rows = [{"period": "2025", "value": 44024412.0, "weight": 55506.603, "units": None, "unit": None,
                         "eur_per_tonne": 793.14, "eur_per_unit": None, "units_note": note}]
            return {"rows": rows, "measure": "all", "loaded": True}, url
        return super().get(endpoint, **params)


def test_how_many_gives_the_count_the_value_and_the_value_per_unit():
    api = FakeQuantities()
    box = Toolbox("fake:", "visualizer", api=api, view=StubViewClient(LocalView(), "visualizer"))
    box.begin("How many cars did Luxembourg export in 2025?")
    out = box.get_quantities("8703", "EXPORT", period="2025")
    assert out["use"] == "units" and out["unit"] == "items"
    assert "37,654 items" in out["say"] and "€827 m" in out["say"] and "€21,960" in out["say"]
    assert "not a price" in out["say"] and box.turn.quantities == [{"product": "8703", "use": "units", "unit": "items"}]
    box.turn.see(out)
    assert not sp.unverified_figures("In 2025 Luxembourg exported 37,654 cars, worth €827 m, about €21,960 per car.",
                                     box.turn.seen)
    # steel: no count in units, so tonnes, and the reason
    box.begin("How many tonnes of steel did we export to the US?")
    out = box.get_quantities("72", "EXPORT", period="2025", partner="US")
    assert out["use"] == "weight" and out["unit"] == "t" and "55,507 t" in out["say"] and "€793" in out["say"]
    assert "many different units" in out["say"]


# ----------------------------------------------------------------------------- review fixes (15 October, evening)
def test_the_readers_length_never_cuts_the_not_proof_of_cause_sentence():
    text = ("**Cause finder.** Exports to the US fell 0.6% in 2025. Steel fell 22.1%. Three tariff dates fall in 2025. "
            "The dates line up with the steel fall, which is not proof of cause.")
    out = sp.fit_reader(text, {"max_sentences": 2, "show_codes": True})
    assert "fell 0.6%" in out and "Steel fell 22.1%" in out and "Three tariff dates" not in out
    assert out.rstrip().endswith("which is not proof of cause.")


def test_no_quote_that_only_repeats_a_sentence_of_the_reply():
    body = ["The series ranged from €25 m in August 2024 to €68 m in June 2023 [1].", "I drew the months."]
    assert sp.echoes("The series ranged from €25 m in August 2024 to €68 m in June 2023.", body)
    assert not sp.echoes("The series ranged from €25 m in August 2024.", body)
    text = "**Visualizer.** The series ranged from €25 m in August 2024 to €68 m in June 2023 [1]. I drew the months."
    out = sp.with_citation(text, profile("policy_adviser"))
    assert out.count("ranged from €25 m") == 1


def test_the_chat_shows_no_api_parameters_and_calls_an_8_digit_code_cn8():
    assert sp.plain_words("the movers between the two years with `flow=EXPORT`, `level=2`, and `rank_by=change`.") \
        == "the movers between the two years."
    assert sp.plain_words("the ranking by share mit `by=product`, `flow=IMPORT`, `partner=US`.") == "the ranking by share."
    assert sp.plain_words("HS 72163390 at €42 m; HS 7216 at €44 m.") == "CN8 72163390 at €42 m; HS 7216 at €44 m."
    assert sp.plain_words("Use `ranking` for this.") == "Use `ranking` for this."
