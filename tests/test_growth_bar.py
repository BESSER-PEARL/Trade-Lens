# SPDX-License-Identifier: MIT
"""Growth per year since a year, drawn (contract 1.7; the "Are there trends" answer of 15 Oct 2026).

The trend finder titled a bar "Fastest-growing export partners since 1999" and the tools refused it twice (a bar of
one year ranks by value). Now a bar or table titled as growth over a span (a start, "since 1999" in the question or
the title) or asked with measure='growth' is a growth bar: one query on /insights/trends, each partner's or
product's growth per year, with its first and last value. A bar of one year titled as growth with no span is still
refused, and the refusal names the growth bar.

No network, no LLM: a small fake API and the kit's in-memory page.
"""
from __future__ import annotations

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

from agents.kit import InsightsClient, InsightsError, get_path, source_url  # noqa: E402
from agents.kit.testing import LocalView, StubViewClient  # noqa: E402
from lux_insights.tools import GROWTH_SPAN_RE, ROLES, Toolbox  # noqa: E402

PARTNERS = [("CN", "China", 38881239.0, 235439740.0, 0.0717), ("PL", "Poland", 73646859.0, 383350934.0, 0.0655),
            ("CZ", "Czechia", 41454059.0, 166892157.0, 0.0550)]
PRODUCTS = [("64", "Footwear", 5e6, 2.96e8, 0.171), ("21", "Other food preparations", 9e6, 2.34e8, 0.132)]


class FakeInsights(InsightsClient):
    def __init__(self):
        self.base_url, self.calls = "fake:", []

    def close(self) -> None:
        pass

    def get(self, endpoint: str, **params):
        url = source_url(endpoint, params)
        self.calls.append((endpoint, dict(params)))
        if endpoint == "/insights/meta":
            return {"periods": {"A": [str(y) for y in range(1999, 2026)], "M": ["2026-06"]}}, url
        if endpoint == "/insights/trends":
            rows = PARTNERS if params.get("by") == "partner" else PRODUCTS
            start, end = str(params.get("start") or "1999"), str(params.get("end") or "2025")
            items = [{"code": c, "name": n, "start_value": a, "value": b, "growth": g, "cagr": g, "is_new": False,
                      "rank": i + 1} for i, (c, n, a, b, g) in enumerate(rows)]
            return {"by": params.get("by"), "start": start, "end": end, "threshold": 0.01, "n_significant": 14,
                    "items": items[:int(params.get("limit") or 10)], "falling": [], "new": [], "loaded": True}, url
        raise InsightsError(404, "not in the fake", url)


def boxes_for(api, store):
    out = {}
    for role in ROLES:
        box = Toolbox("fake:", role, api=api, view=StubViewClient(store, role))
        box.view.lookup_names = False
        box._meta = {}
        out[role] = box
    return out


@pytest.fixture
def world():
    api, store = FakeInsights(), LocalView()
    return api, store, boxes_for(api, store)


def call(box, name, **args):
    tool = next(t for t in box.tools([name]))
    out = tool.call(args)
    return json.loads(out) if out.startswith(("{", "[")) else {"text": out}


def panel_of(store, pid):
    return next(p for p in store.spec["panels"] if p["id"] == pid)


def test_are_there_trends_draws_the_growth_bar_it_was_refused(world):
    """The two calls of 15 Oct, as the trend finder made them: both draw, and keep 'since 1999' in the title."""
    _, store, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("Are there trends")
    partners = call(tf, "show_panel", type="bar", id="longrun_partners", title="Fastest-growing export partners "
                    "since 1999", flow="EXPORT", by="partner", start="1999", end="2025", max_items=5)
    products = call(tf, "show_panel", type="bar", id="longrun_products", title="Fastest-growing export product "
                    "groups since 1999", flow="EXPORT", by="product", start="1999", end="2025", level=1, max_items=5)
    assert partners["ok"] and products["ok"], (partners, products)
    p = panel_of(store, "longrun_partners")
    assert p["type"] == "bar" and p["options"]["measure"] == "growth" and p["options"]["format"] == "pct_signed"
    assert p["title"] == "Fastest-growing export partners since 1999"
    q = p["queries"][0]
    assert q["key"] == "main" and q["endpoint"] == "/insights/trends"
    assert q["params"] == {"by": "partner", "flow": "EXPORT", "start": "1999", "end": "2025", "limit": 5}
    assert panel_of(store, "longrun_products")["queries"][0]["params"]["level"] == 1
    assert partners["shows"]["main"]["items"][0]["growth_per_year"] == "+7.2% a year"
    assert "highlight" in partners["marks_it_takes"]


def test_a_span_in_the_question_makes_a_growth_title_a_growth_bar(world):
    """'since 2015' in the question: the bar starts there, ends at the page's year, and gets a subtitle that says so."""
    _, store, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Which products grew fastest since 2015?")
    out = call(vz, "show_panel", type="bar", title="Fastest-growing export products", by="product", flow="EXPORT",
               level=1, period="2025")
    assert out["ok"], out
    p = panel_of(store, out["panel"])
    assert p["options"]["measure"] == "growth"
    assert p["queries"][0]["params"]["start"] == "2015" and p["queries"][0]["params"]["end"] == "$period"
    assert p["subtitle"] == "Average growth per year since 2015 (compound)"
    assert out["follows_the_page_year"] is True


def test_measure_growth_asks_for_it_directly(world):
    _, store, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Show me which partners are the stars of the long run")
    out = call(vz, "show_panel", type="bar", title="Partners by growth per year", by="partner", flow="EXPORT",
               measure="growth")
    assert out["ok"], out
    assert panel_of(store, out["panel"])["queries"][0]["params"]["start"] == "1999"   # the first year of the data


def test_a_one_year_bar_titled_as_growth_is_refused_and_points_to_the_growth_bar(world):
    _, store, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("Which CN8 lines grew fastest in exports in 2025?")
    bar = call(tf, "show_panel", type="bar", title="Fastest-growing CN8 export lines in 2025", by="product",
               flow="EXPORT", level=4)
    assert bar["ok"] is False
    assert "measure='growth'" in bar["error"] and "waterfall" in bar["error"]
    assert not any((p.get("options") or {}).get("measure") == "growth" for p in store.spec["panels"])


def test_a_highlight_on_a_growth_bar_cites_growth_then_and_now(world):
    api, store, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("Which partners grew fastest since 1999?")
    out = call(tf, "show_panel", type="bar", title="Fastest-growing export partners since 1999", by="partner",
               flow="EXPORT")
    hl = call(tf, "highlight", target=out["panel"], codes=["CN"],
              note="China grew {cn_growth} a year, from {cn_then} to {cn_now}.")
    assert hl["ok"], hl
    mark = store.spec["marks"][-1]
    assert mark["kind"] == "highlight" and "period" not in mark     # a span is about no single year
    facts = mark["facts"]
    assert facts["cn_growth"]["endpoint"] == "/insights/trends" and facts["cn_growth"]["format"] == "pct_signed"
    assert facts["cn_then"]["path"] == "items.[code=CN].start_value" and facts["cn_now"]["path"] == "items.[code=CN].value"
    data, _ = api.get("/insights/trends", **{**facts["cn_growth"]["params"], "end": "2025"})
    assert get_path(data, facts["cn_growth"]["path"]) == pytest.approx(0.0717)


def test_get_trends_says_how_to_draw_it(world):
    _, _, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("Are there trends")
    out = call(tf, "get_trends", by="partner", flow="EXPORT", start="1999")
    assert "measure='growth'" in out["draw_it"] and "start='1999'" in out["draw_it"]
    assert "14 partner countries" in out["significance"] and "1%" in out["significance"]


def test_get_trends_of_one_product_line_by_market(world):
    """'Which markets for our H-sections (CN8 72163390) grew fastest?': the ranking is of that line's markets, and
    the chart it says to draw keeps the line (on 15 Oct the growth bar was drawn for all exports instead)."""
    api, _, boxes = world
    tf = boxes["trend_finder"]
    tf.begin("Which markets for our H-sections grew fastest per year since 2015?")
    out = call(tf, "get_trends", by="partner", flow="EXPORT", start="2015", product="72163390")
    assert api.calls[-1][1]["product"] == "72163390" and "partner" not in api.calls[-1][1]
    assert "product='72163390'" in out["draw_it"]
    call(tf, "get_trends", by="product", flow="EXPORT", start="2015", partner="US", product="TOTAL")
    assert api.calls[-1][1]["partner"] == "US" and "product" not in api.calls[-1][1]


def test_a_growth_bar_and_a_bar_of_values_are_two_kinds_of_chart(world):
    """A new growth bar never re-draws the agent's bar of values from an earlier turn (nor the other way round)."""
    _, store, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Who buys most?")
    values = call(vz, "show_panel", type="bar", title="Main export partners", by="partner", flow="EXPORT")
    if not values.get("ok"):
        pytest.skip("the fake answers no ranking")   # pragma: no cover
    vz.begin("Which partners grew fastest since 1999?")
    growth = call(vz, "show_panel", type="bar", title="Fastest-growing export partners since 1999", by="partner",
                  flow="EXPORT")
    assert growth["ok"] and growth["panel"] != values["panel"]


@pytest.mark.parametrize("words,span", [
    ("Fastest-growing partners since 1999", True), ("Growth per year, 1999-2025", True),
    ("Which grew fastest over the last 20 years", True), ("Fastest-growing CN8 lines in 2025", False),
    ("What grew most", False)])
def test_the_span_words(words, span):
    assert bool(GROWTH_SPAN_RE.search(words)) is span


def test_part_of_a_year_is_compared_with_the_same_months_a_year_before():
    """'Compare January to July 2026 with January to July 2025': get_series freq M says it, summed in code."""
    from lux_insights.tools import same_months
    rows = [{"period": f"{y}-{m:02d}", "value": v * 1e6} for y, vs in
            (("2025", [2.3, 5.4, 1.6, 8.5, 5.0, 4.0, 2.0, 3.6, 1.7, 2.0, 1.7, 4.3]),
             ("2026", [3.9, 10.3, 9.0, 11.5, 14.3, 9.7, 18.1])) for m, v in enumerate(vs, 1)]
    out = same_months(rows)
    assert out["months"] == "January to July" and out["2026_so_far"] == "€77 m" and out["2025_same_months"] == "€29 m"
    assert out["change"].startswith("+16")
    assert same_months(rows[:12]) is None and same_months(rows[5:]) is None   # a full year; months missing before


def test_compare_names_what_it_takes(world):
    _, _, boxes = world
    vz = boxes["visualizer"]
    vz.begin("Compare January to July 2026 with January to July 2025")
    out = call(vz, "compare", what="months")
    assert "heatmap" in json.dumps(out) and "same_months" in json.dumps(out)


def test_one_span_per_series_in_a_turn(world):
    """'Are there trends, and why?': the cause finder analyses the series the trend finder analysed over the same
    span, whatever span it asks for, so both say the same trend's change; the next question starts afresh."""
    from lux_insights.conversation import Conversation

    api, _, boxes = world
    calls = []

    def analyze(endpoint, **params):
        if endpoint == "/trends/analyze":
            calls.append((params.get("start"), params.get("end")))
            return {"trend_summary": "Over the span the trend fell.", "breaks": []}, "fake:"
        return FakeInsights.get(api, endpoint, **params)
    api.get = analyze
    c = Conversation("fake:", None, boxes=boxes)
    for role in ("trend_finder", "cause_finder"):
        c.box(role).begin("Are there any trends, and why?")
    call(c.box("trend_finder"), "analyze_trend", flow="EXPORT", freq="M", start="2023-01", partner="US")
    call(c.box("cause_finder"), "analyze_trend", flow="EXPORT", freq="M", start="2024-01", end="2026-06", partner="us")
    # the same span, and a monthly analysis runs to the last month (no end) unless the user asked for one
    assert calls[-1] == calls[0] == ("2023-01", None)
    c._spans.clear()   # what plan() does for the next question
    call(c.box("cause_finder"), "analyze_trend", flow="EXPORT", freq="M", start="2024-01", partner="US")
    assert calls[-1] == ("2024-01", None)


def test_a_verb_between_two_levels_must_match_them():
    """15 Oct: 'Exports to the United States fell {exports_from_abs} to {exports_to}' showed 'fell €41 m to €54 m'."""
    from lux_insights.tools import direction_problems
    vals = {"exports_from_abs": "€41 m", "exports_to": "€54 m", "a": "€1.2 bn", "b": "€980 m"}
    assert direction_problems("Exports fell {exports_from_abs} to {exports_to} over the span.", vals)
    assert not direction_problems("Exports rose from {exports_from_abs} to {exports_to}.", vals)
    assert not direction_problems("Exports went from {exports_from_abs} to {exports_to}.", vals)
    assert not direction_problems("Imports fell from {a} to {b}.", vals)
    assert direction_problems("Imports grew {a} to {b}.", vals)


def test_a_span_asks_the_analysts_for_one_direction_and_the_visualizer_to_draw(world, monkeypatch):
    """'Show the months since 2023': the trend's change is the analysts' to say; the visualizer only draws (15 Oct:
    its note 'The trend fell over the span shown' on January 2023)."""
    from lux_insights import conversation as conv
    from lux_insights.router import Plan, Step

    q = "Show our exports to the United States. Show the months since 2023"
    monkeypatch.setattr(conv, "route", lambda question, *a, **k: Plan([Step("visualizer", question),
                                                                        Step("trend_finder", question)], "llm"))
    _, _, boxes = world
    c = conv.Conversation("fake:", None, boxes=boxes)
    plan = c.plan(q)
    viz, tf = plan.steps
    assert conv.DRAW_ONLY in viz.task and conv.CONSISTENT not in viz.task
    assert conv.CONSISTENT in tf.task and conv.DRAW_ONLY not in tf.task


def test_a_trend_line_note_says_its_figure_is_a_year():
    """15 Oct: 'The fitted trend fell {growth} over the span' showed 7.4% (its rate a year) next to a reply's
    23.6% over the span. The note must say 'a year'."""
    from lux_insights.tools import PER_YEAR_RE
    assert PER_YEAR_RE.search("The fitted trend changed {growth} a year.")
    assert PER_YEAR_RE.search("Annual growth of the fitted trend: {growth}.")
    assert not PER_YEAR_RE.search("The fitted trend fell {growth_abs} over the span.")


def test_a_point_against_a_year_earlier_is_said_so():
    """{x_at_change} is one point against the same period a year before (15 Oct: read as the trend over the span)."""
    from lux_insights.tools import ToolProblem, at_change_problem
    at_change_problem("Exports were {exports_at}, {exports_at_change} on a year earlier.")
    at_change_problem("Exports were {value} in April.")
    for bad in ("Across the chart's span, exports' trend fell {exports_at_change_abs}.",
                "Exports to the United States moved {exports_at_change} in this month."):
        with pytest.raises(ToolProblem):
            at_change_problem(bad)
