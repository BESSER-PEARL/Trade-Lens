# SPDX-License-Identifier: MIT
"""Build and send view ops (contract/ops.schema.json) and register an agent.

    view = ViewClient("http://localhost:8000", "trend_finder")
    view.register("Trend finder", "#E2142D", capabilities=[...])
    ops = [view.set_title("..."), view.thread_mark("crossroads", "CN", "FR", "Smartphones, China to France",
                                                   "{sold} sold to France", facts={"sold": fact_api(...)})]
    result = view.send(ops, message="what I did")      # {version, assigned, warnings, spec}

Builders only return dicts; ``send`` validates the batch against the schema locally, then POSTs it.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

from .insights import DEFAULT_BASE_URL

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "contract"

# The four agents: pen colour (marks, squares) and ink (their text on the page).
AGENTS = {
    "trend_finder": {"title": "Trend finder", "color": "#E2142D", "ink": "#B5101F",
                     "can": "Finds what changed and drills down to the product line"},
    "cause_finder": {"title": "Cause finder", "color": "#6D3FC0", "ink": "#5A2FA8",
                     "can": "Lines changes up with events and trade agreements"},
    "forecaster": {"title": "Forecaster", "color": "#C17F12", "ink": "#8A5A12",
                   "can": "Extends a series with an uncertainty band"},
    "visualizer": {"title": "Visualizer", "color": "#0F7B6C", "ink": "#0B6156",
                   "can": "Puts any partner, product or year on the page"},
}


class ViewError(RuntimeError):
    """The backend refused a batch. 422: ``errors`` lists {path, code, message}; 409: ``spec`` is current."""

    def __init__(self, status: int, body: Any):
        self.status, self.body = status, body
        body = body if isinstance(body, dict) else {}
        self.errors: list = body.get("errors") or []
        self.spec: Optional[dict] = body.get("spec")
        lines = [f"{e.get('path')}: {e.get('message')}" for e in self.errors]
        super().__init__(f"{status}: " + ("; ".join(lines) or str(body.get("detail", body))))


# --------------------------------------------------------------------------- pieces
def query(endpoint: str, params: Optional[dict] = None, *, key: Optional[str] = None, label: Optional[str] = None,
          tone: Optional[str] = None) -> dict:
    """A query: ``query("/insights/series", {"flow": "EXPORT", "freq": "A"}, key="exports", tone="exports")``."""
    q: dict = {"endpoint": endpoint, "params": dict(params or {})}
    if key:
        q["key"] = key
    if label:
        q["label"] = label
    if tone:
        q["tone"] = tone
    return {k: q[k] for k in ("key", "endpoint", "params", "label", "tone") if k in q}


def fact_from(query_key: str, path: str, format: Optional[str] = None) -> dict:
    """A fact read from one of the panel's queries (in a mark: ``"self"``, the mark's own query)."""
    return _drop_none({"query": query_key, "path": path, "format": format})


def fact_api(endpoint: str, params: dict, path: str, format: Optional[str] = None) -> dict:
    """A fact read from its own call: ``fact_api("/insights/ranking", {...}, "items.[code=FR].value", "eur")``.

    A query that reads a quantity (contract 1.8, ``measure`` weight or units) gives its values in tonnes or in the
    goods' own unit: a euro format becomes "qty", the number alone (the text names what is counted: "{be_value}
    cars", "{steel_now} tonnes")."""
    quantity = {"weight": "qty", "units": "qty"}.get(str((params or {}).get("measure") or ""))
    if quantity and format in ("eur", "eur_signed") and path.endswith(".value"):
        format = quantity
    return _drop_none({"endpoint": endpoint, "params": dict(params), "path": path, "format": format})


def fact_op(op: str, a: str, b: Optional[str] = None, format: Optional[str] = None) -> dict:
    """A fact computed from others: diff, sum, ratio, growth (a/b-1) or abs."""
    return _drop_none({"op": op, "of": [a] if b is None else [a, b], "format": format})


FLOW_RANGE_PANELS = {"headline", "kpi_imports", "kpi_balance", "kpi_exports", "crossroads"}


def flow_range_ops(spec: dict, start: str, end: str) -> list[dict]:
    """The From/To range of the first diagram (contract 1.4): totals summed from ``start`` to ``end``.

    One ``set_context`` moves the page: ``period`` is the To year and ``start`` the From year, so every
    panel, tag and dimming follows one page period, and an agent's later ``set_context(period=...)``
    moves the range with it (a single year is start == end). The page's own range panels (the
    headline, the three figures and the crossroads, while they are the page's and not an agent's) are
    upserted once to follow the placeholders ``$start`` and ``$period``; after that a range change is
    the set_context alone. Their headline year becomes the span (2020–2025) the API reports.
    """
    import copy
    import re
    if not (re.fullmatch(r"[0-9]{4}", start) and re.fullmatch(r"[0-9]{4}", end) and "0001" <= start <= end):
        raise ValueError("Use years YYYY with start <= end")
    ops: list[dict] = [{"op": "set_context", "period": end, "start": start}]
    for original in spec["panels"]:
        if original["id"] not in FLOW_RANGE_PANELS or original.get("agent") not in (None, "baseline", "user"):
            continue
        panel = range_panel(original)
        if panel != original:
            # The store rebuilds provenance from the updated queries.
            panel["provenance"] = {"sources": []}
            ops.append({"op": "upsert_panel", "panel": panel})
    return ops


def range_panel(original: dict) -> dict:
    """A copy of a page panel whose ranking and ladder queries follow the page range: period and end
    $period, start $start (the backend reads start = end = period as the single period)."""
    import copy
    panel = copy.deepcopy(original)
    ranged = {"period": PERIOD, "start": START, "end": PERIOD}
    for q in panel.get("queries") or []:
        if q.get("endpoint") in {"/insights/ranking", "/insights/ladder", "/insights/ranking/records"}:
            params = {k: v for k, v in (q.get("params") or {}).items() if k != "freq"}
            q["params"] = {**params, **ranged}
    for fact in (panel.get("facts") or {}).values():
        if fact.get("endpoint") in {"/insights/ranking", "/insights/ladder"}:
            params = {k: v for k, v in (fact.get("params") or {}).items() if k != "freq"}
            fact["params"] = {**params, **ranged}
        elif fact.get("query") and fact.get("path") == "period" and fact.get("format") == "period":
            fact.update({"path": "span", "format": "text"})  # "In {year}" reads "In 2020–2025"
    return panel


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _sources(sources: Optional[Iterable]) -> Optional[dict]:
    """Provenance from source URLs (as returned by InsightsClient) or {url, label} dicts."""
    if not sources:
        return None
    out = [s if isinstance(s, dict) else {"url": s} for s in sources]
    return {"sources": out}


# --------------------------------------------------------------------------- page context (contract 1.1)
# Placeholders a query param may hold; the page fills them from spec.context (Year and Goods controls).
PERIOD, PREVIOUS, PRODUCT = "$period", "$previous", "$product"
START = "$start"  # 1.4: the first year of a range (context.start), else the period
DEFAULT_CONTEXT = {"period": "2025", "product": "TOTAL"}


def range_start(context: Optional[dict]) -> str:
    """What $start stands for: context.start when it is a year no later than a yearly period, else the
    period (as app/backend/view/validate.range_start)."""
    import re
    ctx = {**DEFAULT_CONTEXT, **(context or {})}
    period, start = str(ctx["period"]), ctx.get("start")
    if isinstance(start, str) and re.fullmatch(r"[0-9]{4}", start) and re.fullmatch(r"[0-9]{4}", period) and start <= period:
        return start
    return period


def previous_period(period: str) -> str:
    """The same period a year earlier: 2025 -> 2024, 2025-03 -> 2024-03."""
    return f"{int(period[:4]) - 1:04d}{period[4:]}"


def fill_placeholders(params: Optional[dict], context: Optional[dict] = None) -> dict:
    """Params with $period, $previous and $product filled from a page context, as the page does:
    use it to read the numbers a panel will show (``api.get(endpoint, **fill_placeholders(p, spec["context"]))``)."""
    ctx = {**DEFAULT_CONTEXT, **(context or {})}
    values = {PERIOD: ctx["period"], PREVIOUS: previous_period(ctx["period"]), START: range_start(ctx),
              PRODUCT: ctx["product"]}
    return {k: values.get(v, v) if isinstance(v, str) else v for k, v in (params or {}).items()}


# --------------------------------------------------------------------------- page numbers (contract 1.6)
def story_numbers(spec: Optional[dict]) -> dict[str, int]:
    """The page number of each mark id: the [n] a reply cites and the circle on the chart.

    Marks that share ``thread`` are one storyline with one number. The backend gives it (``story_n``) when
    the storyline's first mark is added, and the storyline keeps it: replacing a chart or removing another
    storyline never renumbers it. A spec from before 1.6 has none: then the place in order of first n."""
    marks = sorted((spec or {}).get("marks") or [], key=lambda m: m.get("n", 0))
    order: list[str] = []
    for m in marks:
        key = m.get("thread") or f"#{m['id']}"
        if key not in order:
            order.append(key)
    return {m["id"]: m["story_n"] if isinstance(m.get("story_n"), int)
            else order.index(m.get("thread") or f"#{m['id']}") + 1 for m in marks}


# --------------------------------------------------------------------------- panels
def panel(id: str, type: str, title: str, queries: list, *, subtitle: Optional[str] = None,
          text: Optional[str] = None, facts: Optional[dict] = None, value: Optional[str] = None,
          options: Optional[dict] = None, sources: Optional[Iterable] = None) -> dict:
    """A panel of any type: ``panel("drill", "bar", "Inside HS 85", [query(..., key="main")])``."""
    return _drop_none({"id": id, "type": type, "title": title, "subtitle": subtitle, "text": text, "facts": facts,
                       "value": value, "queries": list(queries), "options": _drop_none(dict(options or {})) or None,
                       "provenance": _sources(sources)})


def _tone(flow: Optional[str]) -> Optional[str]:
    return {"EXPORT": "exports", "IMPORT": "imports"}.get(flow or "")


def waterfall_panel(id: str, title: str, *, by: str = "product", period: str = PERIOD, previous: str = PREVIOUS,
                    flow: str = "EXPORT", level: Optional[int] = None, parent: Optional[str] = None,
                    limit: int = 8, show_other: bool = True, options: Optional[dict] = None, **kw) -> dict:
    """What added up to a change: previous total, the biggest movers (up and down), the rest, the new total.
    One query 'main' on /insights/movers (it answers total and previous_total). ``kw``: dimension filters
    (partner="FR") and panel fields (subtitle, text, facts, sources)."""
    fields, filters = _split(kw)
    params = _drop_none({"by": by, "period": period, "previous": previous, "flow": flow, "level": level,
                         "parent": parent, "limit": limit, **filters})
    return panel(id, "waterfall", title, [query("/insights/movers", params, key="main", tone=_tone(flow))],
                 options={"limit": limit, "show_other": show_other, **(options or {})}, **fields)


def choropleth_panel(id: str, title: str, *, measure: str = "share", extent: str = "europe", period: str = PERIOD,
                     flow: str = "EXPORT", max_items: int = 60, options: Optional[dict] = None, **kw) -> dict:
    """Partner countries shaded by share or value (/insights/ranking by partner), or by balance
    (/insights/flows: exports minus imports). measure: share|value|balance; extent: world|europe."""
    fields, filters = _split(kw)
    if measure == "balance":
        q = query("/insights/flows", {"period": period, "max_items": max_items, **filters}, key="main")
    else:
        q = query("/insights/ranking", {"by": "partner", "period": period, "flow": flow, "max_items": max_items,
                                        "threshold": 0, **filters}, key="main", tone=_tone(flow))
    return panel(id, "choropleth", title, [q], options={"measure": measure, "extent": extent, **(options or {})},
                 **fields)


def flowmap_panel(id: str, title: str, *, flow: str = "both", extent: str = "europe", period: str = PERIOD,
                  previous: Optional[str] = None, max_items: int = 10, options: Optional[dict] = None, **kw) -> dict:
    """Arcs from Luxembourg to its partners (/insights/flows), imports in steel and exports in sky.
    flow: imports|exports|both; extent: world|europe. Thread marks run along the arcs."""
    fields, filters = _split(kw)
    params = _drop_none({"period": period, "previous": previous, "max_items": max_items, **filters})
    return panel(id, "flowmap", title, [query("/insights/flows", params, key="main")],
                 options={"flow": flow, "extent": extent, **(options or {})}, **fields)


def heatmap_panel(id: str, title: str, *, kind: str = "month_year", flow: str = "EXPORT", start: Optional[str] = None,
                  end: Optional[str] = None, by: str = "product", measure: str = "share", max_items: int = 12,
                  top: int = 12, options: Optional[dict] = None, **kw) -> dict:
    """kind month_year: months x years of one series (/insights/series freq M); product_year: codes x
    years (/insights/matrix); seasonal: Ivan's month x product seasonal profile (/trends/seasonal-heatmap).
    Trend breaks (month_year, product_year) mark a cell."""
    fields, filters = _split(kw)
    if kind == "month_year":
        q = query("/insights/series", _drop_none({"flow": flow, "freq": "M", "start": start or "1999-01",
                                                  "end": end, **filters}), key="main", tone=_tone(flow))
    elif kind == "product_year":
        q = query("/insights/matrix", {"by": by, "start": start or "1999", "end": end or PERIOD, "flow": flow,
                                       "measure": measure, "max_items": max_items, **filters}, key="main",
                  tone=_tone(flow))
    else:
        q = query("/trends/seasonal-heatmap", _drop_none({"flow": flow, "freq": "M", "start": start, "end": end,
                                                          "top": top, **filters}), key="main", tone=_tone(flow))
    return panel(id, "heatmap", title, [q], options={"kind": kind, **(options or {})}, **fields)


def treemap_panel(id: str, title: str, *, period: str = PERIOD, flow: str = "EXPORT", depth: int = 2,
                  threshold: Any = "auto", options: Optional[dict] = None, **kw) -> dict:
    """The goods as nested tiles (/insights/tree: HS2 > HS4 > HS6 down to ``depth``)."""
    fields, filters = _split(kw)
    q = query("/insights/tree", {"by": "product", "period": period, "flow": flow, "depth": depth,
                                 "threshold": threshold, **filters}, key="main", tone=_tone(flow))
    return panel(id, "treemap", title, [q], options=options, **fields)


def butterfly_panel(id: str, title: str, *, by: str = "product", period: str = PERIOD, max_items: int = 10,
                    options: Optional[dict] = None, **kw) -> dict:
    """Imports to the left, exports to the right, one row per code (/insights/ranking, queries 'in' and 'out')."""
    fields, filters = _split(kw)
    base = {"by": by, "period": period, "max_items": max_items, **filters}
    return panel(id, "butterfly", title,
                 [query("/insights/ranking", {**base, "flow": "IMPORT"}, key="in", tone="imports"),
                  query("/insights/ranking", {**base, "flow": "EXPORT"}, key="out", tone="exports")],
                 options=options, **fields)


def bump_panel(id: str, title: str, *, by: str = "partner", start: str = "2015", end: str = PERIOD,
               flow: str = "EXPORT", max_items: int = 10, options: Optional[dict] = None, **kw) -> dict:
    """Ranks over the years, one line per partner or product (/insights/matrix measure rank)."""
    fields, filters = _split(kw)
    q = query("/insights/matrix", {"by": by, "start": start, "end": end, "flow": flow, "measure": "rank",
                                   "max_items": max_items, **filters}, key="main", tone=_tone(flow))
    return panel(id, "bump", title, [q], options=options, **fields)


def growth_bar_panel(id: str, title: str, *, by: str = "partner", start: str = "1999", end: str = PERIOD,
                     flow: str = "EXPORT", limit: int = 8, level: Optional[int] = None, parent: Optional[str] = None,
                     options: Optional[dict] = None, **kw) -> dict:
    """Growth per year since ``start`` (contract 1.7): a bar of /insights/trends (query 'main'), one bar per partner or
    product, its compound annual growth from ``start`` to ``end`` (the page's year by default), sorted, with its
    first and last value under it. Only items above the endpoint's significance cutoff are ranked; items with no
    value at the start (new ones) have no growth rate and are left out. ``kw``: dimension filters and panel fields."""
    fields, filters = _split(kw)
    params = _drop_none({"by": by, "flow": flow, "start": start, "end": end, "level": level, "parent": parent,
                         "limit": limit, **filters})
    return panel(id, "bar", title, [query("/insights/trends", params, key="main", tone=_tone(flow))],
                 options={"measure": "growth", "x": "name", "y": "growth", "format": "pct_signed", "limit": limit,
                          **(options or {})}, **fields)


def scatter_panel(id: str, title: str, *, by: str = "product", period: str = PERIOD, flow: str = "EXPORT",
                  x: str = "rca", y: str = "growth", size: Optional[str] = "value", growth: Optional[str] = "movers",
                  previous: str = PREVIOUS, start: Optional[str] = None, options: Optional[dict] = None,
                  **kw) -> dict:
    """Bubbles: specialisation (/insights/specialisation, query 'main') joined on code with growth
    (query 'growth': /insights/movers against ``previous``, or /insights/trends from ``start``)."""
    fields, filters = _split(kw)
    queries = [query("/insights/specialisation", {"by": by, "period": period, "flow": flow, **filters}, key="main",
                     tone=_tone(flow))]
    if growth == "movers":
        queries.append(query("/insights/movers", {"by": by, "period": period, "previous": previous, "flow": flow,
                                                  **filters}, key="growth"))
    elif growth == "trends":
        queries.append(query("/insights/trends", _drop_none({"by": by, "start": start, "end": period, "flow": flow,
                                                             **filters}), key="growth"))
    return panel(id, "scatter", title, queries,
                 options={"x": x, "y": y, "size": size, **(options or {})}, **fields)


# --------------------------------------------------------------------------- comparisons (contract 1.2)
COMPARE_MODES = ("absolute", "index", "share", "yoy", "diff")
MAX_COMPARE_SERIES = 6

# Names used when the backend cannot be asked (ViewClient.names asks /insights/compare first).
COUNTRY_NAMES = {
    "LU": "Luxembourg", "BE": "Belgium", "DE": "Germany", "FR": "France", "NL": "Netherlands", "IT": "Italy",
    "ES": "Spain", "PT": "Portugal", "AT": "Austria", "IE": "Ireland", "DK": "Denmark", "SE": "Sweden",
    "FI": "Finland", "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia", "SI": "Slovenia", "HU": "Hungary",
    "RO": "Romania", "BG": "Bulgaria", "HR": "Croatia", "GR": "Greece", "CY": "Cyprus", "MT": "Malta",
    "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania", "CN": "China", "US": "United States",
    "GB": "United Kingdom", "CH": "Switzerland", "JP": "Japan", "KR": "South Korea", "IN": "India",
    "TR": "Türkiye", "NO": "Norway", "TW": "Taiwan", "VN": "Viet Nam", "BR": "Brazil", "RU": "Russia",
    "WORLD": "the world",
}

# Short names of the HS2 chapters (the official names run to a paragraph, and their first words are often not what
# the chapter mostly holds: chapter 84's official name starts "Nuclear reactors, boilers, ..." but it is machinery).
# Every chapter has one, so an agent never names a chapter by its official title.
HS_SHORT = {
    "01": "Live animals", "02": "Meat", "03": "Fish", "04": "Dairy", "05": "Other animal products",
    "06": "Plants and flowers", "07": "Vegetables", "08": "Fruit and nuts", "09": "Coffee, tea and spices",
    "10": "Cereals", "11": "Milling products", "12": "Oil seeds", "13": "Gums and resins",
    "14": "Plaiting materials", "15": "Fats and oils", "16": "Meat and fish preparations", "17": "Sugar",
    "18": "Cocoa and chocolate", "19": "Bakery and cereal products", "20": "Vegetable and fruit preparations",
    "21": "Other food preparations", "22": "Beverages", "23": "Animal feed", "24": "Tobacco",
    "25": "Salt, stone and cement", "26": "Ores", "27": "Mineral fuels", "28": "Inorganic chemicals",
    "29": "Organic chemicals", "30": "Pharmaceuticals", "31": "Fertilisers", "32": "Paints and dyes",
    "33": "Cosmetics and perfumes", "34": "Soap and detergents", "35": "Glues and enzymes",
    "36": "Explosives", "37": "Photographic goods", "38": "Chemical products", "39": "Plastics", "40": "Rubber",
    "41": "Hides and leather", "42": "Leather goods", "43": "Furs", "44": "Wood", "45": "Cork",
    "46": "Basketwork", "47": "Wood pulp", "48": "Paper", "49": "Printed matter", "50": "Silk", "51": "Wool",
    "52": "Cotton", "53": "Other plant fibres", "54": "Man-made filaments", "55": "Man-made fibres",
    "56": "Wadding, felt and rope", "57": "Carpets", "58": "Special fabrics", "59": "Coated textiles",
    "60": "Knitted fabrics", "61": "Knitted clothing", "62": "Clothing", "63": "Other textile articles",
    "64": "Footwear", "65": "Headgear", "66": "Umbrellas", "67": "Feathers and artificial flowers",
    "68": "Stone and cement articles", "69": "Ceramics", "70": "Glass", "71": "Precious metals and stones",
    "72": "Iron and steel", "73": "Articles of iron or steel", "74": "Copper", "75": "Nickel", "76": "Aluminium",
    "78": "Lead", "79": "Zinc", "80": "Tin", "81": "Other base metals", "82": "Tools and cutlery",
    "83": "Metal articles", "84": "Machinery", "85": "Electrical machinery", "86": "Railway vehicles",
    "87": "Vehicles", "88": "Aircraft", "89": "Ships and boats", "90": "Instruments", "91": "Clocks and watches",
    "92": "Musical instruments", "93": "Arms and ammunition", "94": "Furniture", "95": "Toys and games",
    "96": "Miscellaneous manufactured articles", "97": "Works of art", "99": "Other products",
}


def chapter_name(code: str, lower: bool = True) -> str:
    """'machinery (HS 84)': how an agent names an HS2 chapter in a sentence or a title (never its official title).
    Any other code comes back as 'HS <code>'."""
    code = str(code)
    name = HS_SHORT.get(code)
    if not name:
        return f"HS {code}"
    return f"{name[:1].lower() + name[1:] if lower else name} (HS {code})"


_SUBTITLES = {
    "absolute": "€, nominal",
    "share": "Share of {ref}",
    "yoy": "Growth on the year before",
    "diff": "Difference from {ref}",
}


def series_key(code: str) -> str:
    """A query key for a code: DE -> de, 85 -> hs85, 2024 -> y2024 (keys start with a letter)."""
    k = "".join(ch for ch in str(code).lower() if ch.isalnum() or ch == "_")
    if not k or not k[0].isalpha():
        k = ("y" if len(k) == 4 and k.isdigit() else "hs") + k
    return k[:32]


def series_query(key: str, label: Optional[str] = None, *, flow: Optional[str] = "EXPORT", freq: str = "A",
                 start: Optional[str] = None, end: Optional[str] = None, tone: Optional[str] = None,
                 **filters) -> dict:
    """One series of a comparison: /insights/series with its own filters (partner="FR", product="85",
    reporter="DE"). ``label`` is the series' direct label on the chart."""
    params = _drop_none({"flow": flow, "freq": freq, "start": start, "end": end,
                         **{k: v for k, v in filters.items() if v is not None}})
    return query("/insights/series", params, key=key, label=label, tone=tone)


def compare_facts(keys: Iterable[str], base_period: Optional[str] = None, last_step: bool = False) -> dict:
    """Facts of each series of a comparison: ``<key>_now`` (the last value), ``<key>_then`` (the value at
    ``base_period``, else the first one; with ``last_step``, the one before the last) and ``<key>_change``
    (the growth between them), so a text can say "Germany {de_change}, Luxembourg {lu_change}" with every
    figure from the API."""
    then = "-2.value" if last_step else \
        f"[period={base_period}].value" if base_period and not base_period.startswith("$") else "0.value"
    facts: dict = {}
    for k in keys:
        facts[f"{k}_now"] = fact_from(k, "-1.value", "eur")
        facts[f"{k}_then"] = fact_from(k, then, "eur")
        facts[f"{k}_change"] = fact_op("growth", f"{k}_now", f"{k}_then", "pct_signed")
    return facts


def compare_panel(id: str, title: str, queries: list, *, mode: str = "absolute", base_period: Optional[str] = None,
                  reference: Optional[str] = None, focus: Optional[str] = None, facet: Optional[bool] = None,
                  align: Optional[str] = None, balance: Optional[bool] = None, sort: Optional[str] = None,
                  limit: Optional[int] = None, format: Optional[str] = None, options: Optional[dict] = None,
                  **fields) -> dict:
    """A compare panel: 1 to 6 series over time (queries on /insights/series, see ``series_query``), or two
    snapshots side by side (queries keyed 'a' and 'b' on /insights/ranking or /insights/compare).

    mode: absolute | index (100 at ``base_period``) | share (of ``reference``, or of the series' sum) | yoy |
    diff (minus ``reference``). ``focus``: the query key drawn in sky (series) or a row code (snapshots).
    ``facet``: small multiples. ``align="month"``: monthly series of different years on one Jan-Dec axis.
    ``balance``: exports against imports with the gap and the balance. ``fields``: subtitle, text, facts, sources.
    """
    if mode not in COMPARE_MODES:
        raise ValueError(f"mode is one of {', '.join(COMPARE_MODES)}, not {mode!r}")
    opts = _drop_none({"compare": mode, "base_period": base_period, "reference": reference, "focus": focus,
                       "facet": facet, "align": align, "balance": balance, "sort": sort, "limit": limit,
                       "format": format, **(options or {})})
    return panel(id, "compare", title, queries, options=opts, **fields)


def _panel_id(prefix: str, codes: Iterable[str]) -> str:
    return (prefix + "_" + "_".join(series_key(c) for c in codes))[:48].rstrip("_")


def _join(names: list) -> str:
    names = [n for n in names if n]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _since(start: Optional[str], end: Optional[str]) -> str:
    if start and end and not end.startswith("$"):
        return f", {start} to {end}"
    return f", since {start}" if start else ""


def _pair_subtitle(mode: str, a: str, b: str) -> str:
    """The subtitle of two snapshots: 'Growth from 2015 to 2025', 'The year before and the year shown, €'."""
    a = {PREVIOUS: "the year before", PERIOD: "the year shown"}.get(a, a)
    b = {PREVIOUS: "the year before", PERIOD: "the year shown"}.get(b, b)
    text = {"absolute": f"{a} and {b}, € nominal", "share": f"share of the total, {a} and {b}",
            "yoy": f"growth from {a} to {b}", "diff": f"change from {a} to {b}, €"}.get(mode, f"{a} and {b}")
    return text[0].upper() + text[1:]


def _short(name: str, n: int = 40) -> str:
    """'Electrical machinery and equipment ...; parts thereof' -> 'Electrical machinery and equipment ...'."""
    s = str(name or "").split(";")[0].split(" (")[0].replace(" ◀", "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


_PANEL_FIELDS = ("subtitle", "text", "facts", "value", "sources")


def _split(kw: dict) -> tuple[dict, dict]:
    """(panel fields, dimension filters) of a builder's keyword arguments."""
    fields = {k: v for k, v in kw.items() if k in _PANEL_FIELDS}
    filters = {k: v for k, v in kw.items() if k not in _PANEL_FIELDS and v is not None}
    return fields, filters


# --------------------------------------------------------------------------- client
class ViewClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, agent_name: str = "visualizer", timeout: float = 30.0,
                 session: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.agent_name = agent_name
        self.session = session   # the visitor's page (?sid=); None: the shared page
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout,
                                  params={"sid": session} if session else None)
        self._registration: Optional[dict] = None
        self._validator = None

    def close(self) -> None:
        self._http.close()

    # ---- marks
    def add_mark(self, kind: str, target: str, note: str, params: dict, *, series: Optional[str] = None,
                 thread: Optional[str] = None, query: Optional[dict] = None, facts: Optional[dict] = None,
                 period: Optional[str] = None, sources: Optional[Iterable] = None,
                 tmp_id: Optional[str] = None) -> dict:
        """add_mark op. ``note`` may hold {fact} placeholders, never typed-in figures. ``period``: the year
        or month the mark is about; the page dims it when the user looks at another one."""
        mark = _drop_none({"kind": kind, "target": target, "series": series, "thread": thread,
                           "params": dict(params), "query": query, "note": note, "facts": facts, "period": period,
                           "provenance": _sources(sources)})
        return _drop_none({"op": "add_mark", "tmp_id": tmp_id, "mark": mark})

    def trend_break_mark(self, target: str, at: str, note: str, *, label: Optional[str] = None,
                         query: Optional[dict] = None, before: Optional[str] = None, after: Optional[str] = None,
                         from_: Optional[str] = None, to: Optional[str] = None, facts: Optional[dict] = None,
                         **kw) -> dict:
        """A lasting shift in level at ``at`` (a change point of /trends/analyze), on a line or a heatmap.

        With ``query`` (a /trends/analyze query) and no ``facts``, the facts 'before' and 'after' are read
        from the mark's own query (changepoints.[period=<at>].before_mean / after_mean, in euros), so the
        note can say "from {before} to {after} a month".
        """
        if query is not None and facts is None:
            cp = f"changepoints.[period={at}]"
            facts = {"before": fact_from("self", f"{cp}.before_mean", "eur"),
                     "after": fact_from("self", f"{cp}.after_mean", "eur")}
            before, after = "before", "after"
        params = _drop_none({"at": at, "label": label, "before": before, "after": after, "from": from_, "to": to})
        return self.add_mark("trend_break", target, note, params, query=query, facts=facts, **kw)

    def thread_mark(self, target: str, in_code: str, out_code: str, label: str, note: str, *,
                    product: Optional[str] = None, **kw) -> dict:
        """One flow highlighted through the crossroads: bought from in_code, sold to out_code."""
        params = _drop_none({"in_code": in_code, "out_code": out_code, "product": product, "label": label})
        return self.add_mark("thread", target, note, params, **kw)

    def event_mark(self, target: str, at: str, label: str, note: str, *, until: Optional[str] = None,
                   event_id: Optional[str] = None, **kw) -> dict:
        params = _drop_none({"at": at, "until": until, "label": label, "event_id": event_id})
        return self.add_mark("event", target, note, params, **kw)

    def trend_mark(self, target: str, query: dict, note: str, *, label: Optional[str] = None,
                   start: Optional[str] = None, end: Optional[str] = None, **kw) -> dict:
        """A fitted line; ``query`` is an /insights/trend query."""
        return self.add_mark("trend", target, note, _drop_none({"label": label, "start": start, "end": end}),
                             query=query, **kw)

    def forecast_mark(self, target: str, query: dict, note: str, *, label: Optional[str] = None,
                      scenarios: Optional[bool] = None, **kw) -> dict:
        """A band; ``query`` is an /insights/forecast query. ``scenarios=True`` (1.3) also draws the
        response's scenarios ("the smartphone line stops / continues") as labelled points at the band's end."""
        return self.add_mark("forecast", target, note, _drop_none({"label": label, "scenarios": scenarios or None}),
                             query=query, **kw)

    def annotation_mark(self, target: str, x: str, label: str, note: str, **kw) -> dict:
        """A leader line to the panel's own point at x. There is no y: the page reads it from the data."""
        return self.add_mark("annotation", target, note, {"x": x, "label": label}, **kw)

    def highlight_mark(self, target: str, note: str, *, codes: Optional[list] = None,
                       span: Optional[tuple] = None, label: Optional[str] = None, **kw) -> dict:
        """Codes (bars, Sankey nodes, table rows, countries on a map, tiles, butterfly or bump rows,
        bubbles), or ``span=("2020-03", "2020-12")`` on a line."""
        params = _drop_none({"codes": list(codes) if codes else None, "label": label})
        if span:
            params["from"], params["to"] = span
        return self.add_mark("highlight", target, note, params, **kw)

    def update_mark(self, id: str, **fields) -> dict:
        """update_mark op: note, facts, params, series, thread or query."""
        return {"op": "update_mark", "id": id, "set": fields}

    def keep_mark(self, id: str) -> dict:
        return {"op": "keep_mark", "id": id}

    def remove_mark(self, id: str) -> dict:
        return {"op": "remove_mark", "id": id}

    # ---- panels and page
    def upsert_panel(self, panel: dict, after: Optional[str] = None) -> dict:
        """upsert_panel op. ``panel``: {id, type, title, queries, ...}; never data."""
        return _drop_none({"op": "upsert_panel", "panel": panel, "after": after})

    def remove_panel(self, id: str, force: bool = False) -> dict:
        op = {"op": "remove_panel", "id": id}
        if force:
            op["force"] = True
        return op

    def pin_panel(self, id: str, pinned: bool = True) -> dict:
        return {"op": "pin_panel", "id": id, "pinned": pinned}

    def clear(self, scope: str = "marks", agent: Optional[str] = None) -> dict:
        """scope: marks, agent_panels or all. Kept marks and pinned panels always stay."""
        return _drop_none({"op": "clear", "scope": scope, "agent": agent})

    def set_title(self, title: str) -> dict:
        return {"op": "set_title", "title": title}

    def set_context(self, period: Optional[str] = None, product: Optional[str] = None,
                    flow: Optional[str] = None, persona: Optional[str] = None,
                    start: Optional[str] = None) -> dict:
        """Move the page to another year (or month) and/or goods: ``set_context(period="2020")``.
        Panels whose queries hold $period, $previous or $product follow. ``persona`` (contract 1.4)
        draws the page for another reader (``set_context(persona="journalist")``): it changes panels,
        wording and depth, never a figure. ``start`` (1.4) is the From year of a range; see flow_range_ops."""
        op = _drop_none({"op": "set_context", "period": period, "product": product, "flow": flow,
                         "start": start, "persona": persona})
        if len(op) == 1:
            raise ValueError("set_context needs a period, a product, a flow, a start or a persona")
        return op

    def set_panel_context(self, id: str, period: Optional[str] = None, product: Optional[str] = None,
                          start: Optional[str] = None) -> dict:
        """Give ONE panel its own year, range or goods (contract 1.5), which win over the page context for that
        panel, its facts and its marks: ``set_panel_context("mix_export", period="2020")``. The fields given are
        the panel's whole own context; ``set_panel_context("mix_export")`` makes it follow the page again. Only
        what the panel's queries follow can be set ($period/$previous: period, $start: start, $product: product);
        start needs period. To move the whole page, use set_context."""
        return _drop_none({"op": "set_panel_context", "id": id, "period": period, "start": start,
                           "product": product})

    def set_layout(self, rows: Iterable) -> dict:
        """rows: lists of 1-3 panel ids, or {"label", "panels"} dicts. Every panel exactly once."""
        out = []
        for row in rows:
            out.append(dict(row) if isinstance(row, dict) else {"panels": list(row)})
        return {"op": "set_layout", "rows": out}

    # ---- comparisons (contract 1.2): each returns [upsert_panel, and the optional marks], ready for send()
    def names(self, by: str, codes: Iterable[str], period: str = "2025") -> dict:
        """Short names of codes of a dimension (partner, reporter, product), from /insights/compare; codes it
        cannot name keep a fallback (COUNTRY_NAMES, 'HS 85'). Cached per client."""
        codes = [str(c) for c in codes]
        cache = self.__dict__.setdefault("_name_cache", {})
        missing = [c for c in codes if (by, c) not in cache]
        if missing and self.lookup_names:
            try:
                r = self._http.get("/insights/compare", params={"by": by, "period": period, "group": ",".join(missing)},
                                   timeout=5.0)
                if r.status_code == 200 and isinstance(r.json(), list):
                    for row in r.json():
                        if row.get("code") and row.get("name"):
                            cache[(by, str(row["code"]))] = _short(row["name"])
            except (httpx.HTTPError, ValueError):
                pass
        out = {}
        for c in codes:
            name = HS_SHORT.get(c) if by == "product" else None
            name = name or cache.get((by, c))
            if not name or name.lower() == c.lower():  # the API sometimes only echoes the code ("Be")
                name = COUNTRY_NAMES.get(c.upper()) if by in ("partner", "reporter") else None
                name = name or (f"HS {c}" if by == "product" else c)
            out[c] = name
        return out

    lookup_names = True  # set False to build comparisons without asking the backend for names

    def _labels(self, by: str, codes: list, labels: Optional[dict]) -> dict:
        found = self.names(by, [c for c in codes if c not in (labels or {})]) if len(labels or {}) < len(codes) else {}
        return {c: (labels or {}).get(c) or found.get(c) or c for c in codes}

    def _compare_ops(self, panel_: dict, *, highlight: Any = None, annotate: Optional[tuple] = None,
                     note: Optional[str] = None, after: Optional[str] = None, period: Optional[str] = None,
                     thread: Optional[str] = None, facts: Optional[dict] = None) -> list:
        ops = [self.upsert_panel(panel_, after=after)]
        if (highlight or annotate) and not note:
            raise ValueError("a highlight or an annotation needs a note (what the agent says about it)")
        if highlight:
            span = tuple(highlight) if isinstance(highlight, tuple) else None
            ops.append(self.highlight_mark(panel_["id"], note, codes=None if span else list(highlight), span=span,
                                           thread=thread, period=period, facts=facts))
        if annotate:
            x, label = annotate[0], annotate[1]
            series = annotate[2] if len(annotate) > 2 else None
            ops.append(self.annotation_mark(panel_["id"], x, label, note, series=series, thread=thread,
                                            period=period, facts=facts))
        return ops

    def compare_partners(self, partners: Iterable[str], *, flow: str = "EXPORT", start: str = "2015",
                         end: str = PERIOD, mode: str = "absolute", base_period: Optional[str] = None,
                         freq: str = "A", product: Optional[str] = None, focus: Optional[str] = None,
                         facet: Optional[bool] = None, labels: Optional[dict] = None, id: Optional[str] = None,
                         title: Optional[str] = None, subtitle: Optional[str] = None, text: Optional[str] = None,
                         **marks) -> list:
        """Partner against partner over time: one /insights/series per partner (2 to 6), same flow.

        ``view.send(view.compare_partners(["DE", "FR", "BE"], mode="index", base_period="2015"))``.
        ``highlight=("2020", "2021")`` with ``note=`` adds a span; ``annotate=("2025", "France", "fr")`` a point.
        """
        return self._compare_dim("partner", list(partners), flow=flow, start=start, end=end, mode=mode,
                                 base_period=base_period, freq=freq, focus=focus, facet=facet, labels=labels,
                                 id=id, title=title, subtitle=subtitle, text=text,
                                 extra={"product": product}, **marks)

    def compare_products(self, products: Iterable[str], *, flow: str = "EXPORT", start: str = "2015",
                         end: str = PERIOD, mode: str = "absolute", base_period: Optional[str] = None,
                         freq: str = "A", partner: Optional[str] = None, focus: Optional[str] = None,
                         facet: Optional[bool] = None, labels: Optional[dict] = None, id: Optional[str] = None,
                         title: Optional[str] = None, subtitle: Optional[str] = None, text: Optional[str] = None,
                         **marks) -> list:
        """Product against product over time (HS codes: 85, 8517, 85171300): one /insights/series each."""
        return self._compare_dim("product", list(products), flow=flow, start=start, end=end, mode=mode,
                                 base_period=base_period, freq=freq, focus=focus, facet=facet, labels=labels,
                                 id=id, title=title, subtitle=subtitle, text=text,
                                 extra={"partner": partner}, **marks)

    def compare_with_peers(self, peers: Iterable[str], metric: str = "exports", *, start: str = "2015",
                           end: str = PERIOD, mode: Optional[str] = None, base_period: Optional[str] = None,
                           layout: str = "lines", product: Optional[str] = None, facet: Optional[bool] = None,
                           labels: Optional[dict] = None, id: Optional[str] = None, title: Optional[str] = None,
                           subtitle: Optional[str] = None, text: Optional[str] = None,
                           partner: Optional[str] = None, **marks) -> list:
        """Luxembourg against EU reporters (DE, BE, NL...). metric: exports or imports.

        ``partner`` (e.g. "US"): each country's own trade with that partner (Belgium's exports to the United
        States), never Luxembourg's trade with those countries; the backend fetches a missing slice on first use.

        layout "lines": one /insights/series per reporter, Luxembourg in sky, rebased to 100 (mode index) so
        a small and a large economy compare. layout "bars": /insights/compare at ``start`` (a) and ``end`` (b),
        the growth from a to b per reporter (mode yoy), Luxembourg emphasised.
        """
        flow = {"exports": "EXPORT", "imports": "IMPORT"}.get(metric)
        if flow is None:
            raise ValueError("metric is exports or imports")
        codes = ["LU"] + [p for p in peers if p != "LU"]
        if len(codes) > MAX_COMPARE_SERIES and layout == "lines":
            raise ValueError(f"at most {MAX_COMPARE_SERIES - 1} peers on lines; use layout='bars' for more")
        if layout == "lines":
            return self._compare_dim("reporter", codes, flow=flow, start=start, end=end, mode=mode or "index",
                                     base_period=base_period or start, freq="A", focus=series_key("LU"),
                                     facet=facet, labels=labels,
                                     id=id or _panel_id("peers", codes + ([partner.lower()] if partner else [])),
                                     title=title, subtitle=subtitle, text=text,
                                     extra={"product": product, "partner": partner}, metric=metric, **marks)
        names = self._labels("reporter", codes, labels)
        base = _drop_none({"by": "reporter", "flow": flow, "group": ",".join(codes), "product": product,
                           "partner": partner})
        queries = [query("/insights/compare", {**base, "period": start}, key="a", label=start, tone=_tone(flow)),
                   query("/insights/compare", {**base, "period": end}, key="b",
                         label=end if not end.startswith("$") else None, tone=_tone(flow))]
        mode = mode or "yoy"
        peers_words = names[codes[1]] if len(codes) == 2 else f"{len(codes) - 1} EU peers"
        what = f"{metric[:-1].capitalize()} growth" if mode == "yoy" else metric.capitalize()
        p = compare_panel(id or _panel_id("peers_bars", codes + ([partner.lower()] if partner else [])),
                          title or f"{what} of Luxembourg and {peers_words}{self._with(partner, flow)}"
                                   f"{_since(start, end)}",
                          queries, mode=mode, focus="LU", limit=len(codes), sort="b",
                          subtitle=subtitle or _pair_subtitle(mode, start, end), text=text)
        return self._compare_ops(p, **marks)

    def compare_years(self, period_a: str = PREVIOUS, period_b: str = PERIOD, by: str = "partner", *,
                      flow: str = "EXPORT", mode: str = "absolute", limit: int = 8, sort: str = "b",
                      product: Optional[str] = None, partner: Optional[str] = None, focus: Optional[str] = None,
                      id: Optional[str] = None, title: Optional[str] = None, subtitle: Optional[str] = None,
                      text: Optional[str] = None, **marks) -> list:
        """Year against year. by partner or product: two snapshots (/insights/ranking at a and b) side by side,
        with the change per row (mode absolute, share, yoy or diff). by month: the two years month by month on
        one January-to-December axis (/insights/series freq M, align month); then the periods must be years."""
        word = {"EXPORT": "Exports", "IMPORT": "Imports"}.get(flow, "Trade")
        if by == "month":
            if period_a.startswith("$") or period_b.startswith("$") or len(period_a) != 4 or len(period_b) != 4:
                raise ValueError("compare_years by month needs two literal years, such as '2024' and '2025'")
            filters = _drop_none({"product": product, "partner": partner})
            queries = [series_query(series_key(y), y, flow=flow, freq="M", start=f"{y}-01", end=f"{y}-12", **filters)
                       for y in (period_a, period_b)]
            keys = [q["key"] for q in queries]
            p = compare_panel(id or f"months_{period_a}_{period_b}_{flow.lower()}",
                              title or f"{word}, {period_b} against {period_a}, month by month", queries,
                              mode=mode, align="month", focus=focus or keys[1],
                              subtitle=subtitle or _SUBTITLES.get(mode, "").format(ref="the first year"),
                              text=text, facts=compare_facts(keys))
            return self._compare_ops(p, **marks)
        if by not in ("partner", "product"):
            raise ValueError("by is partner, product or month")
        base = _drop_none({"by": by, "flow": flow, "max_items": max(3 * limit, 24), "threshold": 0,
                           "product": product, "partner": partner})
        tone = _tone(flow)
        queries = [query("/insights/ranking", {**base, "period": period_a}, key="a",
                         label=None if period_a.startswith("$") else period_a, tone=tone),
                   query("/insights/ranking", {**base, "period": period_b}, key="b",
                         label=None if period_b.startswith("$") else period_b, tone=tone)]
        what = {"partner": "by partner", "product": "by product"}[by]
        span = "" if period_a.startswith("$") else f", {period_a} and {period_b}"
        p = compare_panel(id or f"years_{by}_{flow.lower()}", title or f"{word} {what}{span}", queries, mode=mode,
                          sort=sort, limit=limit, focus=focus,
                          subtitle=subtitle or _pair_subtitle(mode, period_a, period_b), text=text,
                          facts={"a_total": fact_from("a", "total", "eur"), "b_total": fact_from("b", "total", "eur"),
                                 "change": fact_op("growth", "b_total", "a_total", "pct_signed")})
        return self._compare_ops(p, **marks)

    def compare_flows(self, *, start: str = "2015", end: str = PERIOD, freq: str = "A", partner: Optional[str] = None,
                      product: Optional[str] = None, mode: str = "absolute", id: Optional[str] = None,
                      title: Optional[str] = None, subtitle: Optional[str] = None, text: Optional[str] = None,
                      **marks) -> list:
        """Exports against imports, with the gap shaded and the balance read out (in absolute mode)."""
        filters = _drop_none({"partner": partner, "product": product})
        queries = [series_query("exports", "Exports", flow="EXPORT", freq=freq, start=start, end=end, tone="exports",
                                **filters),
                   series_query("imports", "Imports", flow="IMPORT", freq=freq, start=start, end=end, tone="imports",
                                **filters)]
        facts = compare_facts(["exports", "imports"])
        facts["balance"] = fact_op("diff", "exports_now", "imports_now", "eur_signed")
        where = " with " + self.names("partner", [partner])[partner] if partner else ""
        p = compare_panel(id or _panel_id("flows", [c for c in (partner, product) if c] or ["all"]),
                          title or f"Exports and imports{where}{_since(start, end)}", queries, mode=mode,
                          balance=True if mode == "absolute" else None, focus="exports",
                          subtitle=subtitle or ("€, nominal; the shade is the balance" if mode == "absolute"
                                                else _SUBTITLES.get(mode, "").format(ref="imports")),
                          text=text, facts=facts)
        return self._compare_ops(p, **marks)

    def _with(self, partner: Optional[str], flow: str) -> str:
        """' to the United States' (exports) or ' from China' (imports) for a partner filter, else ''."""
        if not partner:
            return ""
        name = self.names("partner", [partner])[partner]
        name = ("the " + name) if name.startswith(("United ", "Netherlands", "Czech")) else name
        return f" {'from' if flow == 'IMPORT' else 'to'} {name}"

    def _compare_dim(self, by: str, codes: list, *, flow: str, start: str, end: str, mode: str,
                     base_period: Optional[str], freq: str, focus: Optional[str], facet: Optional[bool],
                     labels: Optional[dict], id: Optional[str], title: Optional[str], subtitle: Optional[str],
                     text: Optional[str], extra: dict, metric: Optional[str] = None, **marks) -> list:
        if not 1 <= len(codes) <= MAX_COMPARE_SERIES:
            raise ValueError(f"a comparison draws 1 to {MAX_COMPARE_SERIES} series, not {len(codes)}")
        names = self._labels(by, codes, labels)
        filters = _drop_none(extra)
        # growth on the year before needs the year before the first one shown
        fetch_from = previous_period(start) if mode == "yoy" and start and not start.startswith("$") else start
        queries = [series_query(series_key(c), names[c], flow=flow, freq=freq, start=fetch_from, end=end,
                                **{by: c}, **filters) for c in codes]
        keys = [q["key"] for q in queries]
        word = {"EXPORT": "Exports", "IMPORT": "Imports"}.get(flow, "Trade")
        if title is None:
            if by == "partner":
                title = f"{word} {'to' if flow == 'EXPORT' else 'from'} {_join([names[c] for c in codes])}"
            elif by == "reporter":
                title = (f"{(metric or 'exports').capitalize()} of {_join([names[c] for c in codes])}"
                         f"{self._with(filters.get('partner'), flow)}")
            else:  # product names go lower case inside a sentence-case title
                title = f"{word} of {_join([names[c][:1].lower() + names[c][1:] for c in codes])}"
            title += _since(start, end)
        if subtitle is None:
            subtitle = f"Index, {base_period or start} = 100" if mode == "index" else \
                "Share of the series shown, together" if mode == "share" else \
                _SUBTITLES.get(mode, "").format(ref=names[codes[0]])
        reference = keys[0] if mode == "diff" else None
        p = compare_panel(id or _panel_id(f"compare_{by}", codes), title, queries, mode=mode,
                          base_period=base_period if mode == "index" else None, reference=reference,
                          focus=series_key(focus) if focus else keys[0], facet=facet or None,
                          subtitle=subtitle[:200], text=text,
                          facts=compare_facts(keys, base_period, last_step=mode == "yoy"))
        return self._compare_ops(p, **marks)

    # ---- sending
    def envelope(self, ops: list, message: Optional[str] = None, base_version: Optional[int] = None,
                 run_id: Optional[str] = None) -> dict:
        """The POST /view/ops body. One run_id per batch: resending the same run_id is a safe retry."""
        run_id = run_id or f"{self.agent_name}-{uuid.uuid4().hex[:12]}"
        return _drop_none({"agent": {"name": self.agent_name, "run_id": run_id}, "base_version": base_version,
                           "message": message, "ops": list(ops)})

    def validate(self, envelope: dict) -> list[str]:
        """Schema errors of a batch, checked locally ("<JSON pointer>: <message>"); [] when valid.

        The backend also checks what needs the current spec (targets, series) and pre-flights queries.
        """
        validator = self._schema_validator()
        if validator is None:
            return []
        errors = []
        for e in validator.iter_errors(envelope):
            path = "".join(f"/{p}" for p in e.absolute_path)
            errors.append(f"{path or '/'}: {e.message}")
        return sorted(errors)

    def send(self, ops: list, message: Optional[str] = None, base_version: Optional[int] = None,
             run_id: Optional[str] = None) -> dict:
        """Validate locally, POST /view/ops, return {version, assigned, warnings, spec}.

        Raises ValueError for a locally invalid batch, ViewError for a 409/422 from the backend.
        """
        env = self.envelope(ops, message, base_version, run_id)
        problems = self.validate(env)
        if problems:
            raise ValueError("invalid ops:\n" + "\n".join(problems))
        r = self._http.post("/view/ops", json=env)
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {"detail": r.text}
            if isinstance(body, dict) and isinstance(body.get("detail"), dict):
                body = {**body["detail"], **{k: v for k, v in body.items() if k != "detail"}}
            raise ViewError(r.status_code, body)
        return r.json()

    def spec(self, since: Optional[int] = None) -> dict:
        """GET /view: the current spec (with since=N, the backend may answer 204 when nothing changed)."""
        r = self._http.get("/view", params={"since": since} if since is not None else None)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ---- registry
    def register(self, title: str, color: str, capabilities: list, examples: Optional[list] = None) -> dict:
        """POST /agents/register. capabilities: [{kind, produces, description, inputs}]; examples: prompts.

        Registrations expire after 60 s: call heartbeat() (or keep_alive()) every 30 s or so.
        """
        self._registration = {"name": self.agent_name, "title": title, "color": color,
                              "capabilities": list(capabilities), "examples": list(examples or [])}
        return self.heartbeat()

    def heartbeat(self) -> dict:
        if self._registration is None:
            raise RuntimeError("call register() first")
        r = self._http.post("/agents/register", json=self._registration)
        r.raise_for_status()
        return r.json() if r.content else {}

    def keep_alive(self, interval: float = 30.0) -> threading.Event:
        """Heartbeat in a daemon thread; set the returned event to stop."""
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(interval):
                try:
                    self.heartbeat()
                except Exception:  # backend restarting: try again next time
                    pass

        threading.Thread(target=loop, daemon=True, name=f"heartbeat-{self.agent_name}").start()
        return stop

    def _schema_validator(self):
        if self._validator is None:
            try:
                from jsonschema import Draft202012Validator
                from referencing import Registry, Resource
            except ImportError:
                return None
            spec = json.loads((CONTRACT_DIR / "view-spec.schema.json").read_text(encoding="utf-8"))
            ops = json.loads((CONTRACT_DIR / "ops.schema.json").read_text(encoding="utf-8"))
            registry = Registry().with_resources([(spec["$id"], Resource.from_contents(spec)),
                                                  (ops["$id"], Resource.from_contents(ops))])
            self._validator = Draft202012Validator(ops, registry=registry)
        return self._validator
