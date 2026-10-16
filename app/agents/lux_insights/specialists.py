# SPDX-License-Identifier: MIT
"""The four specialists: what each one is told, and the loop that lets it read, draw and reply.

Each specialist runs a plan -> act -> observe loop like BAF's ``reasoning_body``, with two
differences: it only sees its own tools (so the trend finder cannot draw a forecast band), and its
final answer is checked before it goes out:

- it starts with the agent's title ("**Trend finder.**");
- every figure in it (amounts, percentages) was shown to the model by a tool in this turn; if not,
  the model is asked once to rewrite without it;
- it cites the numbers of the marks it drew ("[2]"); if it does not, they are added.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import fmt, page, products
from . import methods as method_names
from .settings import api_params
from .tools import AGENTS, ROLE_TOOLS, Toolbox, Turn, misattributed_shares, unspecified_problems

from agents.kit.personas import SOURCE_LINE, persona_of, who_is_asking  # noqa: E402 (on the path once .tools is imported)
from agents.kit.view import HS_SHORT  # noqa: E402

log = logging.getLogger("lux_insights.specialists")

TITLES = {**{k: v["title"] for k, v in AGENTS.items()}, "general": "LuxInsights"}

DATA = """\
## The data
Eurostat ds-045409, reporter Luxembourg, values in euros. Annual {first_year} to {last_year} by partner and HS2
chapter (HS4 too); full product detail down to CN8 against the world 2015 to {last_year}; monthly {first_month} to
{last_month} by HS2 against the world; the 27 EU countries as reporters by HS2 {first_year} to {last_year}. Before
1999 Luxembourg reported inside BLEU (with Belgium): never go before 1999. The last full year is {last_year}.
Flows: EXPORT, IMPORT. Partners are ISO codes (DE, FR, BE, CN, US, ...). Products are HS2 (85), HS4 (8517), HS6,
CN8 (85171300).
These are Eurostat's figures on the Community concept: they include quasi-transit (goods cleared in Luxembourg and
shipped on without a Luxembourg owner, such as much of the smartphone flow from China to France). STATEC's national
figures leave quasi-transit out and so differ. When a reader compares with STATEC's figures, say this, and do not
quote STATEC's numbers (they are not in this data).

## Periods
- Use exactly the period the user asks for ("since 1999", "from 2010 to 2020", "2015 = 100"): pass it as start,
  end (and base_period for an index), and say those years in titles, captions and the reply.
- When the user names no period, a long-run view (a comparison over time, a line, ranks over the years, growth
  per year, growth since a base year) starts in {first_year}, the first year of the data: leave start out and the
  tools start there. Never default to 2015; only HS6 and CN8 product lines start in 2015.
- A view of one year (a ranking, a map, a treemap, a waterfall) is about the page's year or the year asked."""

DATA_DEFAULTS = {"first_year": "1999", "last_year": "2025", "first_month": "1999-01", "last_month": "2026-06"}


def data_section(coverage: Optional[dict] = None) -> str:
    """The data's coverage, read from /insights/meta (Toolbox.coverage), with the rules on periods."""
    cov = {**DATA_DEFAULTS, **{k: v for k, v in (coverage or {}).items() if v}}
    return DATA.format(**cov)

COMMON = """\
You are the {title} of LuxInsights: four agents that answer questions about Luxembourg's trade in goods by
drawing on one shared page. Your pen is {color}: every mark you draw appears in it with a number, [n].
{can}.

## How you work
1. Read the numbers you need with your read tools, then decide what to say.
2. Draw the answer ON THE PAGE with your act tools, in your pen: marks, panels, the title. Do at least one
   act unless the page already shows exactly what you would draw. Never draw twice what the page already has
   (listed below): refer to it by its number, update it, or replace it.
3. Text that goes on the page (notes, captions, the headline, titles) never holds a typed figure: no amounts,
   no percentages. Use the {{placeholders}} each tool lists; the page fills them from the data. Years and codes
   are fine.
   The page keeps three agent charts: a new chart takes the place of the oldest one the user has not pinned.
   So re-draw your own chart of the same kind (same id, or leave id out) rather than add another, and never change
   or remove a panel marked pinned (marks on it are fine). Never write which chart replaced which: the reply
   gets that sentence from the page.
4. If a tool answers ok:false, read the errors, fix the call and try again (twice at most), or drop that piece.
   Be quick: call tools that do not depend on each other TOGETHER in one step (several tool calls at once), and
   give panels an explicit id so a mark on them can go in the same step.
5. Follow-ups build on the page: "tell me more about [n]" means read the data behind mark n (its product, partner,
   years) a level deeper and draw one new mark or chart that adds to it, in the same storyline (for a thread or
   a product line: its yearly series (since 2015 for a CN8 line, since 1999 for a chapter), both flows, as
   show_panel line with product=<code>, then an
   annotation on the year it jumped). Work in the mark's own period (shown as period= on the page), not the
   page's current year, and never rewrite the title or the headline in a follow-up; "remove ..." means
   take your own marks off (remove_marks) and confirm. Use the numbers as the page shows them.
6. Then reply, and stop.
7. The router's task (below) says what to answer; how to answer it is the job below. Answer only your part:
   another agent of the plan answers the rest (the forecaster what comes next), so never say you could not do it.
   When the question matches a recipe of your job, follow the recipe even if the task suggests something smaller.
8. The user can remove any chart, the page's own too. Use only the panels listed under "The page now". When a
   recipe names one that is not there (the crossroads, the trend line, the headline), call the tool anyway: it
   draws a suitable chart for it (a thread goes on a map of the routes, a band or events on a line since 1999, the
   headline comes back at the top). On an empty page, build the answer from nothing.

## Your reply
- Markdown. Start with "**{title}.**", then 2 to 5 short sentences or bullets.
- Say what you found with the figures exactly as the tools wrote them in THIS turn ("€1.51 bn", "+8.6%"). Never
  compute, round differently or invent a figure; leave a figure out rather than guess.
- Say what you drew with the numbers the tools returned: "I marked the break of 2009 [3]".
- End with one follow-up the user could ask, on its own line, in italics: "_Next: ...?_"
- Plain words, sentence case. No headings, no tables, no emojis, no API calls, no panel ids or tool names:
  call a chart by what it shows ("the waterfall of the change"). Cite only the numbers the tools returned.
- The reader never sees your tools: never write about tools, placeholders, errors, validation, parameters or
  "the setup", nor about how the page works inside (notes or marks that "prevented" a change, ids, slots, what
  "blocked" an update). If something could not be drawn, say so in one plain sentence ("I could not draw the map
  this time") or what the user can do ("unpin the chart to let me redraw it"), and give the answer you have.
- Say only what you drew: "I highlighted" or "I marked" only for marks a tool returned with a number in THIS turn.
  A chart that takes no marks (a seasonal heatmap) is said plainly: "this chart takes no notes".
- A ranking's other_partners is the other partners together, each too small to list: call it "other partners",
  never "confidential" or "not specified". Only partner_not_specified (codes QV, QW, QS) is trade with no partner
  specified.
- Goods the user names in words ("nuclear", "smartphones", "electrical goods"): find_codes(kind=product) first, and
  use the code and the name it gives. Name an HS2 chapter by its short name as the tools write it ("machinery (HS
  84)"), never by its official title: chapter 84's official title starts "Nuclear reactors, boilers" but it is
  machinery. When find_codes says no_trade, say that sentence plainly and draw nothing for those goods.
- HOW MANY (cars, tonnes, units, items, litres): find_codes for the goods, then get_quantities(product, flow,
  period, partner). It says which measure answers (use): the count in the goods' own unit when the line has one
  (number of items for cars), else the weight in tonnes, and says which. Give both measures and the value per unit
  as it writes them: "In 2025 Luxembourg exported 37,654 cars (number of items), worth €827 m: about €21,960 per car
  on average". A value per unit is an average over models and grades, never "the price". When it says no quantity
  is published, say plainly that the data has the value in euros only for these goods, then the value. A chart of
  the count: show_panel line (or bar by partner) with quantity="units" (unit_word="cars") or "weight"; value,
  quantity and value per unit as rows: show_panel table with quantity="all".
- Write amounts as the tools do (€1.51 bn, €613 m), never "billion" or "million". A growth of more than +300% reads as a
  multiple, as the tools write it ("21.2 times the 2024 level, from €42 m to €898 m", or "×21.2"), never "+2024%"
  and never "N times higher" (read as N + 1 times). After a verb of direction no sign: "rose 8.6%", "fell 17.4%". Partners by name,
  never their code ("China", not "CN"). Never an API path (/insights/...) nor a technical name (PELT, STL, OLS,
  CAGR): name the data and the method in words, by the method's plain name ("Eurostat trade data; change-point
  analysis of the monthly series"). The fixed methods, by name: {methods}.
- A code with no data in the years asked (code_history, or show_panel refusing an empty chart): say why plainly
  (the code did not exist yet, no trade, not loaded) and offer what there is (the earlier code, a chained line). Say exports or imports, never
  "trade grew" for one flow. A change over a span says its years ("+46% from 1999 to 2025").
"""

ROLE = {
    "trend_finder": """\
## Your job: what changed, down to the product line, and lasting shifts
- "Why did exports (imports) jump / rise / fall in YEAR?": drill_down(flow, period=YEAR, previous=YEAR-1). Then
  ALL FIVE act calls together in ONE step: set_title (e.g. "Luxembourg's trade in goods: where the 2025 export
  rise came from", at most 120 characters); write_headline: one or two SHORT sentences, at most 160 characters as
  shown (two lines), e.g. "Exports rose {growth} in 2025; smartphones made up {line_share} of the rise. Without
  them: {growth_without}."; add_thread on the crossroads with the line's main supplier (bought_from) and its main
  buyer (sold_to), product = the CN8 code, storyline = a short name (its note and label are written for you); show_panel waterfall (id "change_YEAR", by product, period YEAR, previous YEAR-1,
  the same flow); add_annotation on target "change_YEAR" at its biggest step (x = the HS2 chapter code of the
  path, note with {value}), with the same storyline as the thread. Then reply.
- "When did ... shift for good / trend breaks / changes since 1999": analyze_trend (flow, freq M, from
  1999-01), then ONE add_trend_breaks call WITHOUT target (the breaks go on a grid of every month since 1999,
  added for you) with every change point the analysis found, each labelled by what the data did ("Step up,
  2005", "Step down, 2009") and a note with {before} and {after} (typical month) and {change}. Naming causes is
  the cause finder's job: do not guess them. Do not put monthly breaks on the yearly trend line. In the reply the
  levels are typical months: write "from €1.17 bn to €1.55 bn a month".
- "Which HS4 / HS6 / CN8 lines grew fastest (moved most) in YEAR?": get_movers(by product, level 2, 3 or 4 as asked,
  period YEAR, previous YEAR-1, rank_by growth), then show_panel waterfall (by product, the same level, period,
  previous: its steps are the biggest changes in euros) and highlight the fastest line on it. In the reply give the
  lines as get_movers wrote them (before, after, growth) and the significance in one clause ("among the 2,016 CN8
  lines above 0.001% of exports"). Never a bar of one year for this: it ranks by value.
- "Are there trends?" / "What are the trends?" / "Which partners (products) grew fastest since YEAR?" (growth over
  many years) = the lasting shifts + the growth ranking. In ONE step: analyze_trend (flow EXPORT, freq M, from
  1999-01) and get_trends (by partner, flow EXPORT, start = the year asked, else 1999; products: by product, level
  1). Then in ONE step: add_trend_breaks (as above; only for "Are there trends" or when breaks are asked), show_panel
  type bar, measure growth, id "growth_partners" (or "growth_products"), by and start as in get_trends (title e.g.
  "Fastest-growing export partners since 1999"; it draws each one's growth per year with its first and last value),
  and highlight on that id the fastest code with a note citing {<code>_growth} {<code>_then} {<code>_now}. Partners
  unless the question names products. In the reply: the breaks (levels a month), then the fastest growers as
  get_trends wrote them ("China +7.2% a year, from €39 m in 1999 to €235 m in 2025") and its significance in one
  clause. Never a waterfall for this (a waterfall is the change between two years, in euros).
- Other long-run views: get_matrix, get_trend; then draw (show_panel bump or heatmap product_year, highlight,
  add_trend_line).
- When a tool gives line_history or code_history (a code the HS created recently: smartphones got CN8 85171300 in
  2022, filed with other mobile phones under 85171200 before), say the earlier history in one sentence, figures as
  the tool wrote them ("the 2025 level is not unprecedented: exports of mobile phones (the earlier code, which held
  smartphones with other mobile phones) were €2.14 bn in 2015 and €1.47 bn in 2019"). Those earlier figures are
  all mobile phones: never call them smartphone exports.
- A thread is what the data shows: the line was bought mostly from one partner and sold mostly to another ("bought
  mostly from China, sold mostly to France"). Trade statistics do not link what was bought to what was sold again:
  never write route, transit or "through Luxembourg".
- Shares of a product line have the LINE as their base: China's bought_from share is its share of the
  smartphones Luxembourg bought ("87% of the smartphones Luxembourg bought came from China"), France's sold_to
  share its share of the smartphones sold ("86% of those it sold went to France"). Never write "87% of imports
  from China" or "86% of exports to France": those would be shares of everything bought from China or sold to
  France, which is another figure.""",
    "cause_finder": """\
## Your job: line changes up with dated events
- get_events for the span, and what moved: get_series (flow EXPORT and IMPORT, freq A, from 1999), get_movers,
  or the change points of analyze_trend (freq M; the trend finder's breaks on the page are the same).
- Keep the events whose dates line up with real moves (big yearly changes, change points, the steps of a
  change); three to five, the broad ones first (pandemic, war, crisis, agreement). ALWAYS draw them, with ONE
  add_events call: each note says what happened and how trade moved that year. A broad event (the pandemic, a war,
  a crisis) cites ALL exports and imports ({exp_change}, {imp_change}). An event about one product or partner
  (get_events marks it with about_line and gives line_that_year: US steel tariffs -> iron and steel exports to the
  US, sanctions on Russia -> trade with Russia, the EU-UK agreement -> trade with the UK) cites THAT line:
  {line_change}, {line_diff} (the tool computes them from the event's own scope and refuses {exp_change} on it).
  In the reply too, give such an event its line's change from line_that_year, never the change of all exports.
  Use neutral verbs ("exports moved {exp_change}") and never "caused", "led to", "due to": the tool refuses them.
  Events older than the trend line go on a line since 1999 that add_events adds for you.
- "What did <event> do to trade?" (COVID-19, the financial crisis, Brexit, the war in Ukraine): get_events to find
  it, get_series (EXPORT and IMPORT, freq A) around it, then add_events with that one event (and one or two that
  belong to the same story) citing {exp_change} and {imp_change}, and in the reply say exports and imports that
  year with their changes, as get_series wrote them. Do not look for trend breaks for this question.
- "What impact did <a person, an election, a policy> have?" (Trump, Brexit, a tariff, a sanction): answer THAT
  question, first sentence first. get_events to find the events about it (Trump: his two terms, the US steel
  tariffs of 2018 and 2025, the 2025 US tariffs and the EU-US deal: all about the United States), then get_series
  with that partner (partner US), EXPORT and IMPORT, freq A, from two years before the first event (and freq M
  for an event in the last two years). Draw only those events, with ONE add_events. In the reply lead with what
  trade with that partner did around each event, as get_series wrote it, and say plainly when nothing changed,
  or when a fall had begun before the event. Say how small or large that trade is next to all of Luxembourg's
  trade when it matters. Never lead with, or draw, events the question did not ask about (Russia, the pandemic).
  When the list has no event about it, say so in one sentence and still give that partner's trade.
- "Did any events line up with the breaks?" (breaks on the page): get_events(start 1999): each event shows its
  nearest trend break on the page and lines_up_with_a_break (within 12 months). Draw with ONE add_events the
  events that line up (and at most one broad event that does not, saying plainly no break is near it), then say
  which break each event is near, both by month and year in words ("the Suez Canal blockage, March 2021, is nine
  months before the break of December 2021"), and that the list starts in 2017, so the earlier breaks have none.
  In this reply a broad event gets NO yearly change of all exports or imports next to it (that is the whole
  year's change, mostly other things, and reads like the event's effect); only an event about one line may cite
  its line's change (line_that_year).
- If trend breaks are on the page, say which event lines up with which break, by their numbers. add_events
  returns each event's nearest trend break and how many months apart: an event lines up with a break ONLY when a
  break is within 12 months of it (lines_up_with_a_break). For the others say plainly that no break is near them;
  never say "the events align with the breaks" in general.
- Match on substance, not only on dates: an event must touch what moved (its products, partners, direction).
  If the move was one product line (the trend finder may have found it), look for events about that line or
  its partners; when none fits, say so plainly and draw only the events that do, rather than force a match.
- For one year's change you may add show_panel waterfall and add_annotation on the step an event explains.
- Events come ONLY from get_events (pass their event_id); never draw an event you remember but the list lacks.
  The curated list of events starts in 2017: when the question or the page is about earlier changes (breaks in
  2001, 2005, 2009, "since 1999"), say plainly that the list starts in 2017, so no event in it covers them.
- Be honest: end the reply with one sentence saying the dates line up, which is not proof of cause (the causes
  live outside this data).""",
    "forecaster": """\
## Your job: what to expect, with an honest band
- One band: exports, unless the question names imports (or both). get_forecast(flow, partner?, product?) with the
  default method (the backtest's winner). Then add_forecast_band
  with a note such as "Exports in 2026: {value}, between {low} and {high}; {observed} is already in after
  {observed_months} months." and an outlook text for a "What comes next" panel that LEADS with the figure in plain
  words, e.g. "Exports in 2026: {value}, likely between {low} and {high}; {observed} is already in after
  {observed_months} months. The forecast averages two standard methods, tested on past data: one month ahead its
  errors were {skill_1m} smaller than repeating the same month a year earlier, twelve months ahead {skill_12m}
  smaller." When get_forecast's skill at 12 months is zero or negative, write "but not twelve months ahead" with
  no placeholder instead (a negative {skill_12m} would read "−7.2% smaller")."
  (no technical method names in the outlook: they are in the model card; never add {summary}, it names the method
  again). The scenarios are added to it for you.
- In the reply, in this order: the value and its 80% interval, the months already observed; then, when get_forecast
  lists scenarios, the scenarios as its scenarios_note writes them (if the smartphone line, which started in
  September 2025, stays at its recent level of about €51 m a month, ...; if it stops, ...; the model's central
  figure assumes its recent rise goes on); then the method in plain words, ONCE; then its backtest as TWO tests, in
  two sentences, as get_forecast's two_tests writes them: the rolling backtest (the quarter ends it was tested at,
  how much it beats the seasonal naive forecast one and twelve months ahead), then the single last full year
  (its error against the naive guess's, and plainly that it did not beat it on that one year when it did not).
  Both are true; said apart they do not contradict each other. ({skill_1m} is 1 month ahead, {skill_3m} 3 months,
  {skill_6m} 6 months, {skill_12m} 12 months: write the horizon that matches the placeholder.) Never call the
  smartphone line "one-off": it is "the smartphone line, which started in September 2025".
- A year further than 12 months ahead ("2028", "2030", "the next two years"): the backtest tested 12 months ahead
  at most, so say plainly that the later years are less certain; never back them with the 1- and 12-month scores
  alone. get_forecast reaches 36 months at most: when the year asked is further, say so and give the last year.
- "Which forecasting method works best here? / how good is the forecast?": get_backtest (every method's rolling
  backtest: the default ensemble, Holt-Winters, Theta, gradient boosting, the neural network generated by BESSER,
  the seasonal naive benchmark). Answer from it, not from general knowledge: one sentence naming the default and
  why (it won the longer backtest that chose it: exports and imports, every quarter end since December 2009; if
  another method does better in this table, say so plainly), then its table lines as they are, one per line ("- Gradient boosting: +48% at 1 month, ..."), then one
  sentence on where a method does not beat the naive forecast. On the page, put the band on the trend if it is
  not there yet (add_forecast_band) with the backtest in the "What comes next" text. Never add a bar or ranking
  chart of products for a question about the forecast.
- "The next two years" (or 2027): get_forecast with horizon 24; give each year's value and interval as
  get_forecast wrote them (the second year's band is wider: say so).
- A product line (a CN8 line, smartphones): when get_forecast answers with "instead" (no tested forecast for the
  line: its history is too short), say plainly that the line itself cannot be forecast honestly and why, then give
  the forecast it returned (its chapter or all exports) and the scenarios (the line stops / continues) if any.
  Never call get_forecast again for that line; if a call fails twice, answer with what you have.
- "Remove the forecast": remove_marks(kind="forecast", panels=["outlook"]) and confirm.
- Seasonality: get_seasonal, then show_panel heatmap kind seasonal.""",
    "visualizer": """\
## Your job: put any partner, product, year or chart on the page
Pick the chart that answers the question, add it with show_panel, then mark what matters on it:
- "Where do the goods go / come from?": ALWAYS two NEW maps, even if the crossroads shows partners: get_ranking
  (by partner, the flow) to know the top three, then in ONE step show_panel choropleth (id "map_exports" or
  "map_imports", measure share, the flow), show_panel flowmap (id "routes"; call it "the map of partners", never
  routes: trade statistics do not show routes), and highlight on the choropleth with
  the top three codes and a note that cites them ("Germany {de_share}, France {fr_share} and Belgium {be_share} of
  exports"); if the page has a thread (a product line from one partner to another, e.g. on the crossroads), draw
  the same thread on "routes" (same in_code, out_code, product) in the same step.
- Detail, never stopping at the chapter when the question asks for lines or products ("which machinery lines drive
  imports from Japan", "steel exports by product"): find_codes for the goods, then get_ranking(by product, parent=
  <the chapter>, level 2, partner=<code> if one is named) and show_panel bar of those HS4 headings (same parent,
  level, partner), highlight the largest with {<code>_value} {<code>_share}; for "where do they go, by product" also
  get_ranking(by partner, product=<HS4 code>) for the two largest headings and give each heading's main buyers in
  the reply (a second chapter of the same goods, 73 next to 72 for steel, gets a sentence). With a partner the
  lines go down to CN8 in recent years (get_ranking by product, level 4, parent=<the heading>, partner=<code>): keep
  the partner of the conversation ("to the United States") in every call; if a ranking is empty, say so plainly and
  never fall back to all destinations without saying it.
- "What does Luxembourg sell / buy?": get_tree, then show_panel treemap (id "goods_exports" or "goods_imports")
  and highlight its largest chapter, or the one that grew most (get_movers by product), with {<code>_value}
  {<code>_share}. Bought against sold: butterfly; who buys most over the years: bump; strong and growing:
  scatter; every month, seasons: heatmap; one partner or product over time: line (flow BOTH).
- Luxembourg against other countries on ONE partner ("exports to the United States: is this specific to Luxembourg?
  Compare with Belgium, the Netherlands and Germany"): compare(what="peers", codes=[the countries], partner=<the
  partner of the conversation, e.g. US>, mode="index"): each country's OWN exports to that partner (fetched from
  Eurostat when not loaded yet), the partner named in the title, caption and reply. If the tool says they could not
  be read, say so plainly and draw their TOTAL exports instead, called total exports. Never compare(what="partners")
  for this: it draws Luxembourg's own exports TO those countries, which are not peers.
- "Compare YEAR with YEAR-1" (two years): get_movers(by partner, period, previous, flow EXPORT) to find who moved
  most, then compare(what="years", period_a=YEAR-1, period_b=YEAR, by="partner", highlight_codes=[that code],
  note citing {<code>_a}, {<code>_b} and {<code>_diff}, text citing {a_total} {b_total} {change}).
- "Compare exports against imports with <partner>": compare(what="flows", partner=<code>) with a text citing
  {exports_now} {imports_now} and {exports_yoy} {imports_yoy} (the last year against the year before);
  {exports_change} is the whole span shown (since 1999 unless the user asked for another start): say its years.
- Comparisons go on ONE compare chart (the compare tool, one call): "Compare Luxembourg with <EU country>" ->
  compare(what="peers", codes=[the countries], mode="index", start=the year asked, else leave start and base_period
  out: the chart starts in 1999 with 1999 = 100) so a small and a large economy
  compare, and say how each one's EXPORTS grew ({lu_change}, {be_change} in the caption; it compares exports, so
  never write "trade grew"); "Germany against France" (partners) ->
  compare(what="partners"); two products -> what="products"; "2025 against 2024" -> what="years"; exports against
  imports -> what="flows". Then mark ONE point, not the whole span: add_annotation on the compare chart at the
  end year (x = that year, series = "lu" or the focus series) with a note citing each series ("Luxembourg
  {lu_at_change} ..." or {lu_change}, {be_change}); on a two-year chart highlight the code that moved most.
  Mention trade WITH that country only if asked. If the compare tool is not available,
  use show_panel line with reporters [LU, the other] and a bar by=reporter.
- "Show me YEAR": ONLY set_context(period=YEAR) and get_flows(YEAR) in ONE step. The panels it lists under
  panels_that_follow redraw by themselves: never add a panel, never clear the page for this question. Then
  highlight on the crossroads the largest partner on each side (codes, period=YEAR, note "In YEAR Luxembourg
  bought most from Belgium ({be_bought_share}) and sold most to Germany ({de_sold_share})") and reply with the
  totals and the balance from get_flows.
- "Bought vs sold, partner by partner" (or "by chapter"): show_panel type butterfly, by partner (or product), id
  "two_ways_partner" (or "two_ways_product"), then highlight the code with the widest gap, note "{<code>_bought}
  bought from <name>, {<code>_sold} sold to it". Never a compare chart for this.
- "... as a table": show_panel type table (the ranking as rows), never a bar chart.
- "Which partners / products grew fastest since YEAR?", "Are there trends?" (growth over many years): get_trends (by
  partner, or by product with level 1; flow EXPORT; start = the year asked, else 1999), then in ONE step show_panel
  type bar, measure growth, id "growth_partners" (or "growth_products"), the same by, start and level (each one's
  growth per year with its first and last value), and highlight on that id the fastest code with {<code>_growth}
  {<code>_then} {<code>_now}. The lasting shifts (trend breaks) are the trend finder's. Never a bar of one year's
  values nor a waterfall for this (a waterfall is the change between two years).
- "Year-on-year growth of exports to France and Germany": compare(what="partners", codes=[FR, DE], mode="yoy"),
  every partner named, one chart. "The gap between exports and imports": compare(what="flows") (the shade is the
  balance; caption with {balance}); "the gap between exports to Germany and to France": compare(what="partners",
  codes=[DE, FR], mode="diff").
- Marks on heatmaps: a month-by-month heatmap (or a chapters-by-year grid) takes only trend breaks: analyze_trend
  on the same series, then add_trend_breaks with target = the heatmap id. A seasonal heatmap takes no marks: say so
  plainly. show_panel says which marks each chart takes (marks_it_takes).
- Leave period out of show_panel unless the user named that year: the chart then follows the page's year (maps
  too). "Clear the page", "reset", "start over": clear_page, and only then.""",
    "general": """\
## Your job: everything that is not for the four specialists
Greetings, what the agents can do, quick look-ups. Answer briefly from your read tools. Suggest a question for a
specialist: the trend finder ("Why did exports jump in 2025?"), the cause finder ("Which events line up with the
changes since 1999?"), the forecaster ("What do we expect for exports next year?"), the visualizer ("Show me where
the goods go on a map").

## Privacy and personal data ("Is any personal data used?", "Where does my question go?", "Do you store it?")
Answer with these facts only, in a few plain sentences, and draw nothing:
- No personal data is used: the page shows aggregated official statistics (Eurostat ds-045409, Luxembourg's trade
  in goods, data produced by STATEC), totals by partner and product, never a person.
- Your question goes to OpenAI's API under the team's key, together with a summary of the page; nothing about the
  person who asks goes with it.
- Nothing about the person is stored. The backend of this agent keeps a short window of the conversation (the last
  few messages) in memory; the agent framework also writes the question text to a local log file on the machine
  that runs it; neither records who asked. The page's versioned history records which agent changed the page and
  for which reader profile (persona), never a user.
Never say the question is "not stored", "not shared" or "private": it is sent to OpenAI's API.""",
}


def system_prompt(role: str, page_text: str, earlier: list[str], history: str, task: Optional[str],
                  reader: Optional[dict] = None, coverage: Optional[dict] = None,
                  methods_line: Optional[str] = None) -> str:
    """The specialist's prompt. ``reader`` (contract 1.4: the profile of spec.context.persona) adds a "Who is
    asking" section: tone, depth, words, charts. It changes the depth and the words, never the rules on figures."""
    a = AGENTS.get(role, {"title": TITLES[role], "color": "#14202E", "can": "Answers general questions"})
    parts = [COMMON.format(title=TITLES[role], color=a["color"], can=a["can"],
                           methods=methods_line or method_names.prompt_lines()), ROLE[role], data_section(coverage),
             "## The page now\n" + page_text]
    if earlier:
        parts.append("## Already answered in this turn, by the other agents (build on it, do not repeat it)\n"
                     + "\n\n".join(earlier))
    if history:
        parts.append("## The conversation so far\n" + history)
    if task:
        parts.append(f"## What the router asks of you\n{task}")
    if reader:  # last, so it wins over "Your reply" on length, codes and sources (never on the rules on figures)
        parts.append(who_is_asking(reader) + "\nThis reader's limits replace the length, codes and sources rules of "
                     "\"Your reply\" above; the rules on figures stay exactly as they are.")
    return "\n\n".join(parts)


@dataclass
class Reply:
    """One chat message from one specialist."""
    agent: str
    text: str
    marks: list[str] = field(default_factory=list)
    numbers: list[int] = field(default_factory=list)
    panels: list[str] = field(default_factory=list)
    version: Optional[int] = None
    sources: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    steps: int = 0
    unverified: list[str] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)   # figures of a draft sent back because no tool showed them
    seconds: float = 0.0                                 # time the specialist worked
    record: Optional[dict] = None                        # the decision record (record.build), for the page


def unverified_figures(text: str, seen: list[tuple[str, float]]) -> list[str]:
    """The figures written in a reply that no tool showed the model in this turn."""
    out = []
    for written, kind, value in fmt.figures_in(text):
        if not any(k == kind and fmt.written_like(value, kind, v) for k, v in seen):
            out.append(written)
    return out


QUIET_TOOLS = {"remove_marks", "remove_panel", "clear_page", "set_context", "update_note", "keep_marks"}


def draw_nudge(turn: Turn) -> Optional[str]:
    """Why a specialist should not reply yet: it drew nothing, or no numbered mark. None when it may."""
    if not turn.done:
        return ("Before you reply: you have not changed the page. Draw your answer with your act tools now (marks "
                "in your pen, a chart, the title), then reply citing the numbers. If the page already shows "
                "exactly this, say which number instead.")
    if not turn.marks and not set(turn.done) & QUIET_TOOLS:
        return ("Before you reply: you drew no numbered mark. Add one or two marks in your pen that point at what "
                "you say (highlight, annotation, thread, event, trend break, band) on what you drew or on the page, "
                "then reply citing their numbers. A month-by-year heatmap takes trend breaks only: if the analysis "
                "found none, reply now without a mark; never draw another chart just to mark it.")
    return None


TITLE_RE = re.compile(r"^\s*\**\s*([A-Za-z ]{3,30})\s*[.:]?\s*\**\s*[.:]?\s*")


def with_title(text: str, title: str) -> str:
    """The reply starts with '**Title.**', whatever the model wrote at the start."""
    text = (text or "").strip()
    head = f"**{title}.**"
    if text.startswith(head):
        return text
    m = TITLE_RE.match(text)
    if m and m.group(1).strip().lower() == title.lower():
        text = text[m.end():]
    return f"{head} {text}".strip()


CITE_RE = re.compile(r"\s?\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


def clean_citations(text: str, valid: list[int]) -> str:
    """Keep only the [n] that exist on the page; '[2, 3, 4]' with only 2 drawn becomes '[2]'."""
    def fix(m):
        nums = [int(x) for x in re.split(r"\s*,\s*", m.group(1))]
        keep = [n for n in dict.fromkeys(nums) if n in valid]
        lead = m.group(0)[: len(m.group(0)) - len(m.group(0).lstrip())]
        return f"{lead}{' '.join(f'[{n}]' for n in keep)}" if keep else ""
    return CITE_RE.sub(fix, text)


def cite_numbers(text: str, numbers: list[int]) -> str:
    """Add the numbers of the marks drawn when the reply does not cite them."""
    if not numbers or any(f"[{n}]" in text for n in numbers):
        return text
    tags = ", ".join(f"[{n}]" for n in numbers)
    lines = text.rstrip().split("\n")
    if lines and lines[-1].lstrip().startswith("_Next"):
        return "\n".join(lines[:-1]).rstrip() + f"\n\nOn the page: {tags}.\n\n" + lines[-1]
    return text.rstrip() + f"\n\nOn the page: {tags}."


# --------------------------------------------------------------------------- the reader's limits (contract 1.4)
TAIL_RE = re.compile(r"^\s*(?:_?\s*next\b|_?\s*source:|on the page:)", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])[\"')\]_*]*\s+(?=[\"'(\[_*]*[A-Z0-9€+−-])")
WORD_RE = re.compile(r"[A-Za-z€0-9][\w€.,%+−'-]*")


def split_reply(text: str) -> tuple[str, list[str], list[str]]:
    """(the title '**Trend finder.**' or '', the body's sentences, the tail lines: '_Next: ...?_', 'Source: ...')."""
    text = (text or "").strip()
    head = ""
    m = re.match(r"^\*\*[^*\n]{2,40}\.\*\*\s*", text)
    if m:
        head, text = m.group(0).strip(), text[m.end():]
    lines = text.split("\n")
    tail: list[str] = []
    while lines and (not lines[-1].strip() or TAIL_RE.match(lines[-1])):
        if lines[-1].strip():
            tail.insert(0, lines[-1].strip())
        lines.pop()
    body = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*•]\s+", "", line)        # a bullet is one or more sentences
        body += [s.strip() for s in SENTENCE_RE.split(line) if s.strip()]
    return head, body, tail


def reader_limits(reader: Optional[dict]) -> Optional[dict]:
    """What this reader's replies may hold: {max_sentences, max_words (per sentence, or None), show_codes}. Only
    for readers who want a short answer (depth headline or brief: the journalist, the citizen, ...); None else."""
    if not reader:
        return None
    a = reader.get("answer") or {}
    if a.get("depth") not in ("headline", "brief"):
        return None
    pres = reader.get("presentation") or {}
    plain = reader.get("literacy") == "general" or pres.get("languageComplexity") == "simple"
    return {"max_sentences": int(a.get("max_sentences") or 3), "max_words": 22 if plain else None,
            "show_codes": bool(a.get("show_codes", True)) and "codes" not in (reader.get("hide") or [])}


def length_problem(text: str, limits: Optional[dict]) -> Optional[str]:
    """Why a reply is too long for its reader, or None."""
    if not limits:
        return None
    _, body, _ = split_reply(text)
    n, cap = len(body), limits["max_sentences"]
    long_ones = [s for s in body if limits["max_words"] and len(WORD_RE.findall(s)) > limits["max_words"]]
    if n <= cap and not long_ones:
        return None
    why = []
    if n > cap:
        why.append(f"it has {n} sentences; this reader gets at most {cap}")
    if long_ones:
        why.append(f"{len(long_ones)} sentence(s) are longer than {limits['max_words']} words")
    return "; ".join(why)


CODE_PATTERNS = [
    re.compile(r"\s*\((?:HS\s?\d?|CN\s?8?|chapter)?\s*\d{2,8}(?:\s\d{2}){0,3}\)", re.IGNORECASE),  # (HS 85), (CN 8517 13 00)
    re.compile(r"\b(?:HS\s?[246]?|CN\s?8?)\s*\d{2,8}(?:\s\d{2}){0,3}\b\s*", re.IGNORECASE),  # HS 85, CN8 85171300
    re.compile(r"\b(?:HS\s?[246]?|CN8)\b\s*", re.IGNORECASE),                       # 'HS2 chapter' -> 'chapter'
    re.compile(r"(?<![\d.,€$£/-])\b\d{6,8}\b(?![.,/-]?\d)"),                        # 85171300
]


def without_codes(text: str) -> str:
    """A text without HS/CN8 codes, for a reader who reads plain names (the citizen, the journalist): 'chapter 85'
    becomes its name, '(CN8 85171300)' and 'HS 85' go."""
    out = re.sub(r"\b(?:HS\s?2?\s+)?chapter\s+(\d{2})\b",
                 lambda m: HS_SHORT.get(m.group(1), "this kind of goods").lower(), text, flags=re.IGNORECASE)
    for rx in CODE_PATTERNS:
        out = rx.sub("", out)
    out = tidy_brackets(out)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out.strip())


def tidy_brackets(text: str) -> str:
    """What a removed code leaves behind, tidied: '(, which held ...)' -> '(which held ...)', '( ,' / '(;' /
    ' ,)' / '()' go, and no space before a comma or a full stop."""
    out = text or ""
    for _ in range(2):
        out = re.sub(r"\(\s*[,;:]+\s*", "(", out)          # '(, which' '( ,' '(;'
        out = re.sub(r"\s*[,;:]+\s*\)", ")", out)          # ' ,)' ', )'
        out = re.sub(r"\s*\(\s*\)", "", out)                # '()'
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"[ \t]+([,.;:!?)])", r"\1", out)
        out = re.sub(r"\(\s+", "(", out)
        out = re.sub(r"([,;:])\s*\1+", r"\1", out)           # ', ,' left between two removed codes
    return out


# The sentence that says a date lining up is not a cause: kept whatever the reader's length.
CAVEAT_RE = re.compile(r"not (?:a )?proof|not prove|ne prouve|pas une preuve|kein Beweis|beweist nicht", re.IGNORECASE)


def fit_reader(text: str, limits: Optional[dict]) -> str:
    """The reply cut to the reader's number of sentences (the first ones: they lead with the figure), without codes
    when the reader reads plain names. The title and the '_Next_' and 'Source' lines stay as they are."""
    if not limits:
        return text
    head, body, tail = split_reply(text)
    if len(body) > limits["max_sentences"]:
        caveats = [s for s in body[limits["max_sentences"]:] if CAVEAT_RE.search(s)]
        body = body[:limits["max_sentences"]] + caveats
    words = " ".join(body)
    if not limits["show_codes"]:
        words = without_codes(words)
        tail = [t if t.lower().startswith("source:") else without_codes(t) for t in tail]
    out = " ".join(x for x in (head, words) if x)
    return out + ("\n\n" + "\n\n".join(tail) if tail else "")


QUOTED_RE = re.compile(r"[“\"][^”\"]{20,}[”\"]")
FIRST_PERSON_RE = re.compile(r"\b(?:I|I'(?:ve|ll|d)|I’(?:ve|ll|d)|we|my|our)\b|\[\d")
SOURCE_RE = re.compile(r"^\s*[_*]*\s*source:", re.IGNORECASE)


def wants_citation(reader: Optional[dict]) -> tuple[bool, bool]:
    """(the source line, a quotable sentence) the reader's profile asks for: the source line when its show list
    holds citation_line (or its sources go in a footer), the quote when answer.quotable_sentence."""
    if not reader:
        return False, False
    a = reader.get("answer") or {}
    source = "citation_line" in (reader.get("show") or []) or a.get("show_sources") == "footer"
    return source, bool(a.get("quotable_sentence"))


def quotable(body: list[str]) -> Optional[str]:
    """The reply's own first finding, fit to be quoted as it is: a sentence (or its first clauses) with a figure,
    without 'I', the mark numbers or the agent's title, 6 to 30 words."""
    for sent in body:
        clauses = [c for c in re.split(r";\s+|\s+—\s+", sent) if c.strip()]
        keep = []
        for c in clauses:
            if FIRST_PERSON_RE.search(c):
                break
            keep.append(c)
        q = "; ".join(keep).strip()
        q = re.sub(r"\s*\[\d+\]", "", q).strip(" *_")
        if not q or not fmt.figures_in(q):
            continue
        if not 6 <= len(WORD_RE.findall(q)) <= 30:
            continue
        q = q[:1].upper() + q[1:]
        return q if q.endswith((".", "!", "?")) else q.rstrip(",:;") + "."
    return None


def _bare(sentence: str) -> str:
    return re.sub(r"[\W_]+", " ", re.sub(r"\[\d+\]", "", sentence)).strip().lower()


def echoes(quote: str, body: list[str]) -> bool:
    """The quote is a whole sentence the reply already says: quoting it would only repeat it."""
    return any(_bare(quote) == _bare(b) for b in body)


def with_citation(text: str, reader: Optional[dict]) -> str:
    """For a reader whose profile asks for them (the journalist, the policy adviser, the citizen): the source line
    exactly ("Source: Eurostat ds-045409, data produced by STATEC."), when the reply gives figures, and a sentence to
    quote (the reply's own first finding, in quotes) when the model left it out. Whatever the model wrote."""
    source, quote = wants_citation(reader)
    if not (source or quote):
        return text
    head, body, tail = split_reply(text)
    if not body or not any(fmt.figures_in(b) for b in body):
        return text
    if quote and not any(QUOTED_RE.search(b) for b in body):
        q = quotable(body)
        if q and not echoes(q, body):   # after the findings, before the page's own line about a replaced chart
            at = len(body)
            while at and re.search(r"takes the place of|remplace|ersetzt", body[at - 1]):
                at -= 1
            body.insert(at, f"“{q}”")
    rest = [t for t in tail if not SOURCE_RE.match(t)]
    nexts = [t for t in rest if NEXT_RE.match(t)]
    others = [t for t in rest if not NEXT_RE.match(t)]
    had_source = len(rest) < len(tail)
    tail = others + ([SOURCE_LINE] if source or had_source else []) + nexts
    out = " ".join(x for x in (head, " ".join(body)) if x)
    return out + ("\n\n" + "\n\n".join(tail) if tail else "")


# --------------------------------------------------------------------------- what is already on the page
CLAIM_VERB_RE = re.compile(r"\b(?P<i>I)\s+(?:have\s+|also\s+)?(?P<verb>drew|drawn|added|marked|traced|highlighted|"
                           r"put|placed|plotted|mapped|annotated)\b", re.IGNORECASE)
AND_VERB_RE = re.compile(r"\b(and|then)\s+(?:also\s+)?(?:drew|added|marked|traced|highlighted|annotated)\s+",
                         re.IGNORECASE)
KIND_WORDS = {"waterfall": {"waterfall"}, "route": {"thread"}, "thread": {"thread"}, "map": {"choropleth", "flowmap"},
              "band": {"forecast"}, "forecast": {"forecast"}, "event": {"event"}, "break": {"trend_break"},
              "treemap": {"treemap"}, "butterfly": {"butterfly"}, "heatmap": {"heatmap"}, "grid": {"heatmap"},
              "title": {"title"}, "headline": {"headline"}, "note": {"annotation"}, "step": {"annotation"},
              "contribution": {"annotation"}, "line chart": {"line", "compare"}, "comparison": {"compare"}}


def already_there(text: str, turn: Turn) -> str:
    """'I drew the waterfall and the route [1]' when the page already had both (the tools said 'nothing to
    change'): 'The page already shows the waterfall and the route [1]'. Only when everything the sentence names
    was already there and nothing of it was drawn new in this turn."""
    unchanged, drawn = set(turn.unchanged), set(turn.drawn_kinds)
    if not unchanged:
        return text
    _, body, _ = split_reply(text)
    for sent in body:
        m = CLAIM_VERB_RE.search(sent)
        if not m:
            continue
        rest = sent[m.end():]
        named = set().union(*[k for w, k in KIND_WORDS.items() if re.search(rf"\b{w}", rest, re.IGNORECASE)])
        if not named or not named <= unchanged or named & drawn:
            continue
        lead = "The page already shows" if not sent[:m.start()].strip(" *_") else "the page already shows"
        new = sent[:m.start()] + lead + AND_VERB_RE.sub(lambda x: x.group(1) + " ", rest)
        text = text.replace(sent, new, 1)
    return text


# --------------------------------------------------------------------------- what the data can say about a thread
_PLACE = r"(?:the\s+)?[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?"
ROUTE_RES = [
    # 'the China-to-France smartphone route'
    re.compile(rf"\b(?:the\s+)?(?P<a>{_PLACE})-to-(?P<b>{_PLACE})\s+(?P<goods>[\w-]+\s+)?(?:route|path|thread)\b"),
    # 'the smartphone route from China to France'
    re.compile(rf"\b(?:the\s+)?(?P<goods>(?!(?:a|an|the|this|that|its|their)\s)[\w-]+\s+)?(?:route|path|thread)\s+from\s+(?P<a>{_PLACE})\s+to\s+"
               rf"(?P<b>{_PLACE})"),
]
THROUGH_RE = re.compile(r"\s*,?\s+(?:(?:in\s+)?transit(?:ing)?\s+)?(?:through|via)\s+Luxembourg\b")
ROUTES_CHART_RE = re.compile(r"\b(?:a\s+|the\s+)?(?:map\s+of\s+(?:the\s+)?routes|routes?\s+(?:chart|map))\b",
                             re.IGNORECASE)


def no_routes(text: str) -> str:
    """Trade statistics do not link what was bought to what was sold again: 'the smartphone route from China to
    France' -> 'the smartphone thread (bought mostly from China, sold mostly to France)'; 'through Luxembourg'
    goes; 'a routes chart' is 'a map of partners'. A sentence that says plainly the data cannot show it stays."""
    out = []
    for sent in re.split(r"(?<=[.!?])(\s+)", text or ""):
        if re.search(r"\b(?:cannot|can't|can’t|does\s+not|do\s+not)\b[^.]*\b(?:show|link|tell)", sent):
            out.append(sent)
            continue
        for rx in ROUTE_RES:
            sent = rx.sub(lambda m: (f"the {(m.group('goods') or '').strip() + ' ' if m.group('goods') else ''}"
                                     f"thread (bought mostly from {m.group('a')}, sold mostly to {m.group('b')})"),
                          sent)
        sent = THROUGH_RE.sub("", sent)
        sent = ROUTES_CHART_RE.sub(lambda m: ("the " if m.group(0).lower().startswith("the") else "a ")
                                   + "map of partners", sent)
        out.append(sent)
    return "".join(out)


# --------------------------------------------------------------------------- ranks written in a reply
ORDINAL_WORDS = {w: i for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth "
    "fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split(), 1)}
ORDINAL_RE = re.compile(r"\b(?:(\d{1,3})(?:st|nd|rd|th)|(" + "|".join(ORDINAL_WORDS) + r"))\b"
                        r"(?!\s+(?:time|year|half|quarter|month|week|day|step|edition|code|test)s?\b)", re.IGNORECASE)
RANK_CONTEXT_RE = re.compile(r"\brank\w*|\bplace\b|\bposition\b|\bfrom\s+\w+\s+to\s+\w+|\b(?:largest|biggest|"
                             r"top|main)\b", re.IGNORECASE)


def ranks_in(text: str) -> list[tuple[str, int, str]]:
    """The ranks written in a reply ('fell from fourth to ninth', '13th'): [(as written, rank, sentence)], only in
    sentences about ranks or places."""
    _, body, _ = split_reply(text)
    out = []
    for sent in body:
        if not RANK_CONTEXT_RE.search(sent):
            continue
        for m in ORDINAL_RE.finditer(sent):
            n = int(m.group(1)) if m.group(1) else ORDINAL_WORDS[m.group(2).lower()]
            out.append((m.group(0), n, sent))
    return out


def unverified_ranks(text: str, ranks: list[tuple]) -> list[str]:
    """The ranks of a reply that no ranking of this turn shows for a partner (or product) named in the same
    sentence; a rank the page showed on its own ('4th') counts for any name."""
    out = []
    loose = {n for name, n in ranks or [] if not name}
    for written, n, sent in ranks_in(text):
        low = sent.lower()
        named = {r for name, r in ranks or [] if name and name in low}
        if n not in named and n not in loose:
            out.append(written)
    return out


def unverified_in(text: str, turn: Turn) -> list[str]:
    """Figures and ranks of a reply that no tool of this turn showed."""
    return unverified_figures(text, turn.seen) + unverified_ranks(text, turn.ranks)


# --------------------------------------------------------------------------- the forecaster's scenarios
SCENARIO_RE = re.compile(r"scenario|\bif\s+(?:it|the\s+(?:smartphone\s+)?line)\s+(?:stops|continues|stays|ends)|"
                         r"one-off|honest\s+range|plausible\s+(?:range|values)|range\s+of\s+plausible", re.IGNORECASE)
TESTED_RE = re.compile(r"\b12\s+months\s+ahead\s+only|less\s+certain|twelve\s+months\s+ahead\s+only", re.IGNORECASE)


def forecast_said_right(text: str, turn: Turn) -> str:
    """The forecaster's reply says the scenarios as the line's level, right after the central figure and its
    interval (the model's own scenario sentences, 'one-off' or 'the honest range', go), never calls the line
    one-off, and says plainly when a year beyond the tested 12 months is given."""
    if turn.scenarios:
        _, body, _ = split_reply(text)
        drop = [b for b in body if SCENARIO_RE.search(b)]
        if len(drop) < len(body):
            text = remove_sentences(text, drop)
            _, body, _ = split_reply(text)
            anchor = next((b for b in body if re.search(r"interval|between|range", b, re.IGNORECASE)), body[0])
            text = text.replace(anchor, anchor + " " + turn.scenarios, 1)
    text = re.sub(r"\b(?:a\s+|the\s+)?(?:recent\s+)?one-off\s+(?=(?:smartphone\s+)?line\b)", "the ", text)
    text = re.sub(r"\bthe\s+the\b", "the", text)
    if turn.beyond_tested and not TESTED_RE.search(text):
        text = add_before_tail(text, "The method was tested up to 12 months ahead only, so the later years are "
                                     "less certain.")
    return text


# --------------------------------------------------------------------------- events next to the breaks
_PCT_PAREN_RE = re.compile(r"\s*\((?=[^()]*%)(?=[^()]*\b(?:exports?|imports?|trade)\b)[^()]*\)")


def broad_event_totals(text: str, turn: Turn) -> str:
    """'the Suez Canal blockage (exports +17.3%, imports +19.7%)': next to a broad event, a whole year's change of
    all trade reads like the event's effect. Those brackets go; an event about one line keeps its line's change."""
    scoped = {sc.get("label") for sc in turn.scoped or []}
    broad = [e["label"] for e in turn.event_breaks or [] if e.get("label") not in scoped]
    if not broad:
        return text

    def fix(m: re.Match) -> str:
        before = text_[max(0, m.start() - 120):m.start()]
        before = re.split(r"[,;:]\s|\.\s", before)[-1]
        if any(_names_event(before, b) for b in broad) and not any(_names_event(before, sc) for sc in scoped if sc):
            return ""
        return m.group(0)
    text_ = text
    return _PCT_PAREN_RE.sub(fix, text)


# --------------------------------------------------------------------------- events about one line (the cause finder)
_EVENT_STOP = {"trade", "with", "from", "applied", "the", "and", "agreement", "price", "peak", "duties", "reporting"}


def scoped_total_problems(text: str, scoped: list[dict]) -> list[dict]:
    """Sentences of a reply that give an event about one product or partner (US steel tariffs) the change of ALL
    exports or imports that year ('+8.6%'): [{event, sentence, totals, line, line_figures}]."""
    out = []
    _, body, _ = split_reply(text)
    for s in scoped or []:
        words = [w for w in re.findall(r"[a-z]{4,}", (s.get("label") or "").lower()) if w not in _EVENT_STOP]
        if not words or not s.get("totals"):
            continue
        for sent in body:
            low = sent.lower()
            hit = [t for t in s["totals"] if t in sent]
            if hit and any(w in low for w in words):
                out.append({"event": s["label"], "sentence": sent, "totals": hit, "line": s.get("line"),
                            "line_figures": s.get("line_figures"), "year": s.get("year")})
    return out


def drop_sentences(text: str, sentences: list[str]) -> str:
    """The reply without these sentences (the last resort when a total is still quoted as an event's own)."""
    head, body, tail = split_reply(text)
    body = [b for b in body if b not in sentences]
    out = " ".join(([head] if head else []) + body)
    return out + ("\n\n" + "\n\n".join(tail) if tail else "")


# --------------------------------------------------------------------------- honesty checks on the reply, in code
LEAK_RE = re.compile(r"\b(?:the\s+tools?|tools?\s+(?:call|calls|requires?|does|did|returned|refused|failed)|placeholders?|"
                     r"validat\w+|parameters?|properties|schema|endpoints?|json|ok:\s*false|panel\s+ids?|the\s+setup|"
                     r"numbered\s+marks?|marks?\s+to\s+cite|"
                     r"encountered\s+(?:an?\s+|some\s+)?(?:issues?|problems?|errors?)|technical\s+(?:issues?|problems?|"
                     r"errors?)|(?:an?|the)\s+error\s+(?:occurred|came\s+up|message)|"
                     # how the page works inside: notes that 'prevented the update', ids, slots
                     r"(?:notes?|marks?|annotations?)\s+(?:on\s+it\s+|there\s+)?(?:prevented|blocked|stopped)|"
                     r"(?:prevented|blocked)\s+(?:the|an?|my)\s+(?:update|change|redraw\w*|chart)|"
                     r"(?:could\s+not|couldn't|was\s+unable\s+to|unable\s+to)\s+(?:re-?draw|update|replace)\b[^.]{0,80}"
                     r"\bbecause\b|existing\s+(?:notes|marks)\s+on\s+it|panel\s+(?:id|slot)s?|slots?\s+(?:are|is)\s+full|"
                     # process talk: what the agent tried and did not manage ('I could not add a note to the chart this
                     # time'): the reader sees what is on the page, never the attempts
                     r"(?:could\s+not|couldn(?:'|’)t|was\s+unable\s+to|unable\s+to|wasn(?:'|’)t\s+able\s+to|"
                     r"did\s+not\s+manage\s+to)\s+(?:add|draw|mark|place|put|highlight|annotate|plot|include|attach|"
                     r"re-?draw|update|label)|this\s+time)\b",
                     re.IGNORECASE)


def leaked(text: str) -> list[str]:
    """Sentences of a reply that talk about tools, placeholders or errors (the reader never sees those)."""
    _, body, _ = split_reply(text)
    return [s for s in body if LEAK_RE.search(s)]


CLAIM_RE = re.compile(r"\b(?:I\s+(?:have\s+|also\s+)?(?:highlighted|marked|annotated|circled|flagged|pointed\s+out)|"
                      r"(?:is|are|was|were|been)\s+(?:also\s+)?highlighted|highlighted\s+(?:on|in)\b|"
                      r"I\s+(?:have\s+)?added\s+(?:a\s+|an\s+|two\s+|three\s+)?(?:notes?|marks?|highlights?|"
                      r"annotations?))", re.IGNORECASE)
CHART_WORDS = {"scatter": {"scatter"}, "map": {"choropleth", "flowmap"}, "treemap": {"treemap"},
               "heatmap": {"heatmap"}, "grid": {"heatmap"}, "butterfly": {"butterfly"}, "waterfall": {"waterfall"},
               "crossroads": {"flow"}, "table": {"table"}, "bar chart": {"bar"}, "bump": {"bump"},
               "line chart": {"line", "compare"}, "trend line": {"line"}}


def unbacked_claims(text: str, marked_types: set, any_marks: bool) -> list[str]:
    """Sentences that say something was highlighted or marked when no mark was drawn there in this turn (the
    scatter reply that claimed highlights it never drew)."""
    _, body, _ = split_reply(text)
    out = []
    for s in body:
        if not CLAIM_RE.search(s):
            continue
        if not any_marks:
            out.append(s)
            continue
        named = set().union(*[types for w, types in CHART_WORDS.items() if re.search(rf"\b{w}", s, re.IGNORECASE)])
        if named and not named & marked_types:
            out.append(s)
    return out


ALIGN_RE = re.compile(r"\b(?:align\w*|lin(?:e|es|ed|ing)\s+up|coincid\w*|match\w*|correspond\w*)\b", re.IGNORECASE)
BREAK_RE = re.compile(r"\bbreaks?\b|\bshifts?\b|\bchange\s*points?\b", re.IGNORECASE)
NEG_ALIGN_RE = re.compile(r"\b(?:no|not|n't|never|none|neither|nor|without)\b(?:\s+\w+){0,3}?\s+(?:align\w*|lin\w+\s+up|"
                          r"coincid\w*|match\w*|correspond\w*|(?:trend\s+)?break\w*)", re.IGNORECASE)


def _names_event(sentence: str, label: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9-]{2,}", (label or "").lower()) if w not in _EVENT_STOP]
    low = sentence.lower()
    return any(w in low for w in words) if words else (label or "").lower() in low


def alignment_problems(text: str, event_breaks: list[dict]) -> list[str]:
    """Sentences that say events line up with the trend breaks when a named event (or, said in general, any
    event drawn) has no break within 12 months."""
    if not event_breaks or all(e["lines_up_with_a_break"] for e in event_breaks):
        return []
    _, body, _ = split_reply(text)
    out = []
    for s in body:
        if not (ALIGN_RE.search(s) and BREAK_RE.search(s)) or NEG_ALIGN_RE.search(s):
            continue
        named = [e for e in event_breaks if _names_event(s, e["label"])]
        if not named or any(not e["lines_up_with_a_break"] for e in named):
            out.append(s)
    return out


def alignment_sentence(event_breaks: list[dict]) -> str:
    """What the dates do say, measured: which events have a trend break within 12 months."""
    near = [e for e in event_breaks if e["lines_up_with_a_break"]]
    far = [e for e in event_breaks if not e["lines_up_with_a_break"]]
    names = lambda es: _join([e["label"] for e in es])  # noqa: E731
    if not near:
        return "None of these events falls within 12 months of a trend break, so their dates do not line up with the breaks."
    breaks = sorted({e["nearest_break"] for e in near})
    others = names(far) if len(far) <= 3 else f"the other {len(far)} events"
    return (f"Only {names(near)} {'falls' if len(near) == 1 else 'fall'} within 12 months of a trend break ("
            + _join([fmt.period_words(b) for b in breaks]) + f"); {others} "
            + ("has" if len(far) == 1 else "have") + " no break within 12 months.")


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def list_start_sentence(question: str, text: str, events_from: Optional[str], breaks: list[str]) -> Optional[str]:
    """'The curated list of events starts in 2017 ...', when the question or the breaks reach before the list
    and the reply does not say so."""
    start = str(events_from or "2017")[:4]
    if start in (text or ""):
        return None
    early = sorted({b[:4] for b in breaks or [] if b[:4] < start})
    asks_early = any(y < start for y in re.findall(r"\b(?:19|20)\d\d\b", question or "")) or \
        re.search(r"\b(?:earlier|older|before)\b", question or "", re.IGNORECASE)
    about_changes = re.search(r"\bbreaks?\b|\bshift|\bchanges?\b|\bline\s+up\b|\bevents?\b", question or "",
                              re.IGNORECASE)
    asked = re.findall(r"\b(?:19|20)\d\d\b", question or "")
    if asked and min(asked) >= start and not asks_early:
        return None   # 'the months since 2023': breaks of the series before the list are not what was asked
    if not (asks_early or (about_changes and early)):
        return None
    return (f"The curated list of events starts in {start}, so no event in it covers the earlier changes"
            + (f" ({_join(early)})" if early else "") + ".")


def remove_sentences(text: str, sentences: list[str], replace: Optional[dict] = None) -> str:
    """The reply without these sentences (or each replaced by ``replace[sentence]``), lines and bullets kept."""
    for s in sentences:
        text = text.replace(s, (replace or {}).get(s, ""))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def add_before_tail(text: str, addition: str, inline: bool = True) -> str:
    """Add a sentence (or lines) at the end of the body, before '_Next: ...?_' and 'On the page: ...'."""
    lines = text.rstrip().split("\n")
    tail = []
    while lines and (not lines[-1].strip() or TAIL_RE.match(lines[-1])):
        tail.insert(0, lines.pop())
    body = "\n".join(lines).rstrip()
    last = body.split("\n")[-1].lstrip() if body else ""
    if inline and re.match(r"^(?:[-*•]|\d+[.)])\s", last):
        inline = False   # never glued onto the last bullet of a list
    body = (body + " " + addition) if inline else (body + "\n\n" + addition)
    tail_text = "\n\n".join(t.strip() for t in tail if t.strip())
    return body + ("\n\n" + tail_text if tail_text else "")


NEXT_RE = re.compile(r"^\s*_?\s*next:\s*(.+?)\s*_?\s*$", re.IGNORECASE)
SUGGEST = {
    "visualizer": ["Where do the goods go?", "What does Luxembourg sell?", "Bought vs sold, partner by partner",
                   "Show me 2020", "Compare Luxembourg with Belgium", "Exports month by month since 1999"],
    "trend_finder": ["When did exports shift for good?", "Why did imports change in 2025?",
                     "Which events line up with the changes since 1999?", "What should we expect for 2026?"],
    "cause_finder": ["What did COVID-19 do to trade?", "When did exports shift for good?",
                     "What should we expect for 2026?"],
    "forecaster": ["Which forecasting method works best here?", "What should we expect for imports in 2026?",
                   "Why did exports jump in 2025?"],
    "general": ["Why did exports jump in 2025?", "Where do the goods go?", "What should we expect for 2026?"],
}


def _words(q: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (q or "").lower())) - {"the", "a", "an", "me", "of", "in", "for", "to", "s"}


def similar(a: str, b: str) -> bool:
    """Two questions that ask the same ('Show me 2025?' and 'Show me 2025'; 'When did exports shift for good since
    1999?' and 'When did exports shift for good?')."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    if wa == wb or len(wa & wb) / len(wa | wb) >= 0.75:
        return True
    small = min(len(wa), len(wb))
    return small >= 3 and len(wa & wb) / small >= 0.8


ODD_NEXT_RE = re.compile(r"^(?:what|which|how)\s+(?:else\s+)?(?:would|do|can|could|shall)\s+(?:you|i|we)\s+"
                         r"(?:like|want|wish|help|explore|do)\b|^(?:anything\s+else|is\s+there\s+anything)\b|"
                         r"\bmy\s+(?:question|data)\b|\bpersonal\s+data\b|\b(?:do|does)\s+(?:you|it)\s+(?:store|keep)\b|"
                         r"\bprivacy\b", re.IGNORECASE)
MAX_NEXT_WORDS = 12


# 'Compare total exports to the United States across these countries?': a follow-up the data answers (each
# country's own trade with one partner), kept
PEER_PARTNER_NEXT_RE = re.compile(r"\b(?:across|among|between|for|of|in)\s+(?:these|those|the\s+other|other|the\s+same)"
                                  r"\s+(?:eu\s+)?countries\b", re.IGNORECASE)
PARTNER_FLOW_RE = re.compile(r"\b(?:to|from|with)\s+(?:the\s+)?(?:United\s+States|US|USA|China|Russia|Japan|India|"
                             r"United\s+Kingdom|UK|Switzerland)\b", re.IGNORECASE)


def peer_partner_next(q: str) -> bool:
    """A follow-up asking other countries' trade with one partner. The data holds it now (each EU country's trade
    with the US, China, the UK and Switzerland is loaded, any other partner is fetched on first use): never dropped."""
    return False


def odd_next(q: str) -> bool:
    """A follow-up that is not a real next question about the data: 'What would you like to explore?', one about
    privacy (the reply just answered it), or longer than about 12 words."""
    return bool(ODD_NEXT_RE.search(q or "")) or len(re.findall(r"[\w’'-]+", q or "")) > MAX_NEXT_WORDS \
        or peer_partner_next(q)


def no_repeated_sentences(text: str) -> str:
    """Drop a sentence that repeats an earlier one of the same paragraph-free reply almost word for word
    ('The central figure assumes its recent rise goes on.' written by the model, then added by the code).
    Short sentences (under six words), bullets, the quote line and the tail lines ('_Next: ...?_', 'Source: ...')
    are kept: a quote repeats the first finding on purpose."""
    out_lines, seen = [], []
    for line in (text or "").split("\n"):
        if NEXT_RE.match(line) or line.lstrip().startswith(("Source:", "_", "- ", "* ", "|", "“", '"')):
            out_lines.append(line)
            continue
        kept = []
        for s in re.split(r"(?<=[.!?])\s+", line):
            words = _words(s)
            if len(words) >= 6 and any(similar(s, t) for t in seen):
                continue
            if len(words) >= 6:
                seen.append(s)
            kept.append(s)
        out_lines.append(" ".join(kept))
    return "\n".join(out_lines)


def fix_next(text: str, role: str, asked: list[str], page_year: Optional[str] = None,
             others: Optional[list[str]] = None) -> str:
    """The follow-up chip never suggests a question already asked in this conversation (the one just answered
    first), nor 'Show me YEAR' for the year the page already shows, nor what another agent of the plan is answering
    in this very turn (the forecast, when the forecaster is in the plan)."""
    from .tools import show_year
    # a '_Next: ...?_' the model wrote at the end of a sentence goes on its own line (never two of them)
    text = re.sub(r"[ \t]+(_\s*next:[^\n]*?_)[ \t]*(?=\n|$)", r"\n\n\1", text or "", flags=re.IGNORECASE)
    nexts = [ln for ln in text.split("\n") if NEXT_RE.match(ln)]
    if len(nexts) > 1:   # keep the last one the model wrote
        keep = nexts[-1]
        text = "\n".join(ln for ln in text.split("\n") if not NEXT_RE.match(ln) or ln is keep)
        text = re.sub(r"\n{3,}", "\n\n", text)
    if nexts and not NEXT_RE.match(text.rstrip().split("\n")[-1]):
        # a '_Next:' line above the tail ('Source: ...'): it goes last, never a second one after it
        keep = nexts[-1]
        text = "\n".join(ln for ln in text.split("\n") if not NEXT_RE.match(ln)).rstrip() + "\n\n" + keep.strip()
        text = re.sub(r"\n{3,}", "\n\n", text)

    def stale(q: str) -> bool:
        y = show_year(q.rstrip("?") if q else q)
        if role != "forecaster" and "forecaster" in (others or []) and FORECAST_TALK_RE.search(q or ""):
            return True
        return odd_next(q) or any(similar(q, a) for a in asked or []) \
            or bool(y and page_year and y == str(page_year)[:4])

    lines = text.rstrip().split("\n")
    m = NEXT_RE.match(lines[-1]) if lines else None
    # the question alone: '_Next: X?_?_' (a model's doubled ending) is X, never 'X?_?'
    current = re.sub(r"^[\s_*]+|[\s_*?]+$", "", m.group(1)) if m else None
    if current and not stale(current):   # kept, in sentence case
        lines[-1] = f"_Next: {current[:1].upper() + current[1:]}?_"
        return "\n".join(lines)
    pick = next((s for s in SUGGEST.get(role, []) + SUGGEST["general"] if not stale(s)), None)
    line = f"_Next: {pick.rstrip('?')}?_" if pick else None
    if m:
        lines = lines[:-1] + ([line] if line else [])
        return "\n".join(lines).rstrip()
    return text if not line else text.rstrip() + "\n\n" + line


FORECAST_TALK_RE = re.compile(r"\b(?:forecast\w*|predict\w*|projection\w*|project(?:s|ed)?\s+to|outlook|expect\w*|"
                              r"next\s+(?:two\s+)?years?|coming\s+(?:months?|years?)|the\s+future|"
                              r"(?:will|is\s+likely\s+to|could|may)\s+(?:likely\s+|probably\s+)?(?:rise|fall|grow|"
                              r"continue|keep|stay|remain|slow|pick\s+up|rebound))\b", re.IGNORECASE)


PAST_CHANGE_RE = re.compile(r"\b(?:rose|fell|grew|jumped|surged|dropped|increas\w*|decreas\w*|contribut\w*|drivers?|"
                            r"drove|added)\b", re.IGNORECASE)
OUTLOOK_RE = re.compile(r"\b(?:forecast\w*|band|interval|scenario\w*|backtest\w*|naive|method|model|20(?:2[6-9]|3\d)|"
                        r"next|expect\w*|observed|test\w*)\b", re.IGNORECASE)


def others_part(text: str, role: str, others: Optional[list[str]]) -> str:
    """The reply without sentences about what another agent of the plan answers in this turn: the forecast is the
    forecaster's part, so the trend finder (or any other) says nothing about what comes next."""
    if role == "forecaster" and "trend_finder" in (others or []):
        # the other way round: the trend finder explains the past change, the forecaster only what comes next
        _, body, _ = split_reply(text)
        talk = [s for s in body if PAST_CHANGE_RE.search(s) and not OUTLOOK_RE.search(s)]
        if talk and len(talk) < len(body):
            log.info("forecaster: left the past change to the trend finder: %s", [t[:60] for t in talk])
            return remove_sentences(text, talk)
        return text
    if role in ("forecaster", "general") or "forecaster" not in (others or []):
        return text
    _, body, _ = split_reply(text)
    talk = [s for s in body if FORECAST_TALK_RE.search(s)]
    if not talk or len(talk) >= len(body):
        return text
    log.info("%s: left the forecast to the forecaster: %s", role, [t[:60] for t in talk])
    return remove_sentences(text, talk)


def with_backtest_table(text: str, backtest: dict) -> str:
    """The forecaster's answer to 'which method works best' carries the backtest table as it came from
    /insights/forecast, one line per method (the model's own list, if any, is replaced)."""
    lines = [ln for ln in text.split("\n") if not re.match(r"^\s*[-*•]\s+", ln)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    table = backtest["intro"] + "\n" + "\n".join(backtest["bullets"])
    return add_before_tail(text, table, inline=False)


REPLACED_RE = re.compile(r"\b(?:replac\w*|(?:takes?|took|taking)\s+the\s+place\s+of|in\s+place\s+of|swapp?\w*)\b",
                         re.IGNORECASE)


def with_replaced(text: str, replaced: list[dict]) -> str:
    """Say which earlier chart a new one took the place of (the page keeps three agent charts), from the page's own
    answer (the replaced panels of /view/ops) only: the model's own words about replacing go, true or not, and the
    sentence is added only to the reply of the agent whose chart made room."""
    _, body, _ = split_reply(text)
    claims = [s for s in body if REPLACED_RE.search(s)]
    if claims:
        text = remove_sentences(text, claims)
    titles = [r.get("title") for r in replaced or [] if r.get("title")]
    if not titles:
        return text
    lang = reply_language(text)
    if lang not in REPLACED_LINE:
        return text      # a language the line is not written in: better nothing than English in a French reply
    which = REPLACED_AND[lang].join(f"“{t}”" for t in titles[:2])
    return add_before_tail(text, REPLACED_LINE[lang].format(which=which))


REPLACED_LINE = {"en": "The new chart takes the place of {which}; pin a chart to keep it.",
                 "fr": "Le nouveau graphique remplace {which} ; épinglez un graphique pour le garder.",
                 "de": "Das neue Diagramm ersetzt {which}; heften Sie ein Diagramm an, um es zu behalten."}
REPLACED_AND = {"en": " and ", "fr": " et ", "de": " und "}
_LANG_WORDS = {
    "en": {"the", "and", "is", "are", "of", "to", "with", "for", "from", "was", "were", "it", "on", "that", "by"},
    "fr": {"les", "des", "est", "sont", "une", "pour", "avec", "dans", "sur", "du", "la", "le", "et", "ont", "au",
           "aux", "été", "qui", "que", "de"},
    "de": {"der", "die", "das", "und", "ist", "sind", "mit", "für", "von", "den", "dem", "nicht", "ein", "eine", "im",
           "auf", "wurden", "wurde"},
}


def reply_language(text: str) -> str:
    """'en', 'fr', 'de' or 'other': the language most of the reply's small words belong to (English by default)."""
    words = re.findall(r"[a-zà-ÿß]+", re.sub(r"\*\*[^*]+\*\*|“[^”]*”|_next:[^\n]*", " ", (text or "").lower()))
    counts = {lang: sum(w in ws for w in words) for lang, ws in _LANG_WORDS.items()}
    other = max(("fr", "de"), key=lambda k: counts[k])
    return other if counts[other] > counts["en"] else "en"


def clear_reply(title: str, cleared: dict, text: str) -> str:
    """The reply to 'Clear the page', said from what stayed: never 'all charts' when kept marks remain."""
    kept, pinned = cleared.get("kept") or [], cleared.get("pinned") or []
    out = f"**{title}.** I cleared what the agents drew: the page is back to its starting view"
    stays = []
    if kept:
        stays.append(f"the {'mark' if len(kept) == 1 else 'marks'} you kept ({' '.join(f'[{n}]' for n in kept)})")
    if pinned:
        stays.append(f"the {'panel' if len(pinned) == 1 else 'panels'} you pinned")
    one = len(kept) + len(pinned) == 1
    out += (", except " + " and ".join(stays) + (", which stays." if one else ", which stay.") if stays else ".")
    nxt = [ln for ln in text.strip().split("\n") if NEXT_RE.match(ln)]
    return out + ("\n\n" + nxt[-1].strip() if nxt else "")


def _marked_types(turn: Turn, box: Toolbox) -> set:
    """The panel types the marks of this turn sit on (a highlight on the map: {'choropleth'})."""
    if not turn.marks:
        return set()
    try:
        spec = turn.spec or box.spec()
    except Exception:
        return set()
    types = {p["id"]: p["type"] for p in spec.get("panels", [])}
    return {types.get(m["target"]) for m in spec.get("marks", []) if m["id"] in turn.marks} - {None}


# --------------------------------------------------------------------------- goods named right
def drawn_codes(turn: Turn, box: Toolbox) -> list[str]:
    """The product codes this turn drew (its panels' product and parent, its marks' codes), else those it read."""
    spec = turn.spec
    codes: list[str] = []
    if spec and (turn.panels or turn.marks):
        for p in spec.get("panels", []):
            if p["id"] in turn.panels or any(m["id"] in turn.marks and m["target"] == p["id"]
                                             for m in spec.get("marks", [])):
                codes += box._panel_codes(p)
        for m in spec.get("marks", []):
            if m["id"] in turn.marks:
                prm = m.get("params") or {}
                codes += products.product_codes(list(prm.get("codes") or []) + [prm.get("product")])
    return list(dict.fromkeys(codes)) or list(turn.products)


def naming_problems(text: str, turn: Turn, box: Toolbox) -> list[dict]:
    """Phrases of a reply that name the goods drawn with an official title's words that are not about them
    ('nuclear reactors' for chapter 84, which is machinery)."""
    return products.misnamed(text, drawn_codes(turn, box))


NOT_MY_PART_RE = re.compile(r"\b(?:can(?:no|'|’)?t|could\s+not|couldn(?:'|’)t|unable\s+to|not\s+able\s+to|"
                            r"(?:does|do|did)(?:\s+not|n(?:'|’)t)\s+(?:provide|give|offer|include))\b[^.]*"
                            r"\b(?:forecast\w*|plan|predict\w*|projection\w*|next\s+(?:two\s+|2\s+)?years?)\b",
                            re.IGNORECASE)


HOW_MANY_RE = re.compile(r"\bhow\s+(?:many|much\s+(?:in\s+)?(?:volume|weight|quantity))\s*(?P<what>[a-z][a-z-]*(?:\s+[a-z]+)?)?|"
                         r"\b(?:number|count|quantity)\s+of\s+(?P<what2>[a-z][a-z-]*)|"
                         r"\b(?P<unit>tonnes?|tons?|kilo(?:gram)?s?|units|pieces|litres?)\b", re.IGNORECASE)
_HOW_MANY_SKIP = {"of", "the", "we", "did", "do", "does", "luxembourg", "were", "are", "is", "euros", "euro"}


def units_sentence(question: str, text: str, turn: Optional[Turn] = None) -> Optional[str]:
    """'How many cars did we export?': the data has quantities (get_quantities). Only when the goods asked about
    have none published (the tool said so) does the reply say, first, that the page measures them in euros ('The
    page measures trade in euros, not the number of cars.'), unless it already does. None when the question asks
    for no count, or a count was found."""
    m = HOW_MANY_RE.search(question or "")
    if not m or turn is None or not turn.quantities or any(q.get("use") for q in turn.quantities):
        return None
    _, body, _ = split_reply(text)
    if body and re.search(r"\beuros?\b|\bvalues?\b", body[0], re.IGNORECASE) and \
            re.search(r"\bnot\b|\bonly\b", body[0], re.IGNORECASE):
        return None
    words = (m.group("what") or m.group("what2") or "").lower().split()
    words = [w for w in words if w not in _HOW_MANY_SKIP][:1]
    if m.group("unit") or (words and re.fullmatch(r"tonnes?|tons?|kilo(?:gram)?s?|units|pieces|litres?", words[0])):
        return "No quantity is published for these goods: the data has their value in euros only."
    thing = words[0] if words else "items"
    thing = thing if thing.endswith("s") else thing + "s"
    return f"No number of {thing} is published: the data has their value in euros only."


LEVEL_Q_RE = re.compile(r"\b(?:jump\w*|ris(?:e|es|ing|en)|rose|grow\w*|grew|f[ae]ll\w*|drop\w*|levels?|since|"
                        r"trends?|records?|histor\w*|earlier|unprecedented|over time|every year|years|change\w*)\b",
                        re.I)


def history_sentence(text: str, turn: Turn, box: Toolbox, question: str, show_codes: bool = True) -> Optional[str]:
    """'The 2025 level is not unprecedented: ...': a reply about a line whose code a revision created (smartphones,
    CN8 85171300 from 2022) says the goods' earlier history, from the API, when it does not already. Only when the
    question is about their level or its change, not about who sold or bought them ('a map of partners')."""
    codes = [c for c in turn.products if products.predecessor(c)]
    if not codes or re.search(r"\b(?:2015|2016|2019)\b", text or ""):
        return None
    if not LEVEL_Q_RE.search(question or ""):
        return None
    pre = products.predecessor(codes[0])
    flow = "IMPORT" if re.search(r"\bimport|\bbu(?:y|ys|ying|ought)\b", question or "", re.I) and \
        not re.search(r"\bexport|\bsel(?:l|ls|ling)\b|\bsold\b", question or "", re.I) else "EXPORT"
    try:
        rows = box.get("/insights/series", flow=flow, freq="A", start="2015", end=pre["until"], product=pre["before"])
    except Exception:  # the API is down: the reply stays as it is
        return None
    top = sorted([r for r in rows or [] if r.get("value")], key=lambda r: -r["value"])[:2]
    if not top:
        return None
    top = sorted(top, key=lambda r: str(r["period"]))
    years = " and ".join(f"{fmt.eur(r['value'])} in {r['period']}" for r in top)
    word = "exports" if flow == "EXPORT" else "imports"
    turn.see(years)
    code = f"{pre['level']} {pre['before']}, " if show_codes else ""
    return (f"The 2025 level is not unprecedented: {word} of {pre['goods']} ({code}the earlier code, which held "
            f"{pre['now']} with other {pre['goods']} until {pre['until']}) were {years}.")


def unsaid_no_trade(text: str, turn: Turn) -> list[dict]:
    """Goods the user asked for that Luxembourg did not trade, which the reply does not say plainly."""
    return [n for n in turn.no_trade if not products.says_no_trade(text, n["goods"])]


def honesty_issues(role: str, text: str, turn: Turn, box: Toolbox) -> list[str]:
    """What the model is asked to fix once before the reply goes out: tool talk, claims of marks it did not draw,
    a line's share given the wrong base, events said to line up with breaks that are far from them."""
    out = []
    names = naming_problems(text, turn, box)
    if names:
        out.append(" ".join(f"\"{n['phrase']}\" is not what HS {n['chapter']} holds: chapter {n['chapter']} is "
                            f"{n['name']} (its official title only starts with those words). Name it "
                            f"\"{n['name']}\"." for n in names))
    unsaid = unsaid_no_trade(text, turn)
    if unsaid:
        out.append("Say plainly first: " + " ".join(n["sentence"] for n in unsaid) + " Do not describe another "
                   "chapter as those goods.")
    leaks = leaked(text)
    if leaks:
        out.append("The reader never sees tools, placeholders, errors or what you tried and could not do, so these "
                   "sentences must go; say only what is on the page: " + " | ".join(leaks) + ".")
    claims = unbacked_claims(text, _marked_types(turn, box), bool(turn.marks))
    if claims:
        out.append("These sentences claim marks you did not draw in this turn (say only what the tools drew, with "
                   "their numbers): " + " | ".join(claims) + ".")
    shares = misattributed_shares(text, turn.shares)
    if shares:
        out.append(" ".join(f"{p['written']} is a share of one product line, not of a whole flow: say \"{p['say']}\"."
                            for p in shares))
    unspecified = unspecified_problems(text, turn.unspecified)
    if unspecified:
        out.append("Only the trade with no partner specified (codes QV, QW, QS: partner_not_specified) may be called "
                   "\"confidential\" or \"not specified\"; the ranking's other bucket is \"other partners\" "
                   "together. Fix these sentences: " + " | ".join(unspecified) + ".")
    if role == "cause_finder":
        aligns = alignment_problems(text, turn.event_breaks)
        if aligns:
            rows = "; ".join(f"{e['label']} ({fmt.period_words(e['at'])}): nearest break "
                             f"{fmt.period_words(e['nearest_break'] or '')}, {e['months_apart']} months apart"
                             for e in turn.event_breaks)
            out.append(f"An event lines up with a trend break only within 12 months of it ({rows}). These sentences "
                       "claim more: " + " | ".join(aligns) + ".")
    return out


def honest_in_code(role: str, question: str, text: str, turn: Turn, box: Toolbox, limits: Optional[dict]) -> str:
    """The same checks after the rewrite, fixed in code whatever the model wrote: tool talk and unbacked claims
    go, a misattributed share is said with its base, alignment claims are measured, the list's start is said,
    and the forecaster's method answer carries the backtest table."""
    from .router import asks_method
    if naming_problems(text, turn, box):   # the official title's words that are not about the code: its short name
        text = products.rename(text, drawn_codes(turn, box))
    if role not in ("forecaster", "general"):
        _, body, _ = split_reply(text)
        disclaim = [s for s in body if NOT_MY_PART_RE.search(s)]
        if disclaim and len(disclaim) < len(body):
            text = remove_sentences(text, disclaim)
    said = history_sentence(text, turn, box, question, not limits or limits["show_codes"]) if re.search(r"\b2025\b", text or "") \
        and reply_language(text) == "en" else None   # a French reply gets no English sentence
    if said:                               # a young code's goods have a history: the reply says it
        text = add_before_tail(text, said)
    unsaid = unsaid_no_trade(text, turn)
    if unsaid:                             # goods asked for that were not traded: said plainly, first
        head, body, tail = split_reply(text)
        lead = " ".join(n["sentence"] for n in unsaid)
        text = " ".join(x for x in (head, lead, " ".join(body)) if x) + ("\n\n" + "\n\n".join(tail) if tail else "")
    leaks = leaked(text)
    if leaks:
        text = remove_sentences(text, leaks)
    claims = unbacked_claims(text, _marked_types(turn, box), bool(turn.marks))
    if claims:
        text = remove_sentences(text, claims)
    units = units_sentence(question, text, turn) if reply_language(text) == "en" else None
    if units:                              # 'how many': no quantity published for these goods, said first
        head, body, tail = split_reply(text)
        text = " ".join(x for x in (head, units, " ".join(body)) if x) + ("\n\n" + "\n\n".join(tail) if tail else "")
    if (leaks or claims) and turn.refused and not (set(turn.done) - QUIET_TOOLS - {"keep_marks"}):
        # it tried and the page refused: never the tool's error nor the attempt; with nothing else to say, how to
        # get the chart
        if not split_reply(text)[1]:
            text = "Ask for that chart in other words to have it drawn."
    elif not split_reply(text)[1]:
        text = add_before_tail(text, "I have nothing more to add on the page for this question.")
    shares = misattributed_shares(text, turn.shares)
    if shares:
        text = remove_sentences(text, [p["sentence"] for p in shares],
                                {p["sentence"]: p["say"][:1].upper() + p["say"][1:] + "." for p in shares})
    unspecified = unspecified_problems(text, turn.unspecified)
    if unspecified:
        text = remove_sentences(text, unspecified)
    if role == "cause_finder":
        aligns = alignment_problems(text, turn.event_breaks)
        if aligns:
            text = add_before_tail(remove_sentences(text, aligns), alignment_sentence(turn.event_breaks))
        breaks = turn.breaks
        if not breaks and re.search(r"\bbreaks?\b|\bshift|\bchanges?\b|\bline\s+up\b", question or "", re.IGNORECASE):
            try:
                breaks = box.known_breaks()
            except Exception:  # the page or the API is down: no breaks to speak of
                breaks = []
        said = list_start_sentence(question, text, turn.events_from, breaks)
        if said:
            text = add_before_tail(text, said)
    if role == "forecaster":
        text = forecast_said_right(text, turn)
    if role == "cause_finder" and re.search(r"\bbreaks?\b|\bshift|\bline\s+up\b", question or "", re.IGNORECASE):
        text = broad_event_totals(text, turn)
    if role == "forecaster" and asks_method(question) and not limits:
        if turn.backtest is None:   # the model answered without it: the table comes from /insights/forecast anyway
            try:
                flow = "IMPORT" if re.search(r"\bimports?\b", question or "", re.IGNORECASE) else "EXPORT"
                turn.see(box.get_backtest(flow))
            except Exception as exc:  # the API is down: the reply stays as the model wrote it
                log.warning("forecaster: no backtest table (%s)", exc)
        if turn.backtest:
            text = with_backtest_table(text, turn.backtest)
    return text


_API_PATH_RE = re.compile(r"`?(?:GET\s+)?/(?:insights|trends|view|product)/[\w/\-]*(?:\.[\w/\-]+)*(?:\?[^\s,;)`]*[^\s,;)`.])?`?")
def _method_words(paths: list[str]) -> list[str]:
    """The methods behind API paths written in a reply, as the catalogue names them in a sentence."""
    ends = {method_names.split_url(p_.strip("`").replace("GET ", "").strip())[0].rstrip("/") for p_ in paths}
    return [w for e, w in method_names.IN_A_SENTENCE.items() if e in ends]


MORE_PLACES = ("Singapore", "Canada", "Switzerland", "Norway", "Turkey", "Türkiye", "India", "Brazil", "Mexico",
               "South Korea", "Korea", "Australia", "Ukraine", "Russia", "Israel", "Saudi Arabia", "United Arab Emirates",
               "Taiwan", "Hong Kong", "Vietnam", "Thailand", "Malaysia", "South Africa", "Morocco", "Egypt")


_PARAM = r"`[A-Za-z_]+=[^`\n]*`"
_PARAMS_RE = re.compile(rf"(?:\s+(?:with|using|mit|avec))?\s*{_PARAM}(?:\s*,?\s*(?:and|und|et)?\s*{_PARAM})*")


def plain_words(text: str) -> str:
    """The chat names the data and the method in plain words: no API paths ('/insights/series?...' becomes
    'Eurostat trade data', with the method), and partners by name, never by their code ('China (CN)', 'to FR')."""
    from agents.kit.view import COUNTRY_NAMES
    lines = []
    for line in (text or "").split("\n"):
        paths = _API_PATH_RE.findall(line)
        if paths:
            words = ["Eurostat trade data (ds-045409)"] + _method_words(paths)
            first = True

            def sub(m):
                nonlocal first
                if first:
                    first = False
                    return "; ".join(words)
                return ""
            line = _API_PATH_RE.sub(sub, line)
            line = re.sub(r"(?:\s*[,;]\s*)+(?=[.)]|$)", "", line)
            line = re.sub(r"\s*[,;]\s*(?:[,;]\s*)+", "; ", line)
            line = re.sub(r"[ \t]{2,}", " ", line)
        line = _PARAMS_RE.sub("", line)                                           # 'with `flow=EXPORT`, `level=2`'
        line = re.sub(r"\bHS\s?(\d{8})\b", r"CN8 \1", line)                     # 'HS 72163390' -> 'CN8 72163390'
        names = {c: n for c, n in COUNTRY_NAMES.items() if len(c) == 2 and c not in ("LU",)}
        places = list(names.values()) + list(MORE_PLACES)
        line = re.sub(r"\b(" + "|".join(re.escape(n) for n in places) + r")\s*\((?:HS\s?|CN8?\s?)?\d{4,8}\)",
                      r"\1", line)                                              # 'Germany (HS 72163390)' -> 'Germany'
        line = re.sub(r"\s*\((" + "|".join(names) + r")\)", "", line)            # 'China (CN)' -> 'China'
        line = re.sub(r"\b(from|to|with|and|or|than|by|in) (" + "|".join(names) + r")(?![\w/-])(?!\s+\d)",
                      lambda m: f"{m.group(1)} {names[m.group(2)]}", line)        # 'to FR' -> 'to France'
        lines.append(line)
    return "\n".join(lines)


LLM_SECONDS = 45.0   # one model call of a specialist: a step takes 2 to 6 s; never the client's default 10 min


def bounded(llm: Any) -> None:
    """The OpenAI client of ``llm`` with a timeout of LLM_SECONDS and one retry, once (a client that hangs costs
    a minute and a half at most, then the reply says the model did not answer)."""
    client = getattr(llm, "client", None)
    if client is None or getattr(llm, "_lux_bounded", False) or not hasattr(client, "with_options"):
        return
    try:
        llm.client = client.with_options(timeout=LLM_SECONDS, max_retries=1)
        llm._lux_bounded = True
    except Exception as exc:  # another client: left as it is
        log.info("could not bound the LLM client (%s)", exc)


def run_specialist(role: str, question: str, llm: Any, box: Toolbox, *, earlier: Optional[list[str]] = None,
                   history: str = "", task: Optional[str] = None, max_steps: int = 12,
                   parameters: Optional[dict] = None, asked: Optional[list[str]] = None,
                   others: Optional[list[str]] = None) -> Reply:
    """Answer one question as one specialist: read, draw, reply. ``llm`` has BAF's ``predict_with_tools``.
    ``others``: the other agents of the plan (what they answer is not this one's part)."""
    started = time.monotonic()
    bounded(llm)
    turn: Turn = box.begin(question)
    tools = box.tools(ROLE_TOOLS[role])
    by_name = {t.name: t for t in tools}
    schemas = [t.openai_schema for t in tools]
    title = TITLES[role]
    reader = None
    try:
        spec = box.spec()
        page_text = page.summary(spec, me=role)
        reader = persona_of(spec)
    except Exception as exc:  # the page is down: the agent can still answer from the data
        page_text = f"(the page could not be read: {exc})"
    try:
        coverage = box.coverage()
    except Exception:  # the data's coverage falls back to the defaults of the prompt
        coverage = None
    try:
        catalogue = method_names.fetch(box.api)
    except Exception:  # the names built in
        catalogue = None
    if role not in ("forecaster", "general") and "forecaster" in (others or []):
        task = ((task or question) + " The forecaster answers what comes next in this turn: say nothing about "
                "forecasts, expectations or the coming years, and do not suggest it as the next question.")
    if role == "forecaster" and "trend_finder" in (others or []):
        task = ((task or question) + " The trend finder explains what changed in this turn: do not explain the past "
                "change, its drivers or products, and draw no waterfall; answer only what comes next.")
    box.others = [o for o in (others or []) if o != role]
    system = system_prompt(role, page_text, earlier or [], history, task, reader=reader, coverage=coverage,
                           methods_line=method_names.prompt_lines(catalogue))
    messages: list[dict] = [{"role": "user", "content": question}]
    params = parameters or api_params(getattr(llm, "name", ""), 0.2, 900)
    limits = reader_limits(reader)
    repaired = pushed = rescoped = shortened = honest = False
    text: Optional[str] = None
    step = 0
    corrected: list[str] = []       # figures of a draft that no tool showed (sent back once)
    failed: dict[str, int] = {}     # tool -> calls that failed in this turn
    for step in range(max_steps):
        last = step == max_steps - 1
        if last and messages[-1]["role"] == "tool":
            # the step budget is spent: answer now with what the tools gave, never "I ran out of steps"
            messages.append({"role": "user", "content": (
                "No steps left: write the reply now from what the tools returned in this turn, saying plainly "
                "what you could not do. No more tool calls.")})
        try:
            response = llm.predict_with_tools(messages=messages, tools=[] if last else schemas,
                                              system_message=system, parameters=params)
        except Exception as exc:
            log.exception("%s: the LLM call failed", role)
            text = f"I could not finish: the language model did not answer ({type(exc).__name__})."
            break
        if response.tool_calls:
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in response.tool_calls]})
            for c in response.tool_calls:
                t = by_name.get(c.name)
                if t and failed.get(c.name, 0) >= 2:
                    content = json.dumps({"ok": False, "error": (
                        f"{c.name} already failed twice in this turn: do not call it again. Change approach (the "
                        "chapter, the total, another chart) or answer with what you have.")})
                else:
                    content = t.call(c.arguments) if t else f"ERROR: there is no tool {c.name!r}"
                if content.startswith("ERROR") or '"ok":false' in content[:40]:
                    failed[c.name] = failed.get(c.name, 0) + 1
                log.info("%s -> %s(%s) -> %s", role, c.name, json.dumps(c.arguments, ensure_ascii=False)[:300],
                         content[:300])
                messages.append({"role": "tool", "tool_call_id": c.id, "content": content})
            continue
        text = response.text or ""
        nudge = None if pushed or role == "general" or step >= max_steps - 2 else draw_nudge(turn)
        if nudge:
            pushed = True
            log.info("%s: pushed back (%s)", role, nudge[:60])
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": nudge})
            continue
        bad = unverified_in(text, turn)
        if bad and not repaired and step < max_steps - 1:
            repaired = True
            corrected = list(bad)
            log.info("%s: figures not seen in tools: %s", role, bad)
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": (
                "Check before it goes out: these figures were not returned by any tool in this turn: "
                + ", ".join(bad) + ". Rewrite the reply with only figures exactly as the tools wrote them (call "
                "the read tools again for a figure of an earlier answer), or without those figures. Never write "
                "about tools, turns or this check in the reply. Reply with the new text only.")})
            continue
        scoped = scoped_total_problems(text, turn.scoped)
        if scoped and not rescoped and step < max_steps - 1:
            rescoped = True
            log.info("%s: a total quoted for an event about one line: %s", role, [p["event"] for p in scoped])
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "Check before it goes out: " + " ".join(
                f"In \"{p['sentence']}\", {', '.join(p['totals'])} is the change of ALL of Luxembourg's exports or "
                f"imports in {p['year']}, but {p['event']} is about {p['line']}: its own change was "
                f"{json.dumps(p['line_figures'], ensure_ascii=False)}." for p in scoped)
                + " Rewrite the reply giving that event its line's change, written exactly as above (you may keep "
                  "the totals only in a sentence about all trade). Reply with the new text only."})
            continue
        issues = honesty_issues(role, text, turn, box)
        if issues and not honest and step < max_steps - 1:
            honest = True
            log.info("%s: honesty check: %s", role, [i[:60] for i in issues])
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "Check before it goes out: " + " ".join(issues)
                             + " Rewrite the reply, keeping the figures exactly as before. Reply with the new text "
                               "only."})
            continue
        problem = length_problem(text, limits)
        if problem and not shortened and step < max_steps - 1:
            shortened = True
            log.info("%s: too long for the reader (%s)", role, problem)
            cap = limits["max_sentences"]
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": (
                f"Too long for this reader: {problem}. Rewrite it in at most {cap} sentence{'s' if cap > 1 else ''}"
                + (f", each under {limits['max_words']} words" if limits["max_words"] else "")
                + ("" if limits["show_codes"] else ", with no HS or CN8 codes")
                + ": keep the one figure that answers the question and the [n] of what you drew, figures exactly "
                  "as before, then the _Next: ...?_ line. Reply with the new text only.")})
            continue
        break
    else:
        text = text or "I ran out of steps before finishing; ask me a narrower question."
    if text is None:
        text = "I ran out of steps before finishing; ask me a narrower question."
    spec = turn.spec
    if spec is None:
        try:
            spec = box.spec()
        except Exception:
            spec = None
    numbers = page.page_numbers(spec, turn.marks) if spec else []
    if spec:
        text = clean_citations(text, page.numbers(spec))   # the [n] on the page now (numbers are stable, 1.6)
    text = method_names.plain_method_words(plain_words(fmt.house_style(text)))
    text = no_routes(products.earlier_as_now(text))   # mobile phones before 2022; no route through Luxembourg
    text = already_there(text, turn)                  # 'I drew' only what was drawn in this turn
    text = no_repeated_sentences(text)                # a sentence the model and the code both wrote, once
    if role == "cause_finder":
        text = fmt.no_causal_claims(text)
    still = scoped_total_problems(text, turn.scoped)
    if still:   # the model kept a total under an event about one line: that sentence goes
        text = drop_sentences(text, [p["sentence"] for p in still])
    text = honest_in_code(role, question, text, turn, box, limits)
    text = others_part(text, role, others)   # what comes next is the forecaster's part when it is in the plan
    text = fit_reader(text, limits)   # in code, whatever the model wrote: the reader's sentences, no codes
    if turn.cleared is not None:      # 'Clear the page': said from what stayed
        text = clear_reply(title, turn.cleared, text)
    else:                             # the page keeps three agent charts: say which one made room, if one did
        text = with_replaced(text, turn.replaced)
    text = fix_next(text, role, asked or [question], page_year=((spec or {}).get("context") or {}).get("period"),
                    others=others)
    text = with_citation(text, reader)   # the source line and a quotable sentence, for readers who ask for them
    text = cite_numbers(with_title(text, title), numbers)
    return Reply(agent=role, text=text, marks=list(turn.marks), numbers=numbers, panels=list(turn.panels),
                 version=turn.version, sources=list(turn.sources), actions=list(turn.actions), steps=step + 1,
                 unverified=unverified_in(text, turn), corrected=corrected,
                 seconds=round(time.monotonic() - started, 1))
