# Agents: how they change the page

The page is drawn from a **view spec**, a JSON document the backend holds. Agents never touch the
page: they send typed **ops** that edit the spec, and every browser redraws from it.

## The architecture in ten lines

1. The backend holds the spec (`data/view_state.json`, or `VIEW_STATE_PATH`); version 0 is
   `app/backend/view/baseline.json`.
2. An agent reads numbers from `/insights/*` (with `agents/kit`) to decide what to say.
3. It sends a batch of ops: `POST /view/ops`.
4. The backend validates the batch (schema, then semantics), then pre-flights every new query
   against `/insights`. A 4xx or `loaded: false` rejects the whole batch.
5. It assigns mark ids and numbers (monotonic, never reused), makes a new version, and saves it.
6. It pushes the full spec to every browser over SSE (`GET /view/events`, event `spec`).
7. The browser only renders. Panels and marks carry **queries**, never data, and the renderer
   fetches them.
8. Text gets its figures from `{fact}` placeholders that the renderer fills from the API.
9. **No number on the page ever comes from an LLM.** Typed-in amounts and percentages are rejected.
10. The user keeps or removes marks from the "Ask the data" panel, which sends ops as agent `user`.

## Files

| What | Where |
|---|---|
| Spec schema (JSON Schema 2020-12, `$id` `besser-view-spec-1.0`) | `contract/view-spec.schema.json` |
| Ops schema (`$id` `besser-view-ops-1.0`, refers to the spec schema) | `contract/ops.schema.json` |
| Baseline spec, version 0 | `contract/examples/initial.json` (copied to `app/backend/view/baseline.json`) |
| The spec after batches 01 to 03, as the backend stores it | `contract/examples/spec_after_03.json` |
| Example batches | `contract/examples/01_smartphone_thread.json` ... `05_clear.json` |
| Example batches of contract 1.1 | `06_maps_and_waterfall.json` (visualizer: choropleth, flowmap, thread), `06b_waterfall.json` (cause finder: waterfall 2025 vs 2024), `07_heatmap_and_breaks.json` (trend finder: month x year heatmap and trend breaks), `08_context_2020.json` (user: set_context 2020) |
| Example batches of contract 1.2 (comparisons) | `09_comparisons.json` (visualizer: Luxembourg against EU peers, partners in 2024 and 2025, exports against imports, 2025 month by month against 2024), `09b_line_bar_comparisons.json` (trend finder: the same as options on a line and a bar) |
| Example batch of contract 1.4 (personas) | `10_persona_journalist.json` (user: the page drawn for a journalist: `set_context` with the persona, and the routes map) |
| Validator, used by the backend | `app/backend/view/validate.py` (`validate_spec`, `validate_ops`) |
| Agent kit | `agents/kit/` (`InsightsClient`, `ViewClient`) |

`app/backend/view/schema/` holds copies of the two schemas so that the backend stays
self-contained. If you change a schema in `contract/`, copy it there too:
`tests/test_view_contract.py` fails until the copies match.

## The spec

```json
{"spec_version": "1.0", "version": 3, "title": "...", "next_mark_n": 8, "next_story_n": 4,
 "context": {"period": "2025", "product": "TOTAL"},
 "panels": [...], "layout": {"rows": [{"panels": ["kpi_imports", "kpi_balance", "kpi_exports"]}]},
 "marks": [...]}
```

**Contract 1.1** is additive: `spec_version` stays `1.0`, every 1.0 spec and batch is still valid,
and the schemas' `$id`s do not change. It adds eight panel types, the `trend_break` mark, a mark
`period`, the page `context` with the `set_context` op and query placeholders, and four endpoints.
See "Contract 1.1" below.

**Baseline panel ids:** `headline` (text), `kpi_imports`, `kpi_balance`, `kpi_exports` (kpi),
`crossroads` (flow; queries `in` and `out`), `trend` (line; queries `imports` and `exports`),
`mix_export`, `specialisation`, `peers` (bar; query `main`). Layout rows: headline / three figures /
crossroads / trend / the quiet row of three.

**Panel:** `{id, type, title, subtitle?, text?, facts?, value?, queries, options?, pinned, agent, provenance?, context?}`
(`context`: the panel's own year and goods, contract 1.5)

- `type`: `kpi`, `bar`, `line`, `table`, `flow` or `text`; since 1.1 also `waterfall`, `choropleth`,
  `flowmap`, `heatmap`, `treemap`, `butterfly`, `bump` and `scatter` (see "The new panel types");
  since 1.2 `compare` (see "Comparisons").
- `queries`: `[{key, endpoint, params, label?, tone?}]`. `tone` is the data colour: `exports`
  (sky) or `imports` (steel). A flow panel has exactly two queries, keyed `in` and `out`.
- `kpi` needs `value` (the name of the fact shown as the big figure) and `facts`. `text` is the
  caption under it.
- `text` panels need `text` and `provenance`.
- `options` holds rendering hints, as scalars or short lists of strings: `x`, `y`, `format`,
  `orientation`, `limit`, `exclude_codes`, `reference`, `gap_fill`, `highlight_focus`,
  `center_label`, `columns`. Since 1.7 `measure: "growth"` on a bar (growth per year since a year: see
  "Contract 1.7"). Since 1.2 the comparison options `compare`, `base_period`, `reference`
  (a query key), `focus`, `facet`, `align`, `balance` (line, compare) and `compare`, `sort`, `focus`
  (bar, compare): see "Comparisons".

**Mark:** `{id, n, story_n?, kind, agent, target, series?, note, facts?, params, query?, status, thread?, period?, provenance}`
(`story_n`: its storyline's page number, set by the backend, contract 1.6)

| kind | on | params | needs |
|---|---|---|---|
| `thread` | flow, flowmap | `in_code`, `out_code`, `label`, `product?` | |
| `trend` | line | `label?`, `start?`, `end?` | `query` on `/insights/trend` |
| `event` | line, compare (series over time) | `at`, `label`, `until?`, `event_id?` | |
| `forecast` | line | `label?` | `query` on `/insights/forecast` |
| `annotation` | line, bar, waterfall, compare | `x`, `label` (**never `y`**: the renderer reads it from the data). On a line or a comparison over time `x` is a period (and `series` names the line); on a bar, waterfall or two snapshots, a row code | |
| `highlight` | bar, flow, table, choropleth, flowmap, treemap, butterfly, bump, scatter, compare of two snapshots: `codes`; line, compare over time: `from` and `to` | `label?` | |
| `trend_break` (1.1) | line, heatmap (`month_year` or `product_year`) | `at`, `label?`, `before?`, `after?`, `from?`, `to?` | `query`, if any, on `/trends/analyze`; `before`/`after` name facts of the mark |

`period` (1.1, optional) is the year or month the mark is about (`2025`, `2020-03`). When the user
moves the page to another period, the page dims the marks whose `period` does not match (a year
matches its months). Leave it out for marks about a span or a whole series.

`series` names one of the target panel's queries (`exports`). `status` is `proposed` until the
user keeps the mark. Marks that share a `thread` value form one storyline, and the page shows them
as one numbered note. The number shown is the storyline's `story_n` (contract 1.6): given when its first
mark is added, and kept (see "Contract 1.6").

**Periods** are `2025` or `2025-03`. Parameters named `period`, `previous`, `start`, `end` and
`base` are checked.

### Facts: how figures get into text

A text (a panel's `text`, or a mark's `note`) holds `{name}` placeholders. Each name is a fact:

```json
"facts": {
  "imports": {"query": "imports", "path": "total", "format": "eur"},
  "bought_cn": {"endpoint": "/insights/ranking", "params": {"by": "partner", "period": "2025", "flow": "IMPORT", "product": "85171300"}, "path": "items.[code=CN].value", "format": "eur"},
  "balance": {"op": "diff", "of": ["exports", "imports"], "format": "eur_signed"}
}
```

- `{query, path}` reads one of the panel's own queries. In a mark, `"query": "self"` reads the
  mark's own `query`.
- `{endpoint, params, path}` makes a call of its own, so marks can cite anything.
- `{op, of}` derives a value from other facts: `diff` (a-b), `sum`, `ratio` (a/b), `growth` (a/b-1)
  or `abs`.

**Path** segments, separated by dots:

| Segment | Meaning |
|---|---|
| `total` | a key |
| `0`, `-1` | an index (`-1` is the last item) |
| `[code=FR]` | the first item whose field equals the value |
| `#length` | the length of an array |
| `#position` | the 1-based position of the item just picked with `[field=value]` |

For example: `items.[code=FR].share`, `-1.value`, `[is_focus=true].#position`, `peers.#length`.
`agents.kit.get_path(data, path)` resolves a path the way the page does.

**Format:**

| Format | Example |
|---|---|
| `eur` | €25.1 bn, €613 m |
| `eur_signed` | −€8.8 bn |
| `pct` | a fraction, so 0.86 shows as 86% |
| `pct_signed` | a fraction with its sign |
| `percent`, `percent_signed` | already in percent, so 5.1 shows as +5.1% |
| `int` | a whole number |
| `number` | a number |
| `ordinal` | 27th |
| `period` | a period |
| `text` | a name, as is |
| `qty` (1.8) | a count or a weight, the number alone: 37,654 (the text names what: "{value} cars") |
| `qty:<unit>` (1.8) | a count and its unit: `qty:cars` shows 37,654 cars |
| `tonnes` (1.8) | a weight: 122,464 t |

A placeholder already carries its unit, so do not type `%` or `€` around it.

The validator rejects typed-in figures in `text` and `note`: currency amounts (`€1.2 bn`, `3 million`)
and percentages (`86%`). Years and codes (`2018`, `CN8 85171300`) are fine.

## Ops

`POST /view/ops`

```json
{"agent": {"name": "cause_finder", "run_id": "cause_finder-3f2a9c"}, "base_version": 1,
 "message": "Four events line up with breaks in the series.",
 "ops": [{"op": "add_mark", "mark": {"kind": "event", "target": "trend", "thread": "events",
          "params": {"at": "2020-03", "label": "COVID-19"}, "note": "Lockdowns from March 2020."}}]}
```

| op | fields | effect |
|---|---|---|
| `upsert_panel` | `panel`, `after?` | add, or replace the panel with the same id (it keeps its place); a new panel gets a new row after the row of `after` (else at the end), unless the agents' area is full: then it takes the place of the oldest agent panel not kept (see "The agents' area"). Refused (`pinned`) on a pinned panel unless sent by `user` |
| `remove_panel` | `id`, `force?` | refused when pinned (for everyone: unpin first), or when it carries marks (unless `force`, which removes them too) |
| `set_layout` | `rows: [{label?, panels: [1-3 ids]}]` | every panel exactly once |
| `add_mark` | `mark` (no id, n or status), `tmp_id?` | the backend assigns `id` `m<n>`, `n` and `status: proposed`, and fills `agent` and `provenance` |
| `update_mark` | `id`, `set: {note, facts, params, series, thread, query, period}` | the merged mark is validated again |
| `keep_mark` / `remove_mark` | `id` | keep sets `kept`; numbers are never reused |
| `pin_panel` | `id`, `pinned` | pinned panels survive `clear` and are never replaced, changed or removed by an agent; only `user` unpins (an agent's `pinned: false` on a pinned panel is refused) |
| `clear` | `scope: marks / agent_panels / all / page`, `agent?` | never removes kept marks, pinned panels or the page's own edits (agent `user`); a baseline panel an agent rewrote goes back to its baseline version. `page` is the user's only ("Clear the page"): every panel not kept goes, the page's own included; `POST /view/reset` brings the starting page back |
| `set_title` | `title` | |
| `set_context` (1.1) | `period?`, `product?`, `flow?`, `start?` and `persona?` (1.4) (at least one) | merges into `spec.context`; panels with `$period`, `$previous` or `$product` follow (see "The page context"), except what a panel has its own context for; `persona` draws the page for another reader (see "Contract 1.4") |
| `set_panel_context` (1.5) | `id`, `period?`, `start?`, `product?` (each may be `null`) | the panel's whole own context: it wins over the page's for that panel, its facts and its marks; a field left out or `null` follows the page again, `{id}` alone makes the panel follow the page. Only what the panel's queries follow (see "Contract 1.5"). Refused (`pinned`) on a pinned panel unless sent by `user` |

- **Batch rules:** a batch holds 1 to 50 ops. It is atomic, and checked in order, so a mark may
  target a panel upserted earlier in the same batch. Later ops can refer to a new mark by its
  `tmp_id`.
- **One `run_id` per batch:** sending the same `run_id` again is a retry and changes nothing.
- **Agent names:** `baseline` is reserved; the person at the page is `user`.
- **Responses:**
  - 200: `{"version", "assigned": {"<tmp_id>": {"id", "n"}}, "marks_added", "warnings": [], "replaced": [], "spec"}`;
    `replaced` lists the agent panels a new one took the place of: `[{"id", "title", "type", "agent", "by"}]`
    (`by` is the new panel's id), so the reply can say "this map takes the place of the waterfall"
  - 422: `{"detail", "errors": [{"path": "/ops/0/mark/target", "code": "unknown_target", "message"}]}`
  - 409: `{"detail", "version", "spec"}` when `base_version` is stale. Re-read the spec and decide
    again.
- **Error codes:** `schema`, `required`, `unexpected`, `unknown_endpoint`, `unknown_param`,
  `missing_param`, `bad_param`, `bad_period`, `unknown_target`, `wrong_target`, `unknown_series`,
  `unknown_fact`, `unknown_query`, `figure_in_text`, `unknown_panel`, `not_in_layout`,
  `duplicate_in_layout`, `unknown_mark`, `pinned`, `has_marks`, `too_many_ops`, `reserved_agent`,
  `duplicate_key`, `duplicate_id`, `duplicate_tmp_id`, `bad_mark_id`, `bad_queries`, `fact_cycle`,
  `agent_mismatch`, `bad_placeholder` (1.1), and `preflight` (backend only).
- **Other routes:** `GET /view` (`?since=N` for polling), `GET /view/events` (SSE, event `spec`,
  15 s heartbeat), `POST /view/undo`, `/view/redo`, `/view/reset` (each makes a new version),
  `GET /view/history`.

## The agent kit

```python
from agents.kit import InsightsClient, ViewClient, AGENTS, fact_api, fact_from, fact_op, query, get_path

api = InsightsClient("http://localhost:8000")
movers, movers_url = api.movers("product", "2025", "2024", flow="EXPORT", level=4)
top = movers["up"][0]                       # read numbers to decide what to say...

view = ViewClient("http://localhost:8000", "trend_finder")
view.register(AGENTS["trend_finder"]["title"], AGENTS["trend_finder"]["color"],
              capabilities=[{"kind": "find_movers", "produces": ["thread", "highlight", "bar"],
                             "description": AGENTS["trend_finder"]["can"], "inputs": ["period", "flow"]}],
              examples=["Why did exports jump in 2025?"])
stop = view.keep_alive()                    # re-registers every 30 s (the registry TTL is 60 s)

phones = {"by": "partner", "period": "2025", "product": top["code"]}
ops = [
    view.thread_mark("crossroads", "CN", "FR", "Smartphones, China to France",
                     "{bought} bought from China and {sold} sold to France.",   # ...but never type them
                     product=top["code"], thread="smartphones", tmp_id="phones", sources=[movers_url],
                     facts={"bought": fact_api("/insights/ranking", {**phones, "flow": "IMPORT"}, "items.[code=CN].value", "eur"),
                            "sold": fact_api("/insights/ranking", {**phones, "flow": "EXPORT"}, "items.[code=FR].value", "eur")}),
]
result = view.send(ops, message="Most of the 2025 export rise is one product line.", base_version=view.spec()["version"])
result["assigned"]["phones"]                # {"id": "m1", "n": 1}
```

- **`InsightsClient(base_url)`:** every call returns `(data, source_url)`, where `source_url` is
  relative (`/insights/...`). It raises `InsightsError` on a 4xx and `NotLoadedError` on
  `loaded: false`. The methods are `meta`, `ranking`, `ranking_records`, `ladder`, `series`,
  `movers`, `compare`, `compare_records`, `specialisation`, `specialisation_records`, `trend`,
  `trends`, `forecast`, `events`, `flows`, and since 1.1 `matrix`, `tree`, `analyze`
  (`/trends/analyze`), `seasonal_heatmap` (`/trends/seasonal-heatmap`), and `get(endpoint, **params)`
  (any `/insights` or `/trends` endpoint).
- **`ViewClient(base_url, agent_name)`:** the builders return op dicts. They are `add_mark`,
  `thread_mark`, `event_mark`, `trend_mark`, `forecast_mark`, `annotation_mark`, `highlight_mark`,
  `trend_break_mark` (1.1), `update_mark`, `keep_mark`, `remove_mark`, `upsert_panel`,
  `remove_panel`, `pin_panel`, `clear`, `set_title`, `set_context` (1.1), `set_panel_context` (1.5) and `set_layout`. Every
  mark builder takes `period=` (1.1). `send(ops, message, base_version, run_id)` checks the batch against
  the schema locally (it raises `ValueError` if the batch is invalid), POSTs it, and raises
  `ViewError` on a 409 or 422, with `.errors` and `.spec`. Also: `spec()`, `register(...)`,
  `heartbeat()` and `keep_alive()`.
- **Pieces:** `query(endpoint, params, key=, label=, tone=)`, `fact_from(key, path, fmt)`,
  `fact_api(endpoint, params, path, fmt)` and `fact_op(op, a, b, fmt)`.
- **Panel builders (1.1):** each returns a panel dict for `upsert_panel`: `panel(id, type, title,
  queries, ...)` for any type, and `waterfall_panel`, `choropleth_panel`, `flowmap_panel`,
  `heatmap_panel`, `treemap_panel`, `butterfly_panel`, `bump_panel`, `scatter_panel`, and since 1.7
  `growth_bar_panel` (growth per year since `start`, see "Contract 1.7"). They default
  to `period="$period"` (and `previous="$previous"`), so the panel follows the page. Extra keyword
  arguments are dimension filters (`partner="FR"`, `product="$product"`) or panel fields
  (`subtitle`, `text`, `facts`, `sources`); `options={...}` adds rendering hints.
- **Comparisons (1.2):** `ViewClient.compare_partners`, `compare_products`, `compare_with_peers`,
  `compare_years` and `compare_flows` each return a list of ops (the `upsert_panel`, plus a
  `highlight` or `annotation` mark when asked), ready for `send`; the pieces are `compare_panel`,
  `series_query`, `compare_facts` and `series_key`. See "Comparisons".
- **Context (1.1):** `set_context(period=, product=, flow=, persona=)` (`persona` since 1.4); `PERIOD`, `PREVIOUS`, `PRODUCT` (the placeholder strings),
  `previous_period("2025") == "2024"` and `fill_placeholders(params, spec["context"])`, to read the
  numbers a panel will show before you write about them.
- **Using the kit from outside the repository:** `agents` is not an installed package. Put the
  repository root on the path before the import, and install the kit's two dependencies (`httpx`
  and `jsonschema`; both are in the root `requirements.txt`). The kit reads the schemas from
  `contract/` next to `agents/`, so keep a whole checkout, not just `agents/kit/`.

  ```python
  import sys
  sys.path.insert(0, r"C:\path\to\statec_hackathon_2026")
  from agents.kit import InsightsClient, ViewClient
  ```

### Where the page puts what you add

The page places the baseline panels itself, in four fixed sections: the headline; the three
figures over the crossroads; the trend; the quiet row of three. A panel an agent adds goes where
`spec.layout` puts it: right after the section of the last baseline panel before it in the layout.
So `upsert_panel` with `after: "crossroads"` lands between the crossroads and the trend,
`after: "trend"` under the trend, and no `after` (a new row at the end) under the row of three.
`set_layout` orders the added rows the same way; it never moves the baseline sections. Only a panel
with no layout slot at all lands in an "Added by the agents" section at the end. Marks always appear
on their `target`.

Each chart has its own quiet Year (or From/To) and Goods menu (contract 1.5): it sends
`set_panel_context` as agent `user`, so only that chart moves; "Follow the page" sends `{id}` alone.
The page context itself has no control of its own on the page any more: `set_context` from an
agent ("Show me 2020") or a reader's default view moves every chart without its own context.
Clicking a bar, a partner on
the crossroads, a dot or a table row sends a `highlight` mark as agent `user` (with the mark's
`period`); a second click removes it. Read those as "the person is looking at this". A panel the
user changed still counts as the page's own: it shows no "Added by" label, and an agent's `clear`
leaves it.

The page dims a mark whose `period` is not the period shown and tags it "about 2025"; it does the
same for an agent's text, panel or page title pinned to other years (queries with literal periods
only, or a title naming other years). In the chat, "[2]" in a reply becomes the numbered circle of
storyline 2, which brings the mark into view when clicked; a last line "Next question: ...?" (or a
`follow_up` field on the reply) becomes a chip that asks it.

### The agents' area: replace, don't append

The agents share a few slots on the page: **3 agent panels** by default (`VIEW_MAX_AGENT_PANELS` on the
backend). An agent panel is one an agent added: owned by an agent (not `baseline`, not `user`) and not
one of the baseline's ids, so the headline an agent rewrites, and the panels the user's own controls
or a persona add, never count.

- **A new panel, when the area is full,** takes the place of the oldest agent panel the user has not
  kept: the same layout slot (the page does not jump; `after` is then ignored), and that panel's
  marks go with it. The 200 response says so in `replaced` and in a warning.
- **Kept** means pinned, or carrying a kept mark. A kept panel takes a slot but is never replaced; when
  every agent panel is kept, a new one is added below them (the page grows: the user chose it).
- **Oldest** is the order of `spec.panels`: a new panel is appended, and re-pointing an agent panel
  (`upsert_panel` with its id) moves it to the end, so the panel replaced is the one drawn longest ago.
- **Re-pointing** (`upsert_panel` with an existing id) keeps the panel's place and replaces nothing.
  It works on any panel that is not pinned, a baseline or user panel too, but only when the agent names
  that id: a new panel never pushes out the page's own panels.
- A page that already holds more agent panels than the slots (saved before the rule) shrinks back to
  the slots at the next new agent panel, as far as the kept ones allow.
- **Pinned panels** are never changed, re-pointed, removed or unpinned by an agent (`pinned`), and
  `clear` leaves them. Marks annotate and do not change the chart, so an agent may still add marks to
  a pinned panel.
- The validator simulates the replacement, so a later op in the same batch that targets a replaced
  panel or one of its marks is refused (`unknown_target`, `unknown_mark`).

**What an agent should do.** Re-use its own panel of the same kind (the same id: the map stays the
map, now of imports) rather than add a new id; read `pinned` in the spec and never target a pinned
panel with `upsert_panel` or `remove_panel`; and say in the reply what a new panel replaced (from
`replaced`). LuxInsights does all three (`app/agents/lux_insights/tools.py`, `show_panel`).

**On the page.** Every panel has a pin toggle in its header ("Keep this chart"; pinned: "Kept: agents
will not change it"), which sends `pin_panel` as `user`. Agent panels also have "Remove", which sends
`remove_panel` with `force` as `user` (hidden while the panel is pinned).

### Registration

`POST /agents/register`

```json
{"name": "trend_finder", "title": "Trend finder", "color": "#E2142D",
 "capabilities": [{"kind": "find_movers", "produces": ["thread", "highlight", "bar"],
                   "description": "Finds what changed and drills down to the product line",
                   "inputs": ["period", "flow"]}],
 "examples": ["Why did exports jump in 2025?"]}
```

`GET /agents` lists the agents seen in the last 60 s. In the panel, each agent card shows a colour
square, the agent's title and the one line from `capabilities[0].description`.

## The agents and their pens

| Agent | Title | Pen (marks, squares) | Ink (its text) | What it can do |
|---|---|---|---|---|
| `trend_finder` | Trend finder | `#E2142D` | `#B5101F` | Finds what changed and drills down to the product line |
| `cause_finder` | Cause finder | `#6D3FC0` | `#5A2FA8` | Lines changes up with events and trade agreements |
| `forecaster` | Forecaster | `#C17F12` | `#8A5A12` | Extends a series with an uncertainty band |
| `visualizer` | Visualizer | `#0F7B6C` | `#0B6156` | Puts any partner, product or year on the page |

A mark is drawn in its agent's pen, with its number in a filled circle. Data only ever comes in
sky blue (exports, `#0E8FD0`) and steel grey (imports, `#7A8796`). Any other colour on the page was
drawn by an agent.

## The endpoints added for agents

These come on top of the generated `/insights` endpoints. They take the same dimension filters
(`flow`, `partner`, `product`, `freq`, ...), plus the parameters below.

| Endpoint | Parameters | Response |
|---|---|---|
| `/insights/trend` | `start`, `end`, `adjust`, `method` | `{"rows": [{"period", "value", "fitted"}], "slope_per_year", "method", "loaded", "fetched_live"}` |
| `/insights/trends` | `by` (required), `start`, `end`, `level`, `parent`, `threshold`, `limit`, `method` | `{"by", "start", "end", "threshold", "n_significant", "total", "items": [{"code", "name", "rank", "value", "start_value", "growth", "cagr", "slope_per_year", "r2", "share", "is_new"}], "falling", "new", "loaded"}`; `growth` (= `cagr`) is the compound growth per year from `start` to `end`, a fraction; `value` is the end year's, `start_value` the start year's; only the `n_significant` codes with at least `threshold` of the total at the start or the end are ranked; `items` holds no new codes (no value at the start, so no rate): they are in `new` |
| `/insights/forecast` | `horizon`, `method` (`auto` default = `ensemble`; also `ets`, `theta`, `airline`, `gbm`, `stl_ets`, `snaive`; see docs/forecasting.md), `interval` (0.8), `start`, `end`, `adjust`, `aggregate` (`A` sums months into years), `backtest` | `{"rows": [{"period", "value", "low", "high"}], "method", "method_label", "interval", "backtest": {"period", "mape", "naive_mape", "mase_h1/3/6/12", "skill_h1/3/6/12", "coverage", "summary", "methods": [{"method", "label", "mase", "mase_h3", "mape_h12", "coverage", "skill", ...}]}, "scenarios": [{"id", "case": "stops"/"continues", "label", "rows"}], "loaded", "fetched_live"}`; `skill_h3` 0.4 = errors 40% smaller than the seasonal naive's 3 months ahead; `backtest.summary` is a sentence to quote; `scenarios` (the smartphone line stops or continues) only for a flow's total or chapter 85 against the world |
| `/insights/events` | `start`, `end`, `category`, `limit`; no dimension filters | `[{"id", "at", "until", "label", "category", "source"}]`, from a curated list |
| `/insights/flows` | `period` (required), `previous`, `max_items`, `threshold` | `{"period", "in": {"total", "items": [...]}, "out": {"total", "items": [...]}, "balance", "loaded"}`; the items are as in `/insights/ranking` |
| `/insights/matrix` (1.1) | `by`, `start`, `end` (required), `level`, `parent`, `max_items`, `threshold`, `measure` (`value` default, `share`, `rank`, `growth`) | `{"by", "measure", "start", "end", "periods": ["2015", ...], "rows": [{"code", "name", "values": [...]}], "loaded"}`; `values[i]` belongs to `periods[i]` (`null` where there is no data); `share` and `growth` are fractions, `rank` is 1-based within each period; rows are the `max_items` largest codes over the span, largest first |
| `/insights/tree` (1.1) | `by=product`, `period` (required), `depth` (1 to 3, default 2), `threshold` (`auto` default, or a share such as 0.02: smaller children go into an `OTHER` child) | `{"by", "period", "depth", "total", "children": [{"code", "name", "value", "share", "children": [...]}], "loaded"}`; nested HS2 > HS4 > HS6, `share` is a fraction of `total`; leaves have `children: []` |
| `/trends/analyze` (Ivan, 1.1) | `start`, `end`, `stl_period`, `min_size` (6), `penalty` (3.0); filters as `/insights/series` (`freq=M` for months) | see "Using Ivan's trend analysis" |
| `/trends/seasonal-heatmap` (Ivan, 1.1) | `start`, `end`, `top` (12); `freq` `M` or `Q` is needed; `product` is ignored | `{"cycle": 12, "rows": [{"code", "name", "pattern": [{"position", "label": "Jan", "average_effect", "average_effect_pct", "tag"}]}]}`; no `loaded` (a 503 when there is no data) |

### The shapes of the generated endpoints

Fact paths start at the top of the response. Some generated endpoints return a bare list, so the
path starts with an index (`0.value`), not with `rows.`.

| Endpoint | Required | Response | Path example |
|---|---|---|---|
| `/insights/series` | none | a bare list `[{"period", "value"}]`, oldest first; with `envelope=true`, `{"rows": [...], "loaded", "fetched_live"}` | `-1.value` (`rows.-1.value` with `envelope=true`) |
| `/insights/ranking` | `by`, `period` | `{"by", "period", "total", "coverage", "items": [{"rank", "code", "name", "value", "share", "cum_share"}], "other", "loaded"}`; `share` is a fraction | `items.[code=FR].share` (`pct`) |
| `/insights/ranking/records` | `by`, `period` | a bare list `[{"name", "code", "value", "share"}]`, with an `OTHER` row; `share` is in percent | `0.share` (`percent`) |
| `/insights/ladder` | `by`, `period` | `{"by", "period", "total", "n_items", "ladder": [{"threshold", "label", "items", "coverage"}]}` | `ladder.0.items` |
| `/insights/movers` | `by`, `period`, `previous` | `{"by", "period", "previous", "total", "previous_total", "up": [{"code", "name", "previous", "value", "change", "growth", "is_new"}], "down": [...], "new": [...]}`; `growth` is a fraction; `total` and `previous_total` (1.1) are the totals of the whole selection in each period, so a waterfall adds up | `up.0.change`, `previous_total` |
| `/insights/compare` | `by`, `period` | a bare list `[{"code", "name", "value", "share"}]`; `share` is a fraction | `[code=LU].share` |
| `/insights/compare/records` | `by`, `period` | a bare list `[{"code", "name", "value", "base_value", "growth", "is_focus"}]`; `growth` is in percent | `[is_focus=true].#position` (`ordinal`) |
| `/insights/specialisation` | `by`, `period` | `{"by", "period", "focus", "peers", "items": [{"code", "name", "value", "share", "peer_share", "rca"}]}` | `items.0.rca` |
| `/insights/specialisation/records` | `by`, `period` | a bare list `[{"name", "code", "rca", "value"}]` | `0.rca` |

When in doubt, call the endpoint and look: `agents.kit.get_path(data, path)` resolves a path
the way the page does.

The validator knows every endpoint's parameters: see `ENDPOINT_PARAMS` in
`app/backend/view/validate.py`. Add a parameter there, and to this table, when an endpoint grows
one.

## Contract 1.1

### The page context

The page is always about one period and one kind of goods: the user's Year and Goods controls.

```json
"context": {"period": "2025", "product": "TOTAL", "flow": "BOTH"}
```

- `period`: a year or a month. `product`: `TOTAL` (all goods) or an HS code (`85`, `8517`,
  `85171300`). `flow` (optional): `IMPORT`, `EXPORT` or `BOTH` (the default), an emphasis only.
  `persona` (optional, 1.4): who the page is drawn for (see "Contract 1.4").
- A spec without `context` reads as `{"period": "2025", "product": "TOTAL"}`.
- `set_context` changes it: `{"op": "set_context", "period": "2020"}`. The user's controls send it
  as agent `user`; an agent sends it when someone asks "show me 2020"
  (`contract/examples/08_context_2020.json`).

**Placeholders.** A query param (of a panel, a mark or an inline fact) may be a placeholder that
the page fills from the context:

| Placeholder | Becomes | Allowed in |
|---|---|---|
| `$period` | `context.period` (`2020`) | `period`, `previous`, `start`, `end`, `base` |
| `$previous` | the same period a year earlier (`2019`; `2020-03` gives `2019-03`) | `period`, `previous`, `start`, `end`, `base` |
| `$product` | `context.product` (`TOTAL`, `85`) | `product`, `parent` |
| `$start` (1.4) | `context.start`, the From year of a range; the period itself when there is none, when it is after the period, or when the period is a month | `start` |

```json
{"key": "main", "endpoint": "/insights/movers",
 "params": {"by": "product", "period": "$period", "previous": "$previous", "flow": "EXPORT"}}
```

- The spec keeps the placeholder as written. The renderer substitutes it before it fetches, and the
  backend's pre-flight substitutes it too (`view.validate.query_url(query, context)`;
  `fill_params`, `previous_period` and `context_of` are there as well). Any other `$...` value is
  rejected with `bad_placeholder`.
- Use a placeholder when the panel should follow the user ("the map of the year you are looking
  at"). Write the period out when the panel is about one period ("what changed from 2024 to 2025").
- A mark's `period` says which period it is about, so the page can dim it when the context moves
  elsewhere.

### The new panel types

Each type takes fixed query keys on fixed endpoints. The validator checks them (`bad_queries`,
`bad_param`), and the schema checks the options.

| Type | Queries (key: endpoint) | Options | Marks on it |
|---|---|---|---|
| `waterfall` | `main`: `/insights/movers` | `limit` (steps, 8), `show_other` (true) | `annotation` (`x` = a code) |
| `choropleth` | `main`: `/insights/ranking` with `by=partner` (measure `share`, `value`) or `/insights/flows` (measure `balance`) | `measure` (`share`, `value`, `balance`; default `share`), `extent` (`world`, `europe`; default `europe`) | `highlight` codes |
| `flowmap` | `main`: `/insights/flows` | `flow` (`imports`, `exports`, `both`; default `both`), `extent` | `thread`, `highlight` codes |
| `heatmap` | `main`: by `options.kind`: `month_year` on `/insights/series` with `freq=M`; `product_year` on `/insights/matrix`; `seasonal` on `/trends/seasonal-heatmap` with `freq` `M` or `Q`. Optional `analysis`: `/trends/analyze` (for facts) | `kind` (required), `scale` (`value`, `deviation`) | `trend_break` (not on `seasonal`) |
| `treemap` | `main`: `/insights/tree` | `limit` | `highlight` codes |
| `butterfly` | `in`: `/insights/ranking` with `flow=IMPORT`; `out`: the same with `flow=EXPORT`; both with the same `by` and `period` | `limit` | `highlight` codes |
| `bump` | `main`: `/insights/matrix` with `measure=rank` | `limit`, `labels` (codes to name) | `highlight` codes |
| `scatter` | `main`: `/insights/specialisation`; optional `growth`: `/insights/movers` or `/insights/trends` (joined on `code`) | `x`, `y` (required), `size`: field names (`rca`, `share`, `peer_share`, `value`, `growth`, `change`); `labels`, `log_x`, `log_y`, `reference_x`, `reference_y` | `highlight` codes |

Data colours stay sky (exports) and steel (imports): set `tone` on the queries. A choropleth of
balance shades from steel (deficit) to sky (surplus). Only marks use the agents' pens.

#### Waterfall: what made up a change

The bars start at `previous_total`, step through the largest movers (`up` and `down`), then one step
for everything else (`total - previous_total -` the steps shown), and end at `total`.

```json
{"id": "export_change", "type": "waterfall", "title": "What changed in exports, 2024 to 2025",
 "text": "Exports went from {before} in 2024 to {after} in 2025.",
 "queries": [{"key": "main", "endpoint": "/insights/movers", "tone": "exports",
              "params": {"by": "product", "period": "2025", "previous": "2024", "flow": "EXPORT", "limit": 8}}],
 "facts": {"before": {"query": "main", "path": "previous_total", "format": "eur"},
           "after": {"query": "main", "path": "total", "format": "eur"}},
 "options": {"limit": 8, "show_other": true}}
```

Kit: `waterfall_panel("export_change", "What changed", period="2025", previous="2024")`.

#### Choropleth: partners on a map

```json
{"id": "export_map", "type": "choropleth", "title": "Where exports go",
 "queries": [{"key": "main", "endpoint": "/insights/ranking", "tone": "exports",
              "params": {"by": "partner", "period": "$period", "flow": "EXPORT", "max_items": 60, "threshold": 0}}],
 "options": {"measure": "share", "extent": "europe"}}
```

For a balance map: `{"key": "main", "endpoint": "/insights/flows", "params": {"period": "$period",
"max_items": 60}}` with `"measure": "balance"`. Partners are ISO 2 codes; `OTHER` and aggregates
are not drawn. Kit: `choropleth_panel(id, title, measure="share", extent="world")`.

#### Flowmap: arcs from Luxembourg

```json
{"id": "routes", "type": "flowmap", "title": "The routes through Luxembourg",
 "queries": [{"key": "main", "endpoint": "/insights/flows",
              "params": {"period": "$period", "previous": "$previous", "max_items": 10}}],
 "options": {"flow": "both", "extent": "europe"}}
```

Import arcs (steel) come in from `in.items`, export arcs (sky) go out to `out.items`; the width is
the value. A `thread` mark (`in_code`, `out_code`) draws one route through Luxembourg in the agent's
pen. Kit: `flowmap_panel(id, title)`.

#### Heatmap: months by years, codes by years, seasons

```json
{"id": "export_months", "type": "heatmap", "title": "Exports month by month, since 1999",
 "queries": [{"key": "main", "endpoint": "/insights/series", "tone": "exports",
              "params": {"flow": "EXPORT", "freq": "M", "start": "1999-01", "end": "2026-06"}}],
 "options": {"kind": "month_year"}}
```

- `month_year`: one row per year, one column per month, from a monthly series.
- `product_year`: one row per code, one column per year, from `/insights/matrix`
  (`{"by": "product", "start": "1999", "end": "$period", "measure": "share", "max_items": 12}`).
- `seasonal`: one row per HS2 chapter, one column per month, the `average_effect_pct` of Ivan's
  seasonal profile (`{"flow": "EXPORT", "freq": "M", "top": 12}`).

Kit: `heatmap_panel(id, title, kind="month_year")`.

#### Treemap: the goods as nested tiles

```json
{"id": "goods", "type": "treemap", "title": "What Luxembourg exports",
 "queries": [{"key": "main", "endpoint": "/insights/tree", "tone": "exports",
              "params": {"by": "product", "period": "$period", "flow": "EXPORT", "depth": 2, "threshold": "auto"}}]}
```

Kit: `treemap_panel(id, title, depth=2)`.

#### Butterfly: imports left, exports right

```json
{"id": "two_ways", "type": "butterfly", "title": "Bought and sold, by chapter",
 "queries": [{"key": "in", "endpoint": "/insights/ranking", "tone": "imports",
              "params": {"by": "product", "period": "$period", "flow": "IMPORT", "max_items": 10}},
             {"key": "out", "endpoint": "/insights/ranking", "tone": "exports",
              "params": {"by": "product", "period": "$period", "flow": "EXPORT", "max_items": 10}}]}
```

Rows are the union of the codes of both sides. Kit: `butterfly_panel(id, title, by="product")`.

#### Bump: ranks over the years

```json
{"id": "partner_ranks", "type": "bump", "title": "Who buys most, year by year",
 "queries": [{"key": "main", "endpoint": "/insights/matrix", "tone": "exports",
              "params": {"by": "partner", "start": "2015", "end": "$period", "flow": "EXPORT", "measure": "rank", "max_items": 10}}],
 "options": {"labels": ["DE", "FR", "BE"]}}
```

Kit: `bump_panel(id, title, by="partner", start="2015")`.

#### Scatter: specialisation against growth

```json
{"id": "strengths", "type": "scatter", "title": "Strong and growing",
 "queries": [{"key": "main", "endpoint": "/insights/specialisation", "tone": "exports",
              "params": {"by": "product", "period": "$period", "flow": "EXPORT"}},
             {"key": "growth", "endpoint": "/insights/movers",
              "params": {"by": "product", "period": "$period", "previous": "$previous", "flow": "EXPORT"}}],
 "options": {"x": "rca", "y": "growth", "size": "value", "reference_x": 1, "reference_y": 0}}
```

One bubble per code in `main.items`; fields not in `main` come from the `growth` rows with the same
`code` (`up`, `down` and `new` of movers, or `items` of trends). Kit: `scatter_panel(id, title)`.

### Trend breaks

A `trend_break` is a lasting shift in a series' level: a change point. On a line it is a rule at
`at` with two level lines (from `from` to `at`, and from `at` to `to`; default six periods each
side). On a heatmap it outlines the cell at `at` and the row edge. The levels come from facts named
by `before` and `after`, usually read from the mark's own `/trends/analyze` query:

```json
{"op": "add_mark", "mark": {
  "kind": "trend_break", "target": "export_months", "thread": "breaks",
  "params": {"at": "2009-01", "label": "Financial crisis", "before": "before", "after": "after"},
  "query": {"endpoint": "/trends/analyze",
            "params": {"flow": "EXPORT", "freq": "M", "start": "1999-01", "end": "2026-06", "penalty": 1.5, "min_size": 12}},
  "note": "From January 2009 a typical month fell from {before} to {after}.",
  "facts": {"before": {"query": "self", "path": "changepoints.[period=2009-01].before_mean", "format": "eur"},
            "after": {"query": "self", "path": "changepoints.[period=2009-01].after_mean", "format": "eur"}}}}
```

Kit: `view.trend_break_mark("export_months", "2009-01", "... from {before} to {after}.",
label="Financial crisis", query=query("/trends/analyze", {...}))` builds those two facts for you.

### Using Ivan's trend analysis

`GET /trends/analyze` runs on the same filters as `/insights/series` (use `freq=M` for months,
`flow`, `product`, `partner`, ...), plus `start`, `end`, `stl_period` (the seasonal cycle; guessed:
12 for months, 4 for quarters), `min_size` (the shortest run that can be a regime; default 6) and
`penalty` (default 3.0; lower finds more breaks). It answers:

```json
{"method": "stl", "seasonal_period": 12,
 "direction": "increasing", "shape": "decelerating",
 "slope": 1465379.2, "relative_change": 0.16, "volatility": 0.07,
 "trend_strength": 0.57, "seasonal_strength": 0.37, "seasonality_label": "moderate",
 "seasonality_summary": "A moderate repeating yearly pattern: Mar is typically the highest point ...",
 "seasonal_pattern": [{"position": 1, "label": "Jan", "average_effect": -43887020.6, "average_effect_pct": -3.5, "tag": "lower than usual"}],
 "seasonal_peak": {"position": 3, "label": "Mar", ...}, "seasonal_trough": {...},
 "points": [{"period": "1999-01", "value": ..., "trend": ..., "resid": ..., "seasonal": ..., "adjusted": ...}],
 "changepoints": [{"period": "2009-01", "index": 120, "before_mean": 1455793245.8, "after_mean": 1274898750.5,
                   "change": -180894495.3, "relative_change": -0.12}],
 "n_points": 330, "start": "1999-01", "end": "2026-06", "fetched_live": false}
```

- `direction`, `shape`, `seasonality_label` and `seasonality_summary` are words, safe in a text as
  `format: "text"` facts. `relative_change`, `trend_strength`, `seasonal_strength` are fractions
  (`pct`). `slope` is euros per period.
- `changepoints` are the breaks: one `trend_break` mark each, `at` = `period`, levels from
  `changepoints.[period=...].before_mean` / `after_mean`. On monthly exports since 1999, the default
  penalty finds one break (2003-08); `penalty=1.5, min_size=12` finds three that read well: 2005-09
  (step up), 2009-01 (the financial crisis), 2021-12 (the post-pandemic step up)
  (`contract/examples/07_heatmap_and_breaks.json`).
- `points[].adjusted` is the seasonally adjusted series; `seasonal_pattern` is the typical year.
- Fewer than its minimum of points is a 400; no data is a 503. Both fail the pre-flight, so ask
  with the kit first: `api.analyze(flow="EXPORT", freq="M", start="1999-01", penalty=1.5)`.
- `GET /trends/seasonal-heatmap` (`freq=M`, `top`, `start`, `end`, plus `flow`, `partner`) gives
  the typical year of each of the top HS2 chapters; use it for a `seasonal` heatmap.

### Which agent uses what

| Agent | Typically draws |
|---|---|
| Trend finder | `heatmap` (`month_year`, `product_year`) with `trend_break` marks from `/trends/analyze`; `bump` of ranks; `highlight` on bars and maps |
| Cause finder | `waterfall` of a change (2025 against 2024) with `annotation` marks on the steps; `event` marks lined up with trend breaks |
| Forecaster | `forecast` marks on lines; a `heatmap` `seasonal` to explain the shape of the year |
| Visualizer | `choropleth`, `flowmap` (with `thread` marks), `treemap`, `butterfly`, `scatter`; `set_context` when asked for another year or product |
| The user | `set_panel_context` from each chart's Year (or From/To) and Goods menu (1.5); `set_context` with a reader; keep, remove and pin |

### What the backend and the page must do for 1.1

- Backend: serve `/insights/matrix` and `/insights/tree` (shapes above); add `total` and
  `previous_total` to `/insights/movers`; apply `set_context` in the store
  (`spec["context"] = {**context_of(spec), **op fields}`); pre-flight with
  `query_url(q, context_of(spec))`.
- Page: substitute placeholders from `spec.context` before fetching (same rules as
  `fill_params`); send `set_context` from the Year and Goods controls; dim marks whose `period`
  does not match `context.period`; render the eight types and `trend_break`.

## Contract 1.2: comparisons

Additive like 1.1 (`spec_version` stays `1.0`). It adds the panel type `compare`, the comparison
options of `line` and `bar` panels, and marks on comparisons. A comparison never carries data: each
side is a query, and the page computes the index, share, growth or difference from the API rows.

### Two layouts

| Layout | Queries | What the page draws |
|---|---|---|
| Series over time | 1 to 6 queries, all on `/insights/series`, one per thing compared (`partner=FR`, `product=85`, `reporter=DE`), all with the same `freq`. The `key` names the series, the `label` is its direct label | One line per series with its label at the line end, and a readout above: where each series ends and how it moved. `facet` gives small multiples |
| Two snapshots | Exactly two queries keyed `a` and `b`, on the same `/insights/ranking` or `/insights/compare`, with the same `by` (usually two periods) | One row per code: paired bars (a light, b full) with the change on every row, or one diverging bar of the growth or the change |

A `compare` panel picks the layout from its queries. A `line` with comparison options is read like
the series layout; a `bar` with comparison options must be two snapshots `a` and `b`.

### Options

| Option | Values | Meaning |
|---|---|---|
| `compare` | `absolute` (default), `index`, `share`, `yoy`, `diff` | see the modes below; two snapshots take every mode but `index` |
| `base_period` | a period | `index`: the period at 100 (default: the first period every series has) |
| `reference` | a query key | `share`: the denominator (not drawn); `diff`: the series subtracted from the others (drawn as the zero line, labelled). Required for `diff` |
| `focus` | a query key (series), a code (snapshots) | the series drawn in sky (default: the first query) / the row emphasised (`LU`) |
| `facet` | `true` | small multiples: one chart per series (2 to 6) on one scale, the other series faint behind |
| `align` | `time` (default), `month` | `month`: monthly series of different years on one January-to-December axis (every query `freq=M`) |
| `balance` | `true` | exports against imports: exactly two series, one `flow=EXPORT` and one `flow=IMPORT`, absolute mode; the gap is shaded (sky for a surplus, steel for a deficit) and the balance is read out |
| `sort` | `b` (default), `a`, `delta` | two snapshots: rows by b, by a, or by the size of the change |
| `limit` | 1 to 30 (8) | two snapshots: rows shown; the `focus` row is always kept |

**Modes:**

| Mode | Series over time | Two snapshots |
|---|---|---|
| `absolute` | the euros | a and b side by side, with "+€824 m, +36%" on each row |
| `index` | each series / its value at `base_period` x 100; the rule at 100 | not allowed (`bad_param`) |
| `share` | each series / `reference` (or / the sum of the series shown), in % | the `share` field of a and b, with the change in points |
| `yoy` | growth on the same period a year earlier (start a year earlier so the first growth exists; the kit does) | the growth from a to b per row |
| `diff` | each series minus `reference`, in euros | b minus a per row |

There is no per-capita mode: the data has no population source.

**Colours.** Data only: one export and one import series are sky and steel; otherwise the focus
series is sky (steel when it is an import series) and the others are neutral greys, each with its
own dash. Agents' pens are for marks only.

**Marks on comparisons.** Over time: `highlight` with `from`/`to`, `event`, `annotation` (`x` a
period, `series` the query key). Two snapshots: `highlight` with `codes`, `annotation` (`x` a code).
`trend_break` stays on lines and heatmaps.

**Facts.** A comparison's queries can be read like any panel's: `{"query": "de", "path": "-1.value"}`.
The kit adds, per series, `<key>_now` (the last value), `<key>_then` (the value at `base_period`, else
the first) and `<key>_change` (growth between them, with its sign), so a text can quote them:
`"Exports since 2015: Luxembourg {lu_change}, Germany {de_change}."` (write it so that a fall
reads as well as a rise). Two snapshots on
`/insights/ranking` get `a_total`, `b_total` and `change`.

**Errors** (`bad_queries`, `bad_param`, `unknown_query`, `required`): mixed endpoints, more than six
series, different `freq`s, `diff` without `reference`, a `reference` or `focus` that is not a query
key, `align: month` on yearly series, `balance` without one export and one import series, `a` and
`b` on different endpoints or `by`s, or the same query twice.

### Comparisons: which chart for which question

| Question | Chart | Kit call (returns ops for `send`) |
|---|---|---|
| "How does Luxembourg compare with Germany and Belgium?" | series over time, one per reporter, `index` from 2015 (a small and a large economy on one scale) | `view.compare_with_peers(["DE", "BE", "NL", "FR"], "exports", start="2015")` |
| "Which EU countries grew their exports most since 2015?" | two snapshots on `/insights/compare` (`by=reporter`, `group=LU,DE,...`), `yoy`, focus `LU` | `view.compare_with_peers([...], "exports", start="2015", end="2025", layout="bars")` |
| "Exports to the US: is this specific to Luxembourg? Compare with Belgium, the Netherlands and Germany" | the same lines, each country's OWN exports to that partner (`/insights/series?reporter=BE&partner=US`; the EU-27 × US, CN, GB, CH are loaded, any other partner is fetched from Eurostat on first use) | `view.compare_with_peers(["BE", "NL", "DE"], "exports", start="2015", partner="US")` |
| "Do we sell more to France or to Germany?" | series over time per partner, `absolute` (or `index` for "who grew faster") | `view.compare_partners(["FR", "DE"], flow="EXPORT", mode="absolute")` |
| "Compare our six biggest suppliers" | the same, `facet=True` (small multiples) | `view.compare_partners([...6 codes], flow="IMPORT", facet=True)` |
| "Machinery or electrical machinery: which matters more?" | series per product, `share` (of the two together) or `absolute` | `view.compare_products(["84", "85"], mode="share")` |
| "How far behind Germany are France and Belgium as buyers?" | series per partner, `diff` against the first | `view.compare_partners(["DE", "FR", "BE"], mode="diff")` |
| "How did 2025 compare with 2024, partner by partner?" | two snapshots on `/insights/ranking`, `absolute` (or `diff` with `sort="delta"` for "what changed most") | `view.compare_years("2024", "2025", "partner")` |
| "Was 2025 a better year than 2024, month by month?" | two monthly series, `align="month"` | `view.compare_years("2024", "2025", "month")` |
| "How fast are exports to France growing?" | one series, `yoy` | `view.compare_partners(["FR"], mode="yoy", start="2016")` |
| "Do we buy more than we sell? With China?" | exports against imports, `balance=True` | `view.compare_flows(start="2015", partner="CN")` |

Every helper takes `id=`, `title=`, `subtitle=`, `text=` (with `{fact}`s), and for a mark
`highlight=("2020", "2021")` (a span over time) or `highlight=["FR"]` (rows), or
`annotate=("2025", "Luxembourg", "lu")` / `annotate=("FR", "France")`, with `note=` (required) and
`period=`, `thread=`, `facts=`. Names come from `/insights/compare` (short HS names are built in);
`labels={"DE": "Germany"}` overrides them. `compare_years` defaults to `$previous` and `$period`, so
its rows follow the page's Year control; a literal year pins it.

**Quotable JSON for a prompt.** Luxembourg against its peers, rebased:

```json
{"op": "upsert_panel", "panel": {"id": "peers_exports", "type": "compare",
  "title": "Exports of Luxembourg, Germany and Belgium, since 2015", "subtitle": "Index, 2015 = 100",
  "queries": [
    {"key": "lu", "label": "Luxembourg", "endpoint": "/insights/series", "params": {"reporter": "LU", "flow": "EXPORT", "freq": "A", "start": "2015", "end": "$period"}},
    {"key": "de", "label": "Germany", "endpoint": "/insights/series", "params": {"reporter": "DE", "flow": "EXPORT", "freq": "A", "start": "2015", "end": "$period"}},
    {"key": "be", "label": "Belgium", "endpoint": "/insights/series", "params": {"reporter": "BE", "flow": "EXPORT", "freq": "A", "start": "2015", "end": "$period"}}],
  "options": {"compare": "index", "base_period": "2015", "focus": "lu"}}}
```

Two years side by side, with the change per partner:

```json
{"op": "upsert_panel", "panel": {"id": "partners_2025", "type": "compare", "title": "Exports by partner, 2024 and 2025",
  "queries": [
    {"key": "a", "label": "2024", "endpoint": "/insights/ranking", "params": {"by": "partner", "period": "2024", "flow": "EXPORT", "max_items": 24, "threshold": 0}},
    {"key": "b", "label": "2025", "endpoint": "/insights/ranking", "params": {"by": "partner", "period": "2025", "flow": "EXPORT", "max_items": 24, "threshold": 0}}],
  "options": {"compare": "absolute", "sort": "b", "limit": 8}}}
```

Exports against imports with the balance:

```json
{"op": "upsert_panel", "panel": {"id": "balance_since_2015", "type": "compare", "title": "Exports and imports, since 2015",
  "queries": [
    {"key": "exports", "label": "Exports", "tone": "exports", "endpoint": "/insights/series", "params": {"flow": "EXPORT", "freq": "A", "start": "2015", "end": "$period"}},
    {"key": "imports", "label": "Imports", "tone": "imports", "endpoint": "/insights/series", "params": {"flow": "IMPORT", "freq": "A", "start": "2015", "end": "$period"}}],
  "options": {"compare": "absolute", "balance": true, "focus": "exports"}}}
```

The same as options on existing types (`contract/examples/09b_line_bar_comparisons.json`): a line with
`"options": {"compare": "index", "base_period": "2015"}`, and a bar with queries `a` and `b` and
`"options": {"compare": "yoy", "sort": "delta", "focus": "85"}`.

**What the data allows.** `reporter` is any EU27 country, yearly 1999 to 2025, in total and by HS2
chapter (Luxembourg only reports alone from 1999). `partner` and `product` series are Luxembourg's,
yearly from 1999; monthly by HS2 chapter from 1999-01. Other slices (a partner month by month, a
partner by product outside the eight loaded partners) are fetched live, so the first view can take a
few seconds. A reporter with no data, or a
code the API does not know, fails the pre-flight: ask with the kit first
(`api.series(reporter="DE", flow="EXPORT", freq="A", start="2015")`).

**Which agent uses what.** The visualizer draws most comparisons ("compare with Belgium", "2025
against 2024"). The trend finder uses `index` and `yoy` to show who moved, the cause finder a `diff`
or two snapshots with `sort="delta"` to show what changed most, and the forecaster `align="month"`
to put the current year against the last.

### What the page must do for 1.2

`app/frontend/src/view/ComparePanel.tsx` draws every comparison (`compare.ts` holds the
transforms). The panel dispatch sends a `compare` panel, and a `line` or `bar` whose options make it
a comparison (`isComparison(panel)` in `compare.ts`), to `ComparePanel`, and the mark tables list
`annotation`, `highlight` and `event` on `compare` (the backend's `MARK_ON`).

## Contract 1.4: personas

Additive like 1.1 to 1.3: `spec_version` stays `1.0`, every earlier spec and batch is still valid, and
a spec without a persona behaves exactly as before.

**What it adds.** Two more fields of the page context: `start`, the From year of a range (see "Change the first diagram's year range" below), and `persona`, the id of a persona profile
(`^[a-z][a-z_]{1,31}$`; the six profiles are in `contract/personas/persona_profiles.json`):

```json
{"op": "set_context", "persona": "journalist"}
```

- It merges like the other context fields: a `set_context` without `persona` keeps it, undo and redo
  restore it, and `GET /view/history` gives each version's `persona`, so the audit trail says which
  reader every version was drawn for (never who the person is: the reader picks a persona, nothing
  is inferred or stored about them).
- The page's "Reading as" control sends it as agent `user`, in one batch: `set_context` with the
  persona and the profile's default context (a single year), `remove_panel` of the previous reader's
  panels, and the new reader's panels (`contract/personas/persona_variants.json`). The baseline panels
  the reader does not need fold under "More for this view" (never removed, never while they carry
  marks), and titles may read differently (`panel_titles`, display only). `everyone`, a persona with
  no profile, is the page for every reader. An agent may send `persona` too, but only when asked
  ("explain it as for a journalist").
- **A persona never changes a figure.** It changes which panels are open, their titles, the wording
  and depth of the replies and the suggested questions. Every number still comes from `/insights`
  through `{fact}` placeholders, and two readers asking the same question see the same figures.
- The persona is part of the shared spec, so every browser on one backend reads as the same persona
  (fine for one reader at a time; a multi-user deployment wants a per-session context).

**For agents.** Read `spec.context.persona`, load its profile and adapt: `answer.max_sentences`,
`answer.depth`, `answer.show_codes`, `vocabulary` (terms to replace in your words, never inside a
`{fact}`), `charts.prefer` / `charts.avoid` / `charts.max_new_panels`, and `agents.max_per_question`
(how many specialists answer one question). `prompt_snippet` is a ready-made "Who is asking"
paragraph.

### Personas

`contract/personas/` holds the readers (one source of truth; the page reads a copy in
`app/frontend/src/view/personas/`, and `tests/test_personas.py` fails until the copies match):

| File | What |
|---|---|
| `persona_profile.schema.json` | the profile schema (`besser-persona-profile-1.0`) |
| `persona_profiles.json` | the six profiles: `statec_analyst`, `policy_adviser`, `journalist`, `exporter`, `researcher`, `citizen` |
| `persona_variants.json` | per persona, the panels the page adds for that reader (`ops`) and removes when the reader leaves (`remove_on_leave`) |

A profile says, for one reader: `default_view` (the context it opens on, the baseline panels shown
and those folded under "More", display titles), `agents` (which answer, which leads,
`max_per_question`, whether the agent cards show), `answer` (depth, `max_sentences`, codes, sources,
method, how a forecast's uncertainty is told, a quotable sentence), `charts` (preferred and avoided
panel types, `max_new_panels`), `vocabulary`, `suggested_questions`, `presentation` (BESSER's
agent-personalisation vocabulary: style, complexity, sentence length, text size and contrast) and
`prompt_snippet`, the "Who is asking" paragraph of the agent's prompts (LuxInsights).

The same readers are BESSER user models in `model/personas_user_model.py`: B-UML object models of
BESSER's User Modeling Language (the reference model the BESSER editor ships for user diagrams),
validated with their OCL invariants, rendered with BESSER's JSON generator into its profile
document, and run through BESSER's rule-based recommender. An adapter derives what a user model can
decide (statistical literacy, codes, depth, text size) and takes the rest from the profiles above.
The generic rules key on age, languages and disabilities: for the citizen (67) the readability rule
gives text 20 and high contrast; nothing in them knows statistical literacy, so the adapter decides it.
These are proto-personas built on the ONS persona research and the Eurostat user survey, to be
validated with STATEC users.

## Contract 1.3: forecast scenarios, and what the backend does for you

Additive like 1.1 and 1.2: `spec_version` stays `1.0`, every earlier spec and batch is still valid.

**Forecast scenarios.** A `forecast` mark's `params` may carry `scenarios: true`. The page then draws
the scenarios of the mark's own `/insights/forecast` answer (today "the smartphone line stops /
continues", for a flow's total or chapter 85 against the world) as two labelled points at the end of
the band, in the agent's ink. Cite them in the note with facts on the mark's own query:

```json
{"op": "add_mark", "mark": {"kind": "forecast", "target": "trend", "series": "exports",
  "query": {"endpoint": "/insights/forecast", "params": {"flow": "EXPORT", "freq": "M", "horizon": 12, "interval": 0.8, "aggregate": "A"}},
  "params": {"label": "2026 forecast", "scenarios": true},
  "note": "Exports in 2026: {value}, between {low} and {high}. If the smartphone line stops: {stops}; if it continues: {continues}.",
  "facts": {"value": {"query": "self", "path": "rows.[period=2026].value", "format": "eur"},
            "low": {"query": "self", "path": "rows.[period=2026].low", "format": "eur"},
            "high": {"query": "self", "path": "rows.[period=2026].high", "format": "eur"},
            "stops": {"query": "self", "path": "scenarios.[id=smartphones_stops].rows.[period=2026].value", "format": "eur"},
            "continues": {"query": "self", "path": "scenarios.[id=smartphones_continues].rows.[period=2026].value", "format": "eur"}}}}
```

Kit: `view.forecast_mark(target, query, note, label=..., scenarios=True)`. Leave `method` out of the
forecast query: the backend then uses the backtest's winner (see [forecasting.md](forecasting.md)),
and `backtest.summary`, `backtest.skill_h3`, `backtest.coverage` and `backtest.methods` say how good
it is. Do not give a forecast mark a `period`: a forecast is about the years after the data, and the
page never dims it.

**What the backend checks and fills in**

- `set_context` is checked cheaply: the period must exist in the data (a year of the annual data, a
  month of the monthly data) and the product code must be known, else a 422 with code `preflight` at
  `/ops/i/period` or `/ops/i/product`. It does not re-pre-flight the panels already on the page; a
  panel with no data for the new year shows its own empty state.
- The pre-flight fills `$period`, `$previous` and `$product` with the context the page will have after
  the batch, including a `set_context` earlier in the same batch.
- Every spec carries `context` (default: the latest complete year and TOTAL), on GET /view and SSE.
- Without `freq`, a month as `period`, `previous`, `start`, `end` or `base` (2025-03) reads the monthly
  data, `YYYY-Qn` quarterly and `YYYY-Sn` half-yearly.
- `/insights/movers`: `up` holds only increases and `down` only decreases; `total` and `previous_total`
  are the totals of the whole selection (the waterfall's first and last bars).
- `/insights/tree` also returns `root` and `requested_depth` (levels the database does not hold for
  that year are left out: 2010 gives chapters only), and its `OTHER` child has `count`.
- `/insights/matrix` also returns `freq`, `totals` and each row's `total`.
- `/insights/flows` keeps its 1% threshold: a balance choropleth wants `threshold: 0`.

**Placeholders the agent's tools offer.** A highlight of codes can cite `{de_value}`, `{de_share}`
on a one-sided panel (choropleth, bar, treemap), and `{de_bought}`, `{de_bought_share}`, `{de_sold}`,
`{de_sold_share}` on a two-sided one (the crossroads, a butterfly, a flow map); product codes take `hs`
in front (`{hs84_value}`); scatter bubbles add `{hs40_rca}`, `{hs40_eu_share}`, `{hs40_growth}`, bump
rows `{de_then}`, `{de_now}`. A code highlight on a panel that follows the page carries the page's year.
On a line or compare chart, `{exports_change}` is the growth over the whole span shown and
`{exports_yoy}` the last year on the one before (exports against imports add `{balance}` and, at a year,
`{balance_at}`); a mark at a year reads that year alone
(`start = end = year`), so it stays right when the page moves to another year. Broad events take
`{exp_change}`/`{imp_change}` (all exports and imports that year); an event whose `scope` in `/insights/events`
names partners or products (US steel tariffs: US, HS 72/73/76) takes only its own line, computed from that
scope: `{line_change}`/`{line_diff}` (and `{line_exp_change}`/`{line_imp_change}` when it concerns both flows),
and `{exp_change}` is refused on it. Highlights of codes and annotations carry the `period` they are about
(the panel's year, or the point on a time axis); events, trend breaks and forecast bands do not. Long-run
views start in 1999 (the first year of `/insights/meta`) unless the question names another start. Forecast notes take `{skill_1m}`, `{skill_3m}`, `{skill_6m}`,
`{skill_12m}`. The tools refuse a note whose verb contradicts its figure's sign ("fell by +8.6%"),
causal wording on events, events that are not in `/insights/events`, and a skill placeholder whose
horizon disagrees with the words next to it. A thread's note is written by the tool, never the model: the
supplier's share of the line's imports, the buyer's share of the line's exports, the line's share of
everything bought from the supplier. `{partner_share}` written as a share of a whole flow ("of exports") and
`{line_share}` not said as a share of the change are refused; the "In short" headline holds at most 160
characters as shown (two lines), the title 120. `clear_page` runs only when the user's message asks to clear,
reset or start over; one-year charts (maps, treemaps, bars, tables, butterflies) use `$period` unless the
question names that year.

**Follow-up chips.** A reply's last line `_Next: ...?_`, or a `follow_up` field in the reply payload,
becomes a chip under the reply; clicking it asks that question.



### Change the first diagram's year range

`/insights/ranking`, `/insights/ranking/records` and `/insights/ladder` accept
`start=2020&end=2025&period=2025`. The range is inclusive and values are summed before ranking
partners and calculating shares; the response says which years it summed in `start`, `end` and
`span` (`"2025"` or `"2020–2025"`, a text fact: the headline reads "In {year} ..." with
`path: "span"`). Without start/end, or with `start = end = period` (a single year, or a month), it
is the single-period query as before.

The range is part of the page context (contract 1.4): `period` is the To year and `start` the From
year. (Since 1.5 the page has no From and To controls of its own: the From/To menu of the three
figures and of the crossroads sends `set_panel_context {period, start}` for that chart, with the
upsert that makes it follow `$start` the first time.) An agent's `set_context {period, start}` moves the
page: every panel without its own context, "about 2025" tag and dimming follows one page period, and an
agent's `set_context {period: "2020"}` moves the range with it (`$start` never goes past the period). The
page's own range panels (the headline, the three figures, the crossroads) follow it with
`period: "$period", start: "$start", end: "$period"` in their ranking and ladder queries.

Use `agents.kit.view.flow_range_ops(spec, "2020", "2025")` and send the returned ops with
`ViewClient.send`: one `set_context`, plus, the first time, the upserts that make those panels follow
`$start`. Example chatbot request: "Show the first diagram from 2020 to 2025." A single year is
`flow_range_ops(spec, "2025", "2025")`.

A page state saved before this (panels holding literal years, or baseline panels from an older
`baseline.json`) is repaired when the backend loads it: those page panels go back to today's
baseline, as one new version (`action: "repair"` in `/view/history`) that undo can reverse.


## Contract 1.5: a chart's own year and goods

Additive like 1.1 to 1.4: `spec_version` stays `1.0`, the schemas' `$id`s do not change, and every
earlier spec and batch is still valid.

**What it adds.** A panel may carry its own `context`, `{period?, start?, product?}`, which wins over
the page context for that panel only: its queries, its inline facts, the marks drawn on it, its "How
this was made" card and the backend's pre-flight fill `$period`, `$previous`, `$start` and `$product`
from `{...spec.context, ...panel.context}` (`view.validate.panel_context`). A panel `period` without
its own `start` is a single year (the page's From does not apply to it).

```json
{"op": "set_panel_context", "id": "mix_export", "period": "2020"}
{"op": "set_panel_context", "id": "peers", "period": "2019", "product": "85"}
{"op": "set_panel_context", "id": "crossroads", "start": "2015", "period": "2025"}
{"op": "set_panel_context", "id": "peers"}
```

- The fields given are the panel's whole own context; a field left out (or `null`) follows the page
  again, and `{id}` alone makes the whole panel follow the page.
- Only what the panel follows can be set (`no_placeholder` otherwise): `period` when its queries or
  facts use `$period` or `$previous`, `start` when they use `$start`, `product` when they use
  `$product`. `start` needs `period`, a year no earlier than it (`bad_period`). Periods and product
  codes must exist in the data, as for `set_context` (`preflight`).
- Who: the user (each chart's menu) and any agent; an agent cannot change a pinned panel's context.
  It is a version like any op: undo, redo and reset bring the earlier context back (reset: none).
- `set_context` still moves the page: every panel without its own context follows it, and a chart
  with its own year keeps it. The agents' page summary flags such a chart (`OWN CONTEXT period 2020`):
  read its figures with its own context, not the page's. `upsert_panel` replaces a panel whole, so a
  panel you rewrite has no own context unless you give it one.
- The page dims a mark about another year by comparing it with its chart's own period, and tags it
  "about 2025" as before.

**The person's menu.** Each chart that follows the page shows one quiet line in its header ("2025,
all goods", "2015 to 2025, machinery"); clicking it opens Year (or From/To for the range charts) and
Goods for that chart only, and "Follow the page" when it has its own. The three figures share one
menu. The text panels (the "In short" headline) follow the page context.


## Contract 1.6: a storyline keeps its number

Additive like 1.1 to 1.5: `spec_version` stays `1.0`, the schemas' `$id`s do not change, and every
earlier spec and batch is still valid.

Before 1.6 the page numbered a storyline by its place among the storylines on the page, so when a
chart was replaced (the agents' three slots) or a storyline removed, the later ones moved down and an
earlier reply's "[3]" pointed to another mark. Now the backend gives the number:

- A mark carries `story_n`, its storyline's page number; the spec carries `next_story_n`, the number the
  next new storyline gets. The backend sets both (an `add_mark` that sends `story_n` is refused) and
  checks them: one number per storyline, never two storylines on one number, all below `next_story_n`.
- A new storyline (a mark with a new `thread`, or a mark without one) takes `next_story_n`; a mark that
  joins a thread on the page takes that storyline's number. The number stays while the storyline is on
  the page. A storyline whose marks are all removed (by the user, `clear`, or with a replaced chart)
  loses its number for good: it is never given to a later storyline.
- Undo and redo bring a version back with its numbers, and the counter keeps its highest value. Only
  `POST /view/reset` ("Back to the starting page") and `clear` with scope `page` start the numbers
  again (after the kept storylines). A state saved before 1.6 is numbered on load, in the order the
  page showed it.
- The page, the chat's "[n]", "keep 1" / "remove 2", `agents.kit.view.story_numbers(spec)` and
  `lux_insights.page` (`number_of`, `numbers`, `page_numbers`, `marks_of_number`) all read `story_n`, so
  numbers have gaps: `page.numbers(spec)` lists the ones on the page.


## Contract 1.7: growth per year since a year

Additive like 1.1 to 1.6: `spec_version` stays `1.0`, the schemas' `$id`s do not change, and every
earlier spec and batch is still valid.

**Why.** "Which partners grew fastest since 1999?" and "Are there trends?" ask for growth over a span.
A bar of one year ranks by value, and a waterfall is the change between two years in euros: neither
draws long-run growth, though `/insights/trends` computes it. A **growth bar** draws it.

**What it adds.** A `bar` with `options.measure: "growth"`. It reads exactly one query keyed `main` on
`/insights/trends` (by `partner` or `product`, with `start`; `end` defaults to the last year, `$period`
follows the page). The page draws one row per item, as ranked, the fastest-growing first: its name, its
growth per year ("+7.2% a year"), a bar from zero (to the left when it shrank), and under it its value in
the first and the last year ("€39 m in 1999 → €235 m in 2025"). A note under the bars gives the span, the
significance cutoff (at least 1% of the flow at the start or the end for partners and chapters, a decimal
less per product level) and that items with nothing at the start have no rate and are left out.

```json
{"op": "upsert_panel", "panel": {"id": "growth_partners", "type": "bar",
  "title": "Fastest-growing export partners since 1999", "subtitle": "Average growth per year since 1999 (compound)",
  "queries": [{"key": "main", "endpoint": "/insights/trends", "tone": "exports",
               "params": {"by": "partner", "flow": "EXPORT", "start": "1999", "end": "$period", "limit": 8}}],
  "options": {"measure": "growth", "x": "name", "y": "growth", "format": "pct_signed", "limit": 8}}}
```

- **The validator** (`_check_growth_bar`): one query `main` on `/insights/trends` (`bad_queries`), `by`
  partner or product (`bad_param`), `start` given (`missing_param`) and before a literal `end`
  (`bad_period`), `method` cagr or absent (`bad_param`), and none of the comparison options `compare`,
  `sort`, `focus` (`bad_param`). The schema allows `measure: "growth"` and no other bar measure.
- **Marks.** `highlight` (codes) and `annotation` (`x` = a code), as on any bar. LuxInsights' highlight
  offers `{<code>_growth}` (`items.[code=CN].growth`, `pct_signed`), `{<code>_then}` (`start_value`) and
  `{<code>_now}` (`value`), e.g. "China grew {cn_growth} a year, from {cn_then} to {cn_now}." A highlight
  on a growth bar carries no `period`: a span is about no single year.
- **Kit:** `growth_bar_panel(id, title, by="partner", start="1999", end="$period", flow="EXPORT",
  limit=8, level=None, parent=None, **filters_and_fields)`.
- **How this was made.** The method card names `/insights/trends` as "growth per year between <start> and
  <end> (compound annual growth), for the partner countries (or products) above the significance cutoff".
- **LuxInsights.** `show_panel(type="bar", measure="growth", by, start)` draws it, and so does a bar or
  table titled as growth ("Fastest-growing export partners since 1999") over a span: a `start`, a start in
  the question ("since 1999") or a span in the title. A bar of one year titled as growth with no span is
  still refused, and the refusal names both the growth bar and the waterfall. `get_trends` says how to
  draw what it found (`draw_it`). The trend finder answers "Are there trends?" with the lasting shifts
  (`analyze_trend`, `add_trend_breaks`) and the growth ranking; the visualizer answers "which partners or
  products grew fastest since <year>" with the growth ranking.

## Contract 1.8: quantities (weight, units) next to values

Additive like 1.1 to 1.7: `spec_version` stays `1.0`, the schemas' `$id`s do not change, and every
earlier spec and batch is still valid. No default changes: a query without `measure` reads euros as before.

**Why.** "How many cars did we export?" asks for a number of cars, not euros. Eurostat ds-045409 has three
indicators: the value in euros, the weight (`QUANTITY_IN_100KG`) and the supplementary quantity (a CN8 line's
own unit: number of items, litres, pairs...; the unit's name comes from Comext's CN8-SUPPL_UNIT file, see
DATA_CARD.md).

**What it adds.**

- A query parameter `measure` on `/insights/series`, `/insights/ranking`, `/insights/ranking/records`
  (value, weight, units or all), `/insights/movers` and `/insights/compare` (value, weight or units):
  - `weight`: the values are tonnes (100 kg × 0.1); `units`: the count in the goods' own unit. A heading (HS4 or
    HS6) sums its CN8 lines when the lines in one unit hold at least 99.5% of its value; total goods and HS2
    chapters have no units (422 that says why: "counted in many different units"; a line with no unit: "counted
    by weight (tonnes) only"). Each response says its unit (`unit`: "t", "items", "litres"...), and each row
    carries `value_eur`, its value in euros, for a label or a tooltip that shows both.
  - `all`: every measure per row: `value` (euros), `weight` (t), `units` and `unit`, `eur_per_tonne` and
    `eur_per_unit` (computed by the backend: value ÷ quantity, an average over models and grades, not a price).
    A missing quantity is `null` with `quantity_note: "no quantity published for this line"`, never 0.
  - Quantities are fetched from Eurostat on first use like any slice (Luxembourg × world × every product code,
    2015–2025, is loaded).
- **Formats** (facts and `options.format`): `qty` (a count: 37,654), `qty:<unit>` (the count and its unit:
  `qty:cars` → 37,654 cars) and `tonnes` (122,464 t). A fact read from a query with `measure` weight or units is
  written `qty` by the kit (`fact_api`), and the text names what is counted ("{value} cars").
- **Charts.** A line or a bar with `measure` units or weight in its queries and `options.format` `qty:<unit>` or
  `tonnes`: the figures and the axis carry the unit, a bar shows each row's value in euros beside its count, the
  line's tooltip the value in euros too. A table of `/insights/ranking/records` with `measure: "all"` and
  `options.columns` `["name", "value", "units", "eur_per_unit", "weight"]` shows value, quantity and unit value
  (`options.unit`, e.g. "cars", names what a number of items counts); a missing quantity reads "not published".
- **How this was made** names the measure ("Weight in tonnes", "Count in the line's own unit", "Value,
  quantities and unit values") and says that unit values are averages, not prices.

```json
{"op": "upsert_panel", "panel": {"id": "cars_units", "type": "line", "title": "Cars exported, number of vehicles",
  "queries": [{"key": "exports", "endpoint": "/insights/series", "tone": "exports",
               "params": {"flow": "EXPORT", "product": "8703", "start": "2015", "end": "$period", "measure": "units"}}],
  "options": {"x": "period", "y": "value", "format": "qty:cars"}}}
```

- **LuxInsights.** `get_quantities(product, flow, period, partner)` gives, per year, the value, the weight, the
  count and the unit values, and which measure answers "how many" (`use`: units when the line has a
  supplementary unit, else weight); `show_panel(..., quantity="units" | "weight", unit_word="cars")` draws a
  line or a bar of it, `quantity="all"` a table. "How many cars did Luxembourg export in 2025?" is answered "In
  2025, Luxembourg exported 37,654 cars, worth €827 m, about €21,960 per car on average"; when no quantity is
  published the reply says that the data has the value in euros only for those goods.
