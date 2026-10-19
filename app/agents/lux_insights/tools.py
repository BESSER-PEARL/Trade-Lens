# SPDX-License-Identifier: MIT
"""The specialists' tools: read numbers from /insights and /trends, and draw on the page with typed ops.

Every specialist gets a ``Toolbox`` under its own name, so its marks come out in its own pen. The
read tools return compact results with the figures already written the way the page writes them
("€1.51 bn", "+8.6%"), so a chat reply quotes them as they are. The act tools are small, typed
builders on top of ``agents.kit``:

1. they read the page (GET /view) and build ops against it (no duplicates: a mark that is already
   there is updated, not drawn twice; pinned panels are never overwritten);
2. they check the batch locally with the backend's own validator, and resolve every {fact} the
   page will show, so a wrong path comes back to the model as an error instead of a blank;
3. they send it with ``base_version`` and a run id, and rebuild on a 409 (up to three times: specialists may work at once);
4. they return ``{"ok", "marks": [{"number", ...}], "panels", "figures", "errors"}``: the page numbers
   to cite as [n], the figures the page now shows, or the 422 errors to fix.

Text that goes on the page never carries a typed figure: notes and captions use {placeholders}
from a small vocabulary each tool documents, and the tool builds the facts behind them.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import logging
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .settings import ROOT, ensure_import_paths

ensure_import_paths()

from agents.kit.view import COUNTRY_NAMES, HS_SHORT  # noqa: E402
from agents.kit import (AGENTS, InsightsClient, InsightsError, ViewClient, ViewError, bump_panel,  # noqa: E402
                        butterfly_panel, choropleth_panel, fact_api, fact_from, fact_op, flowmap_panel, get_path,
                        growth_bar_panel, heatmap_panel, panel, query, scatter_panel, treemap_panel,
                        waterfall_panel)

from . import fmt, page, products  # noqa: E402

log = logging.getLogger("lux_insights.tools")

ROLES = ("trend_finder", "cause_finder", "forecaster", "visualizer")
BACKEND_VIEW = ROOT / "app" / "backend" / "view"
_CONTRACT = "lux_insights_view_contract"
_contract_lock = threading.Lock()


def contract():
    """The backend's view package (validate_ops, fill_params, ...), imported by path: the agent checks
    a batch exactly as the backend will before it sends it."""
    with _contract_lock:
        if _CONTRACT in sys.modules:
            return sys.modules[_CONTRACT]
        spec = importlib.util.spec_from_file_location(_CONTRACT, BACKEND_VIEW / "__init__.py",
                                                      submodule_search_locations=[str(BACKEND_VIEW)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[_CONTRACT] = module
        spec.loader.exec_module(module)
        return module


# --------------------------------------------------------------------------- tools the LLM can call
class ToolProblem(Exception):
    """A problem the model can fix (a bad argument, a missing panel): its text goes back as the result."""


def tool(description: str, properties: Optional[dict] = None, required: Iterable[str] = ()):
    """Mark a Toolbox method as a tool, with the JSON schema of its arguments."""
    def wrap(fn):
        fn._tool = {"description": description, "properties": properties or {}, "required": list(required)}
        return fn
    return wrap


def S(desc: str, **kw) -> dict:
    return {"type": "string", "description": desc, **kw}


def I(desc: str, **kw) -> dict:  # noqa: E743
    return {"type": "integer", "description": desc, **kw}


def B(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


def L(desc: str, items: Optional[dict] = None) -> dict:
    return {"type": "array", "description": desc, "items": items or {"type": "string"}}


FIGURES_DOC = ("only when the built-in placeholders do not cover it: extra facts {name, endpoint, params, path, "
               "format}. path reads the RAW API answer, not the summaries you were shown: /insights/series is a bare "
               "list ('[period=2019].value', '-1.value'), /insights/ranking has 'items.[code=FR].value'. format is "
               "eur, eur_signed, pct, pct_signed, percent, int or text")
FLOW = S("EXPORT or IMPORT", enum=["EXPORT", "IMPORT"])
PERIOD = S("a year (2025) or a month (2025-03)")
START = S("the first period: the one the user asked for ('since 1999'); left out, a long-run view starts in "
          "1999, the first year of the data (2015 for HS6/CN8 lines)")


class LuxTool:
    """A callable the model can use: the same interface as BAF's ``baf.reasoning.tool.Tool``
    (``name``, ``description``, ``openai_schema``, ``call(args) -> str``), with a hand-written schema."""

    def __init__(self, fn: Callable, name: str, description: str, properties: dict, required: list,
                 on_result: Optional[Callable[[Any], None]] = None):
        self.fn, self.name, self.description = fn, name, description
        self.schema = {"type": "object", "properties": properties, "required": required}
        self.on_result = on_result

    @property
    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.schema}}

    def call(self, args: Optional[dict]) -> str:
        args = dict(args or {})
        props = self.schema["properties"]
        unknown = [k for k in args if k not in props]
        missing = [k for k in self.schema["required"] if args.get(k) in (None, "")]
        if unknown or missing:
            return "ERROR: " + "; ".join(([f"unknown argument(s) {unknown}"] if unknown else [])
                                          + ([f"missing argument(s) {missing}"] if missing else []))
        args = {k: v for k, v in args.items() if v is not None}
        try:
            result = self.fn(**args)
        except ToolProblem as exc:
            result = {"ok": False, "error": str(exc)}
        except InsightsError as exc:
            result = {"ok": False, "error": f"the API answered {exc.status} on {exc.url}: {exc.detail}"}
        except ViewError as exc:
            result = {"ok": False, "error": str(exc), "errors": exc.errors}
        except Exception as exc:  # the model reads the error and tries something else
            log.exception("tool %s failed", self.name)
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if self.on_result:
            self.on_result(result)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------- the record of one run
@dataclass
class Turn:
    """What one specialist did while answering one question."""
    agent: str
    sources: list[str] = field(default_factory=list)       # API calls made, relative URLs
    seen: list[tuple[str, float]] = field(default_factory=list)  # figures shown to the model
    marks: list[str] = field(default_factory=list)         # mark ids added or updated
    removed: list[int] = field(default_factory=list)       # page numbers removed
    panels: list[str] = field(default_factory=list)        # panels added or replaced
    version: Optional[int] = None
    spec: Optional[dict] = None                            # the page after the last batch
    actions: list[str] = field(default_factory=list)       # one line per batch, for the next specialist
    done: list[str] = field(default_factory=list)          # act tools that changed the page
    # events about one product or partner drawn in this turn: {label, words, line, totals, line_figures}, so the
    # reply can be checked for a total-trade figure quoted as the event's own (specialists.scoped_total_problems)
    scoped: list[dict] = field(default_factory=list)
    # shares of ONE product line (China's share of the smartphones bought, France's of those sold), each with the
    # sentence that says its base right, so a reply that writes "87% of imports from China" can be caught
    shares: list[dict] = field(default_factory=list)
    # events drawn in this turn with the nearest trend break: {label, at, nearest, months, near}
    event_breaks: list[dict] = field(default_factory=list)
    breaks: list[str] = field(default_factory=list)     # the trend breaks those were measured against
    events_from: Optional[str] = None                   # the first year of the curated list of events
    cleared: Optional[dict] = None                      # clear_page ran: {"kept": [n], "pinned": [ids]}
    # agent charts a new chart of this turn took the place of (the page keeps 3): [{id, title, type, by}]
    replaced: list[dict] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)    # tools that answered ok:false
    backtest: Optional[dict] = None                     # get_backtest: {"bullets": [...], ...}
    # trade with no partner specified (the QV, QW, QS codes) read in this turn, as (kind, value): the only
    # figures a reply may call "confidential" or "not specified" (unspecified_problems)
    unspecified: list[tuple] = field(default_factory=list)
    # product codes read or drawn in this turn (HS2 to CN8): a text naming them is checked against the official
    # titles' misleading words ("nuclear reactors" is not chapter 84, machinery: products.misnamed)
    products: list[str] = field(default_factory=list)
    # goods asked for in words that Luxembourg did not trade (find_codes): {goods, codes, flows, year, sentence}
    no_trade: list[dict] = field(default_factory=list)
    renamed: list[dict] = field(default_factory=list)   # phrases renamed on the page: {phrase, name}
    scenarios: Optional[str] = None      # get_forecast: the scenarios said as the line's level (scenarios_words)
    beyond_tested: bool = False          # a forecast year further ahead than the backtest tested (12 months)
    unchanged: list[str] = field(default_factory=list)  # what an act tool found already on the page (kinds)
    drawn_kinds: list[str] = field(default_factory=list)  # what an act tool drew new in this turn (kinds)
    ranks: list[tuple] = field(default_factory=list)    # (name or '', rank) shown to the model in this turn
    quantities: list[dict] = field(default_factory=list)  # get_quantities: {product, use: units|weight|None, unit}

    def see(self, result: Any) -> None:
        """Remember every figure the model was shown (strings like '€1.5 bn', '+8.6%')."""
        for text in _strings(result):
            for _, kind, v in fmt.figures_in(text):
                self.seen.append((kind, v))
        self.ranks += ranks_shown(result)


def ranks_shown(obj: Any) -> list[tuple[str, int]]:
    """The ranks a tool result shows: (name in lower case, rank) from rank rows ({name, '1999': 4, '2025': 9} of a
    rank matrix, {name, rank}), the order of a ranking's items (those with a share), and ('', n) for a rank the
    page wrote out on its own ('4th')."""
    out: list[tuple[str, int]] = []
    if isinstance(obj, dict):
        name = str(obj.get("name") or "").lower()
        if name:
            for k, v in obj.items():
                if isinstance(v, int) and not isinstance(v, bool) and (re.fullmatch(r"(?:19|20)\d\d", str(k))
                                                                       or k == "rank"):
                    out.append((name, v))
        for v in obj.values():
            out += ranks_shown(v)
    elif isinstance(obj, list):
        if obj and all(isinstance(i, dict) and i.get("name") and "share" in i for i in obj):
            out += [(str(i["name"]).lower(), n) for n, i in enumerate(obj, 1)]
        for v in obj:
            out += ranks_shown(v)
    elif isinstance(obj, str):
        m = re.fullmatch(r"(\d{1,3})(?:st|nd|rd|th)", obj.strip())
        if m:
            out.append(("", int(m.group(1))))
    return out


def _no_rows(endpoint: str, data: Any) -> Optional[bool]:
    """True when an answer has nothing to draw, False when it has, None when it cannot tell."""
    if endpoint == "/insights/series":
        rows = data.get("rows") if isinstance(data, dict) else data
        return not any(r.get("value") for r in rows or [])
    if endpoint == "/insights/ranking/records":
        return not any(r.get("value") for r in data or [] if r.get("code") != "OTHER")
    if endpoint == "/insights/ranking":
        return not (data or {}).get("items")
    if endpoint == "/insights/movers":
        return not ((data or {}).get("up") or (data or {}).get("down") or (data or {}).get("new"))
    if endpoint == "/insights/tree":
        return not (data or {}).get("children")
    if endpoint == "/insights/trends":
        return not (data or {}).get("items")
    return None


def _strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


# --------------------------------------------------------------------------- compact results
def _name(item: dict, limit: int = 50) -> str:
    """A short name: 'Machinery' for chapter 84, not 'Nuclear reactors' (the official name's first words)."""
    code = str(item.get("code") or "")
    if code in HS_SHORT:
        return HS_SHORT[code]
    if code.isdigit() and len(code) > 2:   # a heading or a line: its goods, not only the first word before a comma
        return products.name_of(code, item.get("name") or item.get("label"), limit)
    return fmt.short(item.get("name") or item.get("label") or code, limit)


def _rows(items: list, n: int, fields: dict[str, Callable[[dict], Any]]) -> list[dict]:
    out = []
    for item in (items or [])[:n]:
        row = {}
        for k, get in fields.items():
            try:
                v = get(item)
            except (KeyError, TypeError, ValueError):
                v = None
            if v not in (None, ""):
                row[k] = v
        out.append(row)
    return out


def _series_summary(rows: list[dict]) -> dict:
    rows = [r for r in rows or [] if r.get("value") is not None]
    if not rows:
        return {"rows": 0}
    out: dict = {"rows": len(rows), "first": {"period": rows[0]["period"], "value": fmt.eur(rows[0]["value"])},
                 "last": {"period": rows[-1]["period"], "value": fmt.eur(rows[-1]["value"])}}
    peak = max(rows, key=lambda r: r["value"])
    low = min(rows, key=lambda r: r["value"])
    out["highest"] = {"period": peak["period"], "value": fmt.eur(peak["value"])}
    out["lowest"] = {"period": low["period"], "value": fmt.eur(low["value"])}
    if len(rows) <= 16:
        out["values"] = {r["period"]: fmt.eur(r["value"]) for r in rows}
    else:
        out["last_12"] = {r["period"]: fmt.eur(r["value"]) for r in rows[-12:]}
    if len(rows) >= 2 and rows[0]["value"]:
        out[f"growth_{rows[0]['period']}_to_{rows[-1]['period']}"] = fmt.growth(rows[-1]["value"] / rows[0]["value"] - 1,
                                                                                      base=str(rows[0]["period"]))
    if len(rows) >= 3 and rows[-2]["value"]:
        out[f"growth_{rows[-2]['period']}_to_{rows[-1]['period']}"] = fmt.growth(rows[-1]["value"] / rows[-2]["value"] - 1,
                                                                                        base=str(rows[-2]["period"]))
    return out


def _quantity(v: Optional[float], unit: Optional[str], signed: bool = False) -> Optional[str]:
    """37654 items, 122,464 t (contract 1.8: a count or a weight is never written in euros)."""
    if v is None:
        return None
    sign = ("+" if v > 0 else fmt.MINUS if v < 0 else "") if signed else (fmt.MINUS if v < 0 else "")
    return f"{sign}{abs(v):,.0f} {unit or 'units'}"


def _summarize_quantity(endpoint: str, data: Any, n: int) -> Optional[Any]:
    """A response read with measure weight or units: its figures with their unit, and the value in euros beside."""
    if isinstance(data, dict) and data.get("measure") in ("weight", "units"):
        unit = data.get("unit")
        if endpoint == "/insights/movers":
            row = {"code": lambda i: i["code"], "name": _name, "before": lambda i: _quantity(i["previous"], unit),
                   "after": lambda i: _quantity(i["value"], unit),
                   "change": lambda i: _quantity(i["change"], unit, signed=True)}
            return {"measure": data["measure"], "unit": unit, "period": data.get("period"),
                    "previous": data.get("previous"), "total_before": _quantity(data.get("previous_total"), unit),
                    "total_after": _quantity(data.get("total"), unit), "up": _rows(data.get("up"), n, row),
                    "down": _rows(data.get("down"), max(3, n // 2), row)}
        if data.get("items") is not None:
            row = {"code": lambda i: i["code"], "name": _name, "quantity": lambda i: _quantity(i["value"], unit),
                   "value_in_euros": lambda i: fmt.eur(i.get("value_eur")), "share": lambda i: fmt.pct(i["share"])}
            return {"measure": data["measure"], "unit": unit, "period": data.get("period"),
                    "total": _quantity(data.get("total"), unit), "items": _rows(data.get("items"), n, row)}
    rows = data.get("rows") if isinstance(data, dict) and isinstance(data.get("rows"), list) else data
    if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "unit" in rows[0] and "weight" not in rows[0]:
        unit = rows[0]["unit"]
        if "period" in rows[0]:
            return {"unit": unit, "rows": [{"period": r["period"], "quantity": _quantity(r.get("value"), unit),
                                            "value_in_euros": fmt.eur(r.get("value_eur"))} for r in rows[-n:]]}
        return {"unit": unit, "rows": [{"code": r.get("code"), "name": _name(r), "quantity": _quantity(r.get("value"), unit),
                                        "value_in_euros": fmt.eur(r.get("value_eur"))} for r in rows[:n]]}
    return None


def summarize(endpoint: str, data: Any, n: int = 6) -> Any:
    """The part of an answer the model needs, figures written out. Never the raw response."""
    quantity = _summarize_quantity(endpoint, data, n)
    if quantity is not None:
        return quantity
    item_fields = {"code": lambda i: i["code"], "name": _name, "value": lambda i: fmt.eur(i["value"]),
                   "share": lambda i: fmt.pct(i["share"])}
    if endpoint == "/insights/series":
        rows = data.get("rows") if isinstance(data, dict) else data
        return _series_summary(rows)
    if endpoint == "/insights/ranking":
        out = {"period": data.get("period"), "total": fmt.eur(data.get("total")),
               "items": _rows(data.get("items"), n, item_fields)}
        if data.get("fetched_live"):
            out["fetched_live"] = f"this detail came live from {data.get('source') or 'Eurostat'} just now"
        other, partners = data.get("other") or {}, data.get("by") == "partner"
        if other.get("value") is not None:
            # the bucket of everything not listed: other partners (products), never "confidential"
            out["other_partners" if partners else "other_products"] = {
                "value": fmt.eur(other["value"]), "count": other.get("count"),
                "say": (f"the other {other.get('count')} partners together" if partners
                        else f"the other {other.get('count')} products together")
                       + " (not confidential, not 'not specified': just not listed one by one)"}
        unallocated = data.get("unallocated") or {}
        if partners and unallocated.get("value") is not None:
            out["partner_not_specified"] = {
                "value": fmt.eur(unallocated["value"]), "share": fmt.pct(unallocated.get("share")),
                "say": "trade with no partner specified (codes QV, QW, QS): the only figure to call 'not specified'"}
        return out
    if endpoint in ("/insights/ranking/records", "/insights/specialisation/records"):
        return _rows(data, n, {"code": lambda i: i["code"], "name": _name, "value": lambda i: fmt.eur(i["value"]),
                               "share": lambda i: fmt.pct(i["share"] / 100) if "share" in i else None,
                               "rca": lambda i: round(i["rca"], 2) if "rca" in i else None})
    if endpoint == "/insights/movers":
        mover = {"code": lambda i: i["code"], "name": _name, "before": lambda i: fmt.eur(i["previous"]),
                 "after": lambda i: fmt.eur(i["value"]), "change": lambda i: fmt.eur(i["change"], signed=True),
                 "growth": lambda i: fmt.growth(i["growth"], base=data.get("previous"))
                 if i.get("growth") is not None else None}
        out = {"period": data.get("period"), "previous": data.get("previous")}
        if data.get("fetched_live"):
            out["fetched_live"] = f"this detail came live from {data.get('source') or 'Eurostat'} just now"
        if data.get("total") is not None and data.get("previous_total") is not None:
            t, p = data["total"], data["previous_total"]
            out.update(total_before=fmt.eur(p), total_after=fmt.eur(t), change=fmt.eur(t - p, signed=True),
                       growth=fmt.pct(t / p - 1, signed=True) if p else None)
        out["up"] = _rows(data.get("up"), n, mover)
        out["down"] = _rows(data.get("down"), max(3, n // 2), mover)
        if data.get("new"):
            out["new"] = _rows(data.get("new"), 3, mover)
        return out
    if endpoint == "/insights/flows":
        side = lambda s: {"total": fmt.eur((data.get(s) or {}).get("total")),  # noqa: E731
                          "top": _rows((data.get(s) or {}).get("items"), n, item_fields)}
        return {"period": data.get("period"), "imports_from": side("in"), "exports_to": side("out"),
                "balance": fmt.eur(data.get("balance"), signed=True) if data.get("balance") is not None else None}
    if endpoint == "/insights/compare/records":
        focus = next((i for i, r in enumerate(data) if r.get("is_focus")), None)
        return {"rows": len(data), "luxembourg_position": (focus + 1) if focus is not None else None,
                "items": _rows(data, 30, {"code": lambda i: i["code"], "name": _name,
                                          "value": lambda i: fmt.eur(i["value"]),
                                          "growth": lambda i: fmt.pct(i["growth"] / 100, signed=True)})}
    if endpoint == "/insights/compare":
        return _rows(data, 30, item_fields)
    if endpoint == "/insights/specialisation":
        return {"period": data.get("period"), "items": _rows(data.get("items"), n, {
            "code": lambda i: i["code"], "name": _name, "value": lambda i: fmt.eur(i["value"]),
            "share": lambda i: fmt.pct(i["share"]), "eu_share": lambda i: fmt.pct(i["peer_share"]),
            "rca": lambda i: round(i["rca"], 2)})}
    if endpoint == "/insights/tree":
        return {"period": data.get("period"), "total": fmt.eur(data.get("total")),
                "children": [{**_rows([c], 1, item_fields)[0],
                              "inside": _rows(c.get("children"), 3, item_fields)} for c in (data.get("children") or [])[:n]]}
    if endpoint == "/insights/matrix":
        measure = data.get("measure", "value")
        show = {"value": fmt.eur, "share": fmt.pct, "growth": lambda v: fmt.pct(v, signed=True),
                "rank": lambda v: int(v)}.get(measure, fmt.eur)
        periods = data.get("periods") or []

        def first_last(r):
            vals = [(p, v) for p, v in zip(periods, r.get("values") or []) if v is not None]
            return {vals[0][0]: show(vals[0][1]), vals[-1][0]: show(vals[-1][1])} if vals else {}
        return {"measure": measure, "periods": f"{periods[0]} to {periods[-1]}" if periods else None,
                "rows": [{"code": r["code"], "name": _name(r), **first_last(r)} for r in (data.get("rows") or [])[:8]]}
    if endpoint == "/insights/trend":
        return {"method": data.get("method_label") or data.get("method"), "start": data.get("start"),
                "end": data.get("end"), "start_value": fmt.eur(data.get("start_value")),
                "end_value": fmt.eur(data.get("end_value")),
                "growth_per_year": fmt.pct(data.get("cagr") if data.get("cagr") is not None else data.get("slope_per_year"),
                                           signed=True),
                "fit_r2": round(data["r2"], 2) if data.get("r2") is not None else None}
    if endpoint == "/insights/trends":
        return {"start": data.get("start"), "end": data.get("end"), "items": _rows(data.get("items"), n, {
            "code": lambda i: i["code"], "name": _name, "value": lambda i: fmt.eur(i["value"]),
            "since_start": lambda i: fmt.eur(i["start_value"]),
            "growth_per_year": lambda i: f"{fmt.pct(i['growth'], signed=True)} a year"})}
    if endpoint == "/trends/analyze":
        return {"start": data.get("start"), "end": data.get("end"), "direction": data.get("direction"),
                "shape": data.get("shape"), "trend_summary": data.get("trend_summary"),
                "change_over_span": (fmt.pct(data["relative_change"], signed=True)
                                     if data.get("relative_change") is not None else None),
                "trend_strength": fmt.pct(data.get("trend_strength")),
                "seasonality": data.get("seasonality_label"), "seasonality_summary": data.get("seasonality_summary"),
                "changepoints": [{"at": c["period"], "level_before": fmt.eur(c["before_mean"]),
                                  "level_after": fmt.eur(c["after_mean"]),
                                  "change": fmt.pct(c.get("relative_change"), signed=True)}
                                 for c in data.get("changepoints") or []]}
    if endpoint == "/trends/seasonal-heatmap":
        out = []
        for r in (data.get("rows") or [])[:n]:
            pat = [p for p in r.get("pattern") or [] if p.get("average_effect_pct") is not None]
            if pat:
                hi = max(pat, key=lambda p: p["average_effect_pct"])
                lo = min(pat, key=lambda p: p["average_effect_pct"])
                out.append({"code": r["code"], "name": _name(r),
                            "peak": f"{hi['label']} {fmt.pct(hi['average_effect_pct'] / 100, signed=True)}",
                            "trough": f"{lo['label']} {fmt.pct(lo['average_effect_pct'] / 100, signed=True)}"})
        return {"rows": out}
    if endpoint == "/insights/events":
        items = data.get("items") if isinstance(data, dict) else data
        return [{"id": e.get("id"), "at": e.get("at"), "until": e.get("until"), "label": e.get("label"),
                 "category": e.get("category"), "about": fmt.short(e.get("title") or "", 90),
                 "partners": ((e.get("scope") or {}).get("partners") or [])[:4] or None}
                for e in items or []]
    if endpoint == "/insights/ladder":
        return {"total": fmt.eur(data.get("total")), "n_items": data.get("n_items"),
                "ladder": [{"label": s.get("label"), "items": s.get("items"), "coverage": fmt.pct(s.get("coverage"))}
                           for s in data.get("ladder") or []][:5]}
    return data


# --------------------------------------------------------------------------- the toolbox
CODE_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
PERIOD_RE_LOCAL = re.compile(r"^[0-9]{4}(-(0[1-9]|1[0-2]))?$")
PANEL_ID_RE = re.compile(r"[^a-z0-9_]+")


def _slug(*parts: Any, limit: int = 48) -> str:
    s = "_".join(str(p).lower() for p in parts if p not in (None, "", "TOTAL") and not isinstance(p, bool))
    s = PANEL_ID_RE.sub("_", s).strip("_")
    if not s or not s[0].isalpha():
        s = "p_" + s
    return s[:limit].rstrip("_")


def _thread(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    s = _slug(name, limit=32)
    return s or None


def _label(text: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _year(period: str) -> str:
    return str(period)[:4]


HEADLINE_MAX = 160           # the 'In short' headline: two lines at 1440 px
TITLE_MAX = 120              # the page title: two lines at its size
FIRST_YEAR = "1999"          # Luxembourg reports alone from 1999 (inside BLEU before); /insights/meta says it too
DETAIL_FROM = "2015"         # HS6 and CN8 lines (against the world) start in 2015
_YEAR = r"(19\d\d|20\d\d)"
_SPAN_RES = [re.compile(rf"\bbetween\s+{_YEAR}\s+and\s+{_YEAR}\b", re.I),
             re.compile(rf"\bfrom\s+{_YEAR}\s*(?:to|until|till|through|-|–)\s*{_YEAR}\b", re.I),
             re.compile(rf"\b{_YEAR}\s*(?:-|–|to)\s*{_YEAR}\b", re.I)]
_SINCE_RE = re.compile(rf"\b(?:since|from|after|starting(?:\s+in)?)\s+{_YEAR}\b", re.I)
_BASE_RE = re.compile(rf"\b{_YEAR}\s*=\s*100\b|\bbase(?:\s+year)?\s*(?:of\s+)?{_YEAR}\b", re.I)


def asked_span(question: str) -> dict:
    """The period a question asks for, in years: {"start", "end", "base"} (each optional).
    'since 1999' -> start 1999; 'from 2010 to 2020', 'between 2010 and 2020', '2010-2020' -> start and end;
    '2015 = 100' -> base 2015. Years outside 1999..2030 are ignored (Luxembourg reports alone from 1999)."""
    out: dict = {}
    q = question or ""
    for rx in _SPAN_RES:
        m = rx.search(q)
        if m:
            a, b = sorted((m.group(1), m.group(2)))
            out.update(start=a, end=b)
            break
    if "start" not in out:
        m = _SINCE_RE.search(q)
        if m:
            out["start"] = m.group(1)
    m = _BASE_RE.search(q)
    if m:
        out["base"] = m.group(1) or m.group(2)
    return {k: v for k, v in out.items() if "1999" <= v <= "2030"}


# --------------------------------------------------------------------------- what the question asks for, in code
_CLEAR_RE = re.compile(r"\b(clear|reset|wipe|start\s+(?:over|again|afresh|from\s+scratch)|from\s+scratch|"
                       r"clean\s+(?:up\s+)?the\s+page|empty\s+the\s+page|blank\s+page|fresh\s+page)\b", re.I)
SHOW_YEAR_RE = re.compile(r"^\s*(?:please\s+)?(?:show|take|move|go|switch|jump|turn)\s+(?:me\s+|us\s+|the\s+page\s+|to\s+)*"
                          r"(?:the\s+)?(?:year\s+)?(?:in\s+)?((?:19|20)\d\d)\s*(?:please\s*)?[?.!]*\s*$", re.I)
TABLE_RE = re.compile(r"\b(?:as|in|into)\s+a\s+table\b|\btable\s+of\b|\ba\s+table\b|\btabular\b", re.I)
YOY_RE = re.compile(r"\byear[- ]on[- ]year\b|\byoy\b|\bannual\s+growth\b|\bgrowth\s+(?:rates?\s+)?(?:on|over)\s+the\s+"
                    r"(?:previous\s+year|year\s+before)\b|\bgrowth\s+(?:each|every)\s+year\b", re.I)
GAP_RE = re.compile(r"\bgap\b|\bdifference\s+between\b|\bbalance\b", re.I)
TWO_WAYS_RE = re.compile(r"\bbought\s+(?:vs\.?|versus|against|and)\s+sold\b|\bsold\s+(?:vs\.?|versus|against)\s+"
                         r"bought\b|\bimports?\s+(?:vs\.?|versus|against|and)\s+exports?\s+(?:partner\s+by\s+partner|by\s+"
                         r"partner|by\s+(?:chapter|product)|chapter\s+by\s+chapter)", re.I)
BY_PRODUCT_RE = re.compile(r"\bby\s+(?:chapter|product|goods)\b|\bchapter\s+by\s+chapter\b|\bproduct\s+by\s+product\b",
                           re.I)
_YEARS_RE = re.compile(r"\b(19\d\d|20\d\d)\b")
TWO_YEARS_RE = re.compile(r"\b(?:two|2)\s+(?:next\s+)?years?\b|\bnext\s+(?:two|2)\b|\b2027\b|\b24\s+months\b",
                          re.I)
GROWTH_TITLE_RE = re.compile(r"\b(?:grow\w*|grew|fastest|rising|rise|rose|risers|fall\w*|fell|movers?|increase\w*|"
                             r"decrease\w*|change\w*|gain\w*|drop\w*|jump\w*)\b", re.I)
# A span of years in a title or a question ("since 1999", "from 2015", "1999-2025", "over 25 years"): growth over it is
# a growth bar (growth per year, /insights/trends), not the change between two years (a waterfall).
GROWTH_SPAN_RE = re.compile(rf"\b(?:since|from|between|after|starting(?:\s+in)?)\s+{_YEAR}\b|\b{_YEAR}\s*(?:-|–|to)\s*"
                            rf"{_YEAR}\b|\bper\s+year\b|\ba\s+year\b|\bannual(?:ly)?\b|\blong[- ]run\b|"
                            r"\bover\s+(?:the\s+)?(?:(?:last|past)\s+)?(?:\d+\s+)?(?:years|decades?|long\s+run)\b", re.I)


def asks_clear(question: Optional[str]) -> bool:
    """'Clear the page', 'reset', 'start over': the only questions that may clear the page."""
    return bool(_CLEAR_RE.search(question or ""))


def show_year(question: Optional[str]) -> Optional[str]:
    """'Show me 2025' -> '2025': a question that only moves the page to another year."""
    m = SHOW_YEAR_RE.match(question or "")
    return m.group(1) if m else None


def years_in(question: Optional[str]) -> set:
    return set(_YEARS_RE.findall(question or ""))


_PARTNER_WORDS = {**{v.lower(): k for k, v in COUNTRY_NAMES.items() if k != "LU"},
                  "the us": "US", "usa": "US", "america": "US", "the uk": "GB", "uk": "GB", "britain": "GB",
                  "great britain": "GB", "holland": "NL"}


def partners_in(question: Optional[str]) -> list[str]:
    """The partner countries a question names, in its order: 'France and Germany' -> ['FR', 'DE']."""
    q = (question or "").lower()
    found = []
    for word, code in _PARTNER_WORDS.items():
        m = re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", q)
        if m:
            found.append((m.start(), code))
    return list(dict.fromkeys(code for _, code in sorted(found)))


def _drop_year(text: Optional[str], year: str) -> Optional[str]:
    """'Export destinations in 2025' -> 'Export destinations': a chart that follows the page's year names none."""
    if not text:
        return text
    out = re.sub(rf"\s*,?\s*\b(?:in|for|of|during)\s+{year}\b", "", text)
    out = re.sub(rf"^\s*{year}\s+", "", out)
    out = re.sub(rf"\s*\(\s*{year}\s*\)", "", out).strip()
    return (out[:1].upper() + out[1:]) if out else text


_SPAN_RE = re.compile(r"\s*,?\s*\b(?:from|between|in|over)?\s*\(?\s*(?:19|20)\d\d\s*(?:–|—|-|to|and)\s*(?:19|20)\d\d\s*\)?",
                      re.I)


def _drop_span(text: Optional[str]) -> Optional[str]:
    """'Products behind the export rise, 1999–2025' -> 'Products behind the export rise': a chart of one year (or
    one year against the one before) that follows the page's year cannot show a span of years."""
    if not text:
        return text
    out = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", _SPAN_RE.sub(" ", text))).strip(" ,")
    return (out[:1].upper() + out[1:]) if out else text


def _respan(text: Optional[str], old: Optional[str], new: str) -> Optional[str]:
    """A title or caption the model wrote for the start it passed ('since 2015') when the chart starts elsewhere
    ('since 1999'): the year follows the chart. On a chart over time 'since YEAR' always says where it starts."""
    if not text:
        return text
    if old and _year(old) != _year(new):
        return re.sub(rf"\b{_year(old)}\b", _year(new), text)
    return re.sub(r"\b(since)\s+(?:19|20)\d\d\b", lambda m: f"{m.group(1)} {_year(new)}", text, flags=re.I)


def months_between(a: str, b: str) -> int:
    """'2021-12' and '2021-01' -> 11; a bare year counts as its January."""
    def n(p: str) -> int:
        p = str(p)
        return int(p[:4]) * 12 + (int(p[5:7]) - 1 if len(p) >= 7 else 0)
    return n(a) - n(b)


def _prev_year(period: str) -> str:
    return str(int(_year(period)) - 1)


PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}")


def pick_facts(text: str, vocab: dict[str, dict], deps: Optional[dict[str, list[str]]] = None) -> dict:
    """The facts a text needs from a vocabulary (with what they are derived from). Unknown names raise."""
    used = PLACEHOLDER_RE.findall(text or "")
    unknown = sorted({u for u in used if u not in vocab})
    if unknown:
        raise ToolProblem(f"unknown placeholder(s) {', '.join('{' + u + '}' for u in unknown)} in {text!r}. "
                          f"Available here: {', '.join('{' + k + '}' for k in vocab)}. Write figures only as "
                          "placeholders, never typed in.")
    out: dict = {}

    def add(name: str) -> None:
        if name in out:
            return
        fact = vocab[name]
        out[name] = fact
        for d in fact.get("of", []) if "op" in fact else []:
            add(d)
    for u in used:
        add(u)
    return out


_UP_VERB = (r"rose|rises?|risen|grew|grows?|grown|increased|increases?|climbed|jumped|gained|up|"
            r"(?:an?\s+)?(?:rise|increase|gain|jump)\s+of")
_DOWN_VERB = (r"fell|falls?|fallen|dropped|drops?|declined|declines?|decreased|decreases?|shrank|slipped|contracted|"
              r"lost|down|(?:an?\s+)?(?:fall|drop|decline|decrease|loss)\s+of")
_SIGNED_AFTER_VERB_RE = re.compile(rf"\b(?P<verb>(?P<up>{_UP_VERB})|(?P<down>{_DOWN_VERB}))(?P<by>\s+by)?\s+"
                                   r"\{(?P<name>[a-z][a-z0-9_]{0,27})\}", re.IGNORECASE)
_UNSIGNED = {"pct_signed": "pct", "eur_signed": "eur", "percent_signed": "percent"}


def plain_signs(obj: dict) -> None:
    """A note or text whose verb says the direction gets its figure without a second sign, in place: 'rose
    {growth}' (pct_signed) shows 'rose 8.6%', never 'rose +8.6%'; 'fell {change}' shows 'fell 17.4%' (the
    figure's absolute value), never 'fell −17.4%'."""
    facts = obj.get("facts")
    if not isinstance(facts, dict):
        return
    for field_ in ("note", "text"):
        text = obj.get(field_)
        if not isinstance(text, str) or "{" not in text:
            continue

        def fix(m: re.Match) -> str:
            name = m.group("name")
            fact = facts.get(name)
            if not isinstance(fact, dict) or (fact.get("format") or "") not in _UNSIGNED and not m.group("down"):
                return m.group(0)
            unsigned = _UNSIGNED.get(fact.get("format") or "", fact.get("format"))
            if m.group("down"):
                new = f"{name[:26]}_abs"
                facts.setdefault(new, {"op": "abs", "of": [name], **({"format": unsigned} if unsigned else {})})
            else:
                new = f"{name[:28]}_u"
                facts.setdefault(new, {**fact, "format": unsigned})
            return f"{m.group('verb')} {{{new}}}"
        obj[field_] = _SIGNED_AFTER_VERB_RE.sub(fix, text)


_TITLE_STOP = {"luxembourg", "luxembourg's", "trade", "goods", "where", "what", "from", "came", "with", "since",
               "the", "and", "into", "this", "that", "page", "chart"}


def subject_words(title: Optional[str]) -> set:
    """The words that say what a title is about: '... where the 2025 export rise came from' -> {2025, export,
    rise}."""
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", (title or "").lower())
    return {w.rstrip("s") if not w.isdigit() else w for w in words
            if (len(w) >= 4 or w.isdigit()) and w not in _TITLE_STOP}


def title_about(title: Optional[str], chart_title: Optional[str]) -> bool:
    """The page title is about that chart: they share at least two words of subject, a year among them or three."""
    shared = subject_words(title) & subject_words(chart_title)
    return len(shared) >= 3 or (len(shared) >= 2 and any(w.isdigit() for w in shared))


PEERS_ASK_RE = re.compile(
    r"\b(?:specific|unique|particular|peculiar|special)\s+to\s+(?:luxembourg|us)\b|\b(?:other|eu|european|neighbou?ring|"
    r"peer)\s+(?:eu\s+)?(?:countries|economies|member\s+states)\b|\bcompared?\s+(?:luxembourg|us|ourselves)\s+"
    r"(?:with|to|against)\b|\bluxembourg\s+(?:against|versus|vs\.?|compared\s+(?:with|to))\s|\b(?:only|just)\s+"
    r"(?:us|luxembourg)\b|\bdo\s+(?:other|the\s+other)\s+countries\b|\b(?:belgium|the\s+netherlands|germany|france)"
    r"(?:'s)?\s+(?:own\s+)?exports\s+to\b", re.IGNORECASE)
PEERS_TOTAL_ONLY = (
    "The other EU countries' trade with that partner could not be read from Eurostat just now: the page has their "
    "TOTAL trade only (all partners together, also by HS2 chapter).")
PEERS_WITH_PARTNER = (
    "Each country's OWN trade with that partner is in the data (Belgium's exports to the United States; a slice "
    "not loaded yet is fetched from Eurostat on first use): compare(what='peers', codes=[the countries], "
    "partner=<the partner the conversation is about, e.g. 'US' after a question on exports to the United States>, "
    "mode='index'), and name the partner in the title, the caption and the reply ('exports to the United States "
    "of Luxembourg, Belgium, the Netherlands and Germany'). With no partner in the conversation, leave partner out: "
    "each country's TOTAL exports, called total exports.")


def asks_peers(question: Optional[str]) -> bool:
    """'Is this specific to Luxembourg? Compare with Belgium, the Netherlands and Germany': Luxembourg against
    other countries (peers), not Luxembourg's trade with those countries (partners)."""
    return bool(PEERS_ASK_RE.search(question or ""))


def level_of(code: Optional[str]) -> int:
    """The product level of a code: 1 HS2, 2 HS4, 3 HS6, 4 CN8 (0 for none)."""
    c = str(code or "")
    return len(c) // 2 if c.isdigit() and len(c) in (2, 4, 6, 8) else 0


def code_key(code: str) -> str:
    """The placeholder prefix of a code: 'DE' -> 'de', '85' -> 'hs85' (a placeholder starts with a letter)."""
    c = str(code).lower()
    return c if c[:1].isalpha() else f"hs{c}"


def need_figure(note: str, what: str) -> None:
    """A mark's note says something with a number: at least one placeholder."""
    if not PLACEHOLDER_RE.search(note or ""):
        typed = [w for w, _, _ in fmt.figures_in(note or "")]
        raise ToolProblem(f"the note of this {what} has no placeholder"
                          + (f" and types figures ({', '.join(typed[:3])}): replace each typed figure by its "
                             "placeholder" if typed else ": put at least one placeholder in it")
                          + " (the tool's description lists them); the page fills the numbers in")


def typed_or_empty(note: str) -> bool:
    """A note with typed figures ('From €766 m to €912 m') or without any placeholder."""
    return bool(fmt.figures_in(note or "")) or not PLACEHOLDER_RE.search(note or "")


CAUSAL_RE = re.compile(r"\b(caus(?:e|ed|es|ing)|led to|leads? to|due to|because of|owing to|result(?:ed|s)? in|"
                       r"resulting in|triggered|drove|driven by|brought about|thanks to)\b", re.IGNORECASE)


def no_causal_words(note: str, what: str) -> None:
    """Events line up with moves; the data cannot say they caused them."""
    plain = re.sub(r"\b(?:proof|evidence|sign) of (?:a |the )?causes?\b|\bnot (?:a |the )?causes?\b|"
                   r"\bcauses? (?:lie|lies|are|is) outside\b", "", note or "", flags=re.IGNORECASE)
    m = CAUSAL_RE.search(plain)
    if m:
        raise ToolProblem(f"the note of this {what} says {m.group(0)!r}: the data shows that dates line up, not "
                          "causes. Rewrite it with 'lines up with', 'came as', 'in the year of' or 'that year'")


_UP = (r"rose|rise|rises|risen|rising|grew|grow|grows|growing|increased|increase|increases|increasing|surged|surge|"
       r"surges|jumped|jump|jumps|climbed|climb|gained|gain|went up|up|higher")
_DOWN = (r"fell|fall|falls|fallen|falling|dropped|drop|drops|declined|decline|declines|declining|decreased|decrease|"
         r"decreases|shrank|shrink|shrunk|slumped|slump|plunged|plunge|lost|went down|down|lower|contracted|slid")
_DIRECTION_RE = re.compile(rf"\b(?P<verb>{_UP}|{_DOWN})\s+(?:(?:by|of|some|about|nearly|almost|around)\s+)*"
                           r"\{(?P<name>[a-z][a-z0-9_]{0,31})\}", re.IGNORECASE)


_FROM_TO_RE = re.compile(rf"\b(?P<verb>{_UP}|{_DOWN})\s+(?:from\s+)?\{{(?P<a>[a-z][a-z0-9_]{{0,31}})\}}\s+to\s+"
                         r"\{(?P<b>[a-z][a-z0-9_]{0,31})\}", re.IGNORECASE)
_AT_CHANGE_RE = re.compile(r"\{[a-z0-9_]+_at_change(?:_abs|_u)?\}")
_YEAR_EARLIER_RE = re.compile(r"\b(?:a|one|the)\s+year\s+(?:earlier|before)|\byear[- ]on[- ]year\b|\bthan\s+in\s+"
                              r"(?:(?:19|20)\d\d|\w+\s+(?:19|20)\d\d)\b|\bon\s+(?:19|20)\d\d\b|\bagainst\s+(?:a\s+year|"
                              r"\w+\s+(?:19|20)\d\d|(?:19|20)\d\d)\b", re.IGNORECASE)
_SPAN_WORDS_RE = re.compile(r"\btrend\b|\bspan\b|\bover\s+the\s+(?:period|years|months)\b|\bsince\s+(?:19|20)\d\d\b",
                            re.IGNORECASE)


def at_change_problem(note: str) -> None:
    """{x_at_change} is one point against the same period a year before. 15 Oct: 'Across the chart's span, exports'
    trend fell {exports_at_change_abs}' and 'moved {exports_at_change} in this month' read it as something else."""
    if not _AT_CHANGE_RE.search(note or ""):
        return
    if _SPAN_WORDS_RE.search(note) or not _YEAR_EARLIER_RE.search(note):
        raise ToolProblem("{<key>_at_change} is this point against the same period a year before, never a trend nor a "
                          "change over the span: say so, e.g. 'Exports were {exports_at}, {exports_at_change} on a year "
                          "earlier.', or leave it out")


PER_YEAR_RE = re.compile(r"\b(?:a|per|each|every)\s+year\b|\bannual(?:ly)?\b|\byearly\b|/\s*year\b", re.IGNORECASE)
_EUR_RE = re.compile(r"([-−+]?)€\s*([\d.,]+)\s*(bn|m|k)?", re.IGNORECASE)


def _parse_eur(written) -> Optional[float]:
    """'€1.51 bn' -> 1.51e9, '€41 m' -> 4.1e7; None when it is no amount."""
    m = _EUR_RE.search(str(written or ""))
    if not m:
        return None
    v = float(m.group(2).replace(",", "")) * {"bn": 1e9, "m": 1e6, "k": 1e3}.get((m.group(3) or "").lower(), 1)
    return -v if m.group(1) in "-−" and m.group(1) else v


def direction_problems(text: str, values: dict) -> list[str]:
    """'fell by {x}' where x shows '+8.6%', 'surged by {y}' where y shows '−69%': the verb and the sign disagree.
    Only signed figures (changes) are checked; a level ('rose to {now}') carries no sign."""
    out = []
    for m in _FROM_TO_RE.finditer(text or ""):   # 'fell {from} to {to}': two levels, the verb must match them
        a, b = _parse_eur(values.get(m.group("a"))), _parse_eur(values.get(m.group("b")))
        if a is None or b is None or a == b:
            continue
        up = re.fullmatch(_UP, m.group("verb"), re.IGNORECASE) is not None
        if up != (b > a):
            out.append(f"{m.group(0)!r} would read '{m.group('verb')} {values.get(m.group('a'))} to "
                       f"{values.get(m.group('b'))}': the verb says the other direction. Write 'went from "
                       f"{{{m.group('a')}}} to {{{m.group('b')}}}' or the right verb")
    for m in _DIRECTION_RE.finditer(text or ""):
        shown = str(values.get(m.group("name")) or "")
        if not shown or shown[0] not in "+-−":
            continue
        up = re.fullmatch(_UP, m.group("verb"), re.IGNORECASE) is not None
        if up == (shown[0] != "+"):
            out.append(f"{m.group(0)!r} would read '{m.group('verb')} ... {shown}': the verb and the sign disagree. "
                       f"Use a neutral verb ('changed by {{{m.group('name')}}}', 'moved {{{m.group('name')}}}') or "
                       "the right direction, and check that the figure is the one you mean")
    return out


_SKILL_RE = re.compile(r"\{skill_(\d+)m\}")
_HORIZON_RE = re.compile(r"\b(\d+|one|three|six|twelve)[- ]months?\b", re.IGNORECASE)
_WORDS = {"one": "1", "three": "3", "six": "6", "twelve": "12"}


def horizon_problem(text: str) -> None:
    """'beats the naive forecast by {skill_3m} at 1 month': the placeholder and the horizon written disagree."""
    for m in _SKILL_RE.finditer(text):
        window = text[max(0, m.start() - 45): m.end() + 45]
        said = {_WORDS.get(h.lower(), h) for h in _HORIZON_RE.findall(window)}
        if said and m.group(1) not in said:
            raise ToolProblem(f"{m.group(0)} is the skill {m.group(1)} months ahead, but the text next to it says "
                              f"{', '.join(sorted(said))} month(s): use {{skill_{sorted(said)[0]}m}} or change the "
                              "words ({skill_1m}, {skill_3m}, {skill_6m}, {skill_12m} exist)")


# --------------------------------------------------------------------------- shares of one product line
def goods_words(name: str) -> str:
    """'Smartphones for wireless networks' -> 'smartphones': the goods of a line, said in a sentence."""
    name = products.heading_words(name or "", 60)   # 'Lifting, handling, ... machinery', not 'Lifting'
    if len(name) > 22:
        head = re.split(r"\s+(?:for|of|with|used|other|not|in|n\.e\.s)\b", name, maxsplit=1)[0].strip(" ,;")
        name = head or name
    return fmt.sentence(name.rstrip("…").strip())


def line_share(share: float, partner: str, goods: str, flow: str) -> dict:
    """A partner's share of one line, with the sentence that gives it the right base."""
    written = fmt.pct(share)
    say = (f"{written} of the {goods} Luxembourg bought came from {partner}" if flow == "IMPORT"
           else f"{written} of the {goods} Luxembourg sold went to {partner}")
    return {"value": share, "written": written, "partner": partner, "goods": goods, "flow": flow, "say": say}


# "87% of imports from China", "86% of its exports", "{sold_share} of exports": a share of ONE line written as a
# share of a whole flow (no goods word between 'of' and the flow)
_WHOLE_FLOW_RE = re.compile(r"\s*(?:of|in)\s+(?:all\s+|total\s+|its\s+|their\s+|the\s+|Luxembourg's\s+|the\s+country's\s+)?"
                            r"(?:total\s+)?(imports?|exports?|purchases|sales|trade|goods\s+(?:bought|sold))\b", re.I)
_CHANGE_RE = re.compile(r"\s*(?:of|in)\s+(?:the\s+)?(?:total\s+|whole\s+|overall\s+|export\s+|import\s+|year's\s+)?"
                        r"(?:change|rise|increase|growth|jump|fall|drop|decline|decrease|gain|step|move)\b", re.I)


def _parse_pct(written: Optional[str]) -> Optional[float]:
    m = fmt._PCT_RE.search(str(written or ""))
    return float(m.group(2).replace(",", ".")) / 100 if m else None


def share_base_problems(text: str) -> list[str]:
    """Placeholders of a line's share written against the wrong base, in a text for the page."""
    out = []
    for name in ("bought_share", "sold_share", "partner_share"):
        for m in re.finditer(r"\{" + name + r"\}", text or ""):
            if _WHOLE_FLOW_RE.match(text[m.end():]):
                out.append(f"{{{name}}} is a share of the product line (of the line's {'imports' if name == 'bought_share' else 'exports'}), "
                           f"not of everything Luxembourg {'bought from' if name == 'bought_share' else 'sold to'} that "
                           f"partner: write it as \"{{{name}}} of the line's {'imports' if name == 'bought_share' else 'exports'}\" "
                           "or name the goods (\"of the smartphones Luxembourg sold\")")
    for m in re.finditer(r"\{line_share\}", text or ""):
        if not _CHANGE_RE.match(text[m.end():]):
            out.append("{line_share} is the line's share of the CHANGE between the two years: write it as "
                       "\"{line_share} of the rise\" (or of the change)")
    return out


def misattributed_shares(text: str, shares: list[dict]) -> list[dict]:
    """Shares of one line written in a reply as shares of a whole flow ('making up 87% of imports from China'):
    [{"written", "say", "sentence"}]."""
    out = []
    if not shares:
        return out
    for m in fmt._PCT_RE.finditer(text or ""):
        value = float(m.group(2).replace(",", ".")) / 100
        hit = next((s for s in shares if fmt.written_like(value, "pct", s["value"])), None)
        if hit and _WHOLE_FLOW_RE.match(text[m.end():]):
            start = max(text.rfind(". ", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
            end = text.find(". ", m.end())
            out.append({"written": m.group(0), "say": hit["say"],
                        "sentence": text[start:(end + 1 if end >= 0 else len(text))].strip()})
    return out


_UNSPECIFIED_RE = re.compile(r"\b(confidential\w*|not[ -]specified|unspecified|unallocated|not allocated|undisclosed|"
                             r"secret|unknown partners?)\b", re.IGNORECASE)


def unspecified_problems(text: str, unspecified: list[tuple]) -> list[str]:
    """Sentences that call a figure 'confidential' or 'not specified' when it is not the trade with no partner
    specified (QV, QW, QS) read in this turn: usually the ranking's other partners together."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        words = [m.span() for m in _UNSPECIFIED_RE.finditer(sentence)]
        if not words:
            continue
        # the figures written next to the label (within a few words of it), as (kind, value)
        near = []
        for kind, rx in (("eur", fmt._EUR_RE), ("pct", fmt._PCT_RE)):
            for m in rx.finditer(sentence):
                if any(m.start() - 60 <= b and a <= m.end() + 60 for a, b in words):
                    value = float(m.group(2).replace(",", "."))
                    near.append((kind, value * fmt._SCALE[(m.group(3) or "").lower() or None] if kind == "eur"
                                 else value / 100))
        if any(not any(k == kind and v is not None and fmt.written_like(value, kind, v) for k, v in unspecified)
               for kind, value in near):
            out.append(sentence.strip())
    return out


MAX_HORIZON = 36   # months: /insights/forecast refuses more (400)
TESTED_UP_TO = 12  # months: the rolling backtest tests 1, 3, 6 and 12 months ahead


def line_words(label: Optional[str]) -> str:
    """'the smartphone line (CN 8517 13 00)' -> 'the smartphone line'."""
    out = re.sub(r"\s*\([^)]*\)", "", label or "").strip()
    return out or "the line"


def scenarios_words(scenarios: list[dict], year: str, central: Optional[float]) -> Optional[str]:
    """The two scenarios said as the line's level, and where the model's central figure stands against them:
    'If the smartphone line, which started in September 2025, stays at its recent level of about €51 m a month,
    exports in 2026 would be €17 bn; if it stops, €16.7 bn. The model's central €17.3 bn is above both: it
    assumes the line's recent rise goes on.' None without both scenarios for that year."""
    by_case = {}
    for sc in scenarios or []:
        row = next((r for r in sc.get("rows") or [] if str(r.get("period")) == str(year)), None)
        if row and row.get("value") is not None and sc.get("case") in ("stops", "continues"):
            by_case[sc["case"]] = (sc, row["value"])
    if len(by_case) < 2:
        return None
    sc = by_case["continues"][0]
    line = line_words(sc.get("line_label") or sc.get("label"))
    since = f", which started in {fmt.period_words(str(sc['since'])[:7])}," if sc.get("since") else ""
    level = (f" at its recent level of about {fmt.eur(sc['recent_extra'])} a month"
             if sc.get("recent_extra") else " at its recent level")
    cont, stop = by_case["continues"][1], by_case["stops"][1]
    out = (f"If {line}{since} stays{level}, the forecast for {year} is {fmt.eur(cont)}; if it stops, "
           f"{fmt.eur(stop)}.")
    if central is not None:
        if central > max(cont, stop):
            out += (f" The model's central {fmt.eur(central)} is above both: it assumes the line's recent rise "
                    "goes on.")
        elif central < min(cont, stop):
            out += f" The model's central {fmt.eur(central)} is below both: it assumes the line fades."
        else:
            out += f" The model's central {fmt.eur(central)} lies between the two."
    return out


_METHOD_NAMES_RE = re.compile(r"\s*(?:,\s*)?\b(?:the\s+)?(?:average\s+of\s+)?(?:Holt-Winters(?:\s+exponential)?"
                              r"(?:\s+smoothing)?(?:\s+and\s+gradient\s+boosting)?(?:\s+trained\s+on\s+every\s+"
                              r"HS2\s+chapter)?)", re.IGNORECASE)


def plain_outlook(text: str, word: str, year: str, scenarios: bool) -> str:
    """The 'What comes next' text leads with the figure and its interval in plain words; the method is one short
    plain clause (its technical names are in the model card); the scenarios are said as the line's level."""
    sents = [x for x in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if x]
    if scenarios:   # the model's own scenario sentences go: the plain one below replaces them
        sents = [x for x in sents if not re.search(r"\{(?:stops|continues)\}|scenario|one-off|\bassum\w*\b.*\b(?:rise|continu|goes on)",
                                                   x, re.IGNORECASE)]
    sents = [x.replace("{method}", "an average of two standard forecasting methods (named in the model card)")
             for x in sents]
    sents = [re.sub(r"\bthe\s+seasonal\s+naive\s+forecast\b", "repeating the same month a year earlier", x,
                    flags=re.IGNORECASE) for x in sents]
    if not sents or "{value}" not in sents[0]:
        sents = [x for x in sents if "{value}" not in x]
        sents.insert(0, f"{word.capitalize()} in {year}: {{value}}, likely between {{low}} and {{high}} (the "
                        "{interval} interval).")
    if scenarios:
        sents.insert(1, "If the smartphone line, which started in {line_since}, stays at its recent level of about "
                        "{line_level} a month: {continues}; if it stops: {stops}. The central figure assumes its "
                        "recent rise goes on.")
    return " ".join(sents)


def tidy_outlook(text: str) -> str:
    """A 'What comes next' text in sentence case and without '..': {method} reads 'the average of Holt-Winters
    ...' (lower case), so a sentence never starts with it, and {summary} ends with its own period."""
    t = re.sub(r"\{summary\}\s*\.", "{summary}", text or "")
    if "{method}" in t:  # {summary} starts with the method's name: "The forecast uses X. X beats ..." repeats it
        t = re.sub(r"\s*\{summary\}", "", t)
    t = re.sub(r"(^|[.!?]\s+)\{method\}\s*([.;:])", lambda m: f"{m.group(1)}The forecast uses {{method}}{m.group(2)}", t)
    t = re.sub(r"(^|[.!?]\s+)\{method\}(?=,)", lambda m: f"{m.group(1)}The method, {{method}}", t)
    t = re.sub(r"(^|[.!?]\s+)\{method\}", lambda m: f"{m.group(1)}The method, {{method}},", t)
    t = re.sub(r"\.\s*\.(?!\.)", ".", t)
    t = t.strip()
    return t[:1].upper() + t[1:] if t[:1].isalpha() else t


def _inline(name: str) -> str:
    """'The Theta method' -> 'the Theta method', 'Gradient boosting' -> 'gradient boosting'; 'Holt-Winters' stays."""
    return name[:1].lower() + name[1:] if name.startswith(("The ", "Gradient")) else name


def tested_span(bt: dict) -> Optional[str]:
    """'the 20 quarter ends from June 2021 to March 2026': where the rolling backtest was run."""
    if not bt.get("first_origin"):
        return None
    n = f"the {bt['origins']} " if bt.get("origins") else "the "
    return f"{n}quarter ends from {fmt.period_words(bt['first_origin'])} to {fmt.period_words(bt['last_origin'])}"


def two_tests(bt: dict) -> Optional[str]:
    """The rolling backtest and the single last full year, in two sentences: both are true, and said apart
    they do not read as a contradiction ("beats the naive forecast" / "did not beat it on 2025")."""
    out = []
    s1, s12 = bt.get("skill_h1"), bt.get("skill_h12")
    if tested_span(bt) and s1 is not None and s12 is not None:
        out.append(f"Tested at {tested_span(bt)}, it beats the seasonal naive forecast (the same month a year earlier) "
                   f"by {fmt.pct(s1)} one month ahead and "
                   + (f"by {fmt.pct(s12)} twelve months ahead." if s12 > 0 else "not at all twelve months ahead."))
    if bt.get("period") and bt.get("mape") is not None:
        year = str(bt["period"])
        before = str(int(year) - 1) if year.isdigit() else "the year before"
        line = (f"Tested on the single year {year}, forecast from the December before, it was off by "
                f"{fmt.pct(bt['mape'])}")
        if bt.get("naive_mape") is not None:
            line += f", against {fmt.pct(bt['naive_mape'])} for repeating {before}"
            if bt["mape"] > bt["naive_mape"]:
                line += ": on that one year it did not beat the naive guess"
        out.append(line + ".")
    return " ".join(out) or None


def same_months(rows: Any) -> Optional[dict]:
    """A monthly series whose last year is not complete: that year so far against the same months a year before
    ('January to July 2026: €76.8 m, against €28.8 m in January to July 2025, +166.4%'), the only fair comparison of
    a part of a year. None when the last year is complete or the year before lacks one of those months."""
    rows = rows.get("rows") if isinstance(rows, dict) else rows
    vals = {str(r.get("period")): r.get("value") for r in rows or [] if r.get("value") is not None}
    months = sorted(p for p in vals if re.fullmatch(r"\d{4}-\d{2}", p))
    if not months:
        return None
    year, last = months[-1][:4], int(months[-1][5:])
    if last == 12:
        return None
    prev = str(int(year) - 1)
    keys = [f"{{}}-{m:02d}" for m in range(1, last + 1)]
    if any(k.format(year) not in vals or k.format(prev) not in vals for k in keys):
        return None
    now, before = sum(vals[k.format(year)] for k in keys), sum(vals[k.format(prev)] for k in keys)
    span = fmt.period_words(f"{year}-01").split()[0] + (f" to {fmt.period_words(months[-1]).split()[0]}" if last > 1 else "")
    return {"months": span, f"{year}_so_far": fmt.eur(now), f"{prev}_same_months": fmt.eur(before),
            "change": fmt.pct(now / before - 1, signed=True) if before else None,
            "say": f"{span} {year}: {fmt.eur(now)}, against {fmt.eur(before)} in {span} {prev}"}


class Toolbox:
    """The tools of one specialist, drawing under its own name (its pen)."""

    def __init__(self, api_url: str, agent: str, api: Optional[InsightsClient] = None,
                 view: Optional[ViewClient] = None, session: Optional[str] = None):
        self.agent = agent
        self.api = api or InsightsClient(api_url)
        self.view = view or ViewClient(api_url, agent, session=session)
        self.turn = Turn(agent)
        self._meta: Optional[dict] = None
        self._cache: dict[str, Any] = {}
        self._analysis: dict[str, dict] = {}   # series key -> params of the last /trends/analyze call
        # The spans analysed in this turn by any specialist of the conversation (Conversation shares one dict): a
        # second analysis of the same series takes the first one's span, so every answer gives the same trend's
        # change (one measure of direction for a span; 15 Oct: -23.6% to July 2026, then -27.6% to June)
        self.shared_spans: Optional[dict] = None
        self._forecast: dict[str, dict] = {}   # series key -> params of the last /insights/forecast call
        self._fc_redirect: dict[str, tuple] = {}  # a line with no tested forecast -> (partner, product) used instead
        self._hint: Optional[list] = None       # placeholders of the tool being called, for its error
        self.asked: dict = {}                   # the period the question asks for (asked_span)
        self.question: str = ""                 # the user's message being answered
        self.others: list[str] = []             # the other agents of the plan, answering their own parts
        self._names: dict[str, str] = {}        # product code -> short name (drill_down, find_codes)
        self._headline_tries = 0

    # ---- plumbing
    def begin(self, question: str = "") -> Turn:
        """A new question: a fresh record and a fresh cache (the data may have moved)."""
        self.turn = Turn(self.agent)
        self._cache.clear()
        self.asked = asked_span(question)
        self.question = question or ""
        self.others = []          # the other agents of the plan (run_specialist sets them)
        self._headline_tries = 0
        return self.turn

    # ---- the data's coverage, and the period the user asked for
    def coverage(self) -> dict:
        """The years and months the data holds, from /insights/meta (1999 to the last full year)."""
        periods = (self.meta() or {}).get("periods") or {}
        years, months = periods.get("A") or [], periods.get("M") or []
        return {"first_year": years[0] if years else FIRST_YEAR, "last_year": years[-1] if years else None,
                "first_month": months[0] if months else f"{FIRST_YEAR}-01", "last_month": months[-1] if months else None}

    def first_year(self, *codes: Optional[str]) -> str:
        """Where a long-run view starts by default: the first year of the data (1999), or 2015 for HS6/CN8 lines."""
        if any(c and str(c).isdigit() and len(str(c)) >= 6 for c in codes):
            return DETAIL_FROM
        return str(self.coverage()["first_year"])[:4]

    def _start(self, given: Optional[str], *codes: Optional[str], monthly: bool = False) -> str:
        """The start of a long-run view: what the user asked for ('since 1999') wins over what the model passed,
        which wins over the first year of the data. Never before the data (1999)."""
        if self.asked.get("start"):
            year = self.asked["start"]
        elif given:
            if monthly and len(str(given)) == 7:
                return str(given)
            year = _year(str(given))
        else:
            year = self.first_year(*codes)
        year = max(year, FIRST_YEAR)
        return f"{year}-01" if monthly else year

    def _end(self, given: Optional[str], default: Optional[str] = None) -> Optional[str]:
        """The end of a view: the user's 'to 2020' wins over the model's, else the model's, else ``default``."""
        return self.asked.get("end") or given or default

    def _base(self, given: Optional[str], start: str) -> str:
        """The year at 100 on an index: the user's ('2015 = 100'), else the start (the model's base only when the
        user named no period)."""
        if self.asked.get("base"):
            return self.asked["base"]
        if given and not self.asked.get("start") and given >= start:
            return _year(given)
        return _year(start)

    def tools(self, names: Iterable[str]) -> list[LuxTool]:
        out = []
        for name in names:
            fn = getattr(self, name)
            spec = fn._tool
            out.append(LuxTool(fn, name, spec["description"], spec["properties"], spec["required"],
                               on_result=lambda result, n=name: self._after(n, result)))
        return out

    ACT_KINDS = {"add_thread": "thread", "add_annotation": "annotation", "highlight": "highlight",
                 "add_events": "event", "add_forecast_band": "forecast", "add_trend_breaks": "trend_break",
                 "set_title": "title", "write_headline": "headline", "add_trend_line": "trend"}

    def _act_kinds(self, name: str, result: dict) -> set:
        """What an act tool drew (or found already there): its mark kind, or the type of its chart."""
        if name in self.ACT_KINDS:
            return {self.ACT_KINDS[name]}
        if name in ("show_panel", "compare") and result.get("panel") and self.turn.spec:
            p = page.panel(self.turn.spec, result["panel"])
            return {p["type"]} if p else set()
        return set()

    def _after(self, name: str, result: Any) -> None:
        self._hint = None
        self.turn.see(result)
        if isinstance(result, dict) and result.get("ok"):
            kinds = self._act_kinds(name, result)
            if result.get("changed") is False:
                self.turn.unchanged += [k for k in kinds if k not in self.turn.unchanged]
            else:
                self.turn.drawn_kinds += [k for k in kinds if k not in self.turn.drawn_kinds]
        if isinstance(result, dict) and result.get("ok"):
            self.turn.done.append(name)
        elif isinstance(result, dict) and result.get("ok") is False:
            self.turn.refused.append(name)

    def get(self, endpoint: str, **params) -> Any:
        """GET an /insights or /trends endpoint (cached for the question), remembering the call."""
        params = {k: v for k, v in params.items() if v is not None}
        for k in ("product", "parent"):
            for c in products.product_codes(str(params.get(k) or "").split(",")):
                if c not in self.turn.products:
                    self.turn.products.append(c)
        url = endpoint + "?" + json.dumps(params, sort_keys=True)
        if url not in self._cache:
            data, source = self.api.get(endpoint, **params)
            self._cache[url] = data
            if source not in self.turn.sources:
                self.turn.sources.append(source)
        return self._cache[url]

    def meta(self) -> Optional[dict]:
        if self._meta is None:
            try:
                self._meta, _ = self.api.meta()
            except Exception as exc:  # the validator falls back to its default dimensions
                log.warning("could not read /insights/meta: %s", exc)
                self._meta = {}
        return self._meta or None

    def spec(self) -> dict:
        return self.view.spec()

    def context(self, spec: Optional[dict] = None) -> dict:
        return contract().validate.context_of(spec or self.spec())

    # ---- facts: what the page will show
    def resolve_facts(self, facts: Optional[dict], queries: dict[str, dict], context: dict) -> tuple[dict, list[str]]:
        """{name: value as the page writes it} for these facts, and the errors of the ones that will not resolve."""
        validate = contract().validate
        values: dict[str, Any] = {}
        raw: dict[str, Any] = {}
        errors: list[str] = []
        facts = facts or {}

        def resolve(name: str, seen: tuple = ()) -> Any:
            if name in raw:
                return raw[name]
            f = facts.get(name)
            if f is None or name in seen:
                raise KeyError(f"no fact {name!r}")
            if "op" in f:
                args = [resolve(a, seen + (name,)) for a in f["of"]]
                a = args[0]
                b = args[1] if len(args) > 1 else None
                op = f["op"]
                if op == "abs":
                    v = abs(a)
                elif op == "sum":
                    v = a + b
                elif op == "diff":
                    v = a - b
                elif op == "ratio":
                    v = a / b if b else None
                else:  # growth
                    v = a / b - 1 if b else None
            else:
                if "query" in f:
                    q = queries.get(f["query"])
                    if q is None:
                        raise KeyError(f"no query {f['query']!r}")
                    endpoint, params = q["endpoint"], q.get("params") or {}
                else:
                    endpoint, params = f["endpoint"], f.get("params") or {}
                data = self.get(endpoint, **validate.fill_params(params, context))
                v = get_path(data, f["path"])
            raw[name] = v
            return v

        for name, f in facts.items():
            try:
                v = resolve(name)
                values[name] = fmt.value(v, f.get("format"))
            except (KeyError, IndexError, TypeError, ZeroDivisionError) as exc:
                where = f.get("path") or f.get("op")
                errors.append(f"fact {{{name}}} ({where}) will not show: {exc}")
            except InsightsError as exc:
                errors.append(f"fact {{{name}}}: the API answered {exc.status} ({exc.detail})")
        return values, errors

    def _check_facts(self, ops: list[dict], spec: dict) -> tuple[dict, list[str]]:
        """Resolve the facts of every panel and mark in a batch, as the page will.

        Returns ({what: {fact: value as shown}}, errors); helper facts (rest_now, from_in, ...) that the
        text does not show are left out."""
        ctx = self.context(spec)
        values, errors = {}, []
        marks = {m["id"]: m for m in spec.get("marks", [])}
        for op in ops:
            if op["op"] == "upsert_panel":
                p = op["panel"]
                qs = {q["key"]: q for q in p.get("queries", [])}
                v, e = self.resolve_facts(p.get("facts"), qs, ctx)
                what, text = f"panel {p['id']}", p.get("text") or ""
            elif op["op"] == "add_mark":
                m = op["mark"]
                v, e = self.resolve_facts(m.get("facts"), {"self": m["query"]} if m.get("query") else {}, ctx)
                what = f"{m['kind']} {(m.get('params') or {}).get('label') or (m.get('params') or {}).get('at') or ''}"
                text = m.get("note") or ""
            elif op["op"] == "update_mark" and ("facts" in op["set"] or "note" in op["set"]):
                m = {**marks.get(op["id"], {}), **op["set"]}
                v, e = self.resolve_facts(m.get("facts"), {"self": m["query"]} if m.get("query") else {}, ctx)
                what, text = f"mark {op['id']}", m.get("note") or ""
            else:
                continue
            e = e + direction_problems(text, v)
            shown = set(PLACEHOLDER_RE.findall(text))
            v = {k: x for k, x in v.items() if k in shown}
            if v:
                values[what.strip()] = v
            errors += e
        return values, errors

    def pick(self, text: str, vocab: dict) -> dict:
        """pick_facts, remembering the vocabulary so a refused batch can say which placeholders exist."""
        self._hint = sorted(k for k in vocab if not k.startswith(("rest_", "from_")))
        return pick_facts(text, vocab)

    # ---- sending
    def commit(self, build: Callable[[dict], tuple[list, list]], message: str, check_facts: bool = True) -> dict:
        try:
            out = self._commit(build, message, check_facts)
        finally:
            hint, self._hint = getattr(self, "_hint", None), None
        if not out.get("ok") and hint:
            out["placeholders_you_can_use"] = ["{" + h + "}" for h in hint]
        return out

    def _commit(self, build: Callable[[dict], tuple[list, list]], message: str, check_facts: bool = True) -> dict:
        """Read the page, build ops against it, check them, send them; rebuild on a 409 (three times at most).

        ``build(spec)`` returns (ops, items) where items are {"tmp" or "id", "what"} for each mark.
        """
        for attempt in range(4):   # specialists working at once may each move the page
            spec = self.spec()
            ops, items = build(spec)
            renamed = self._named_right(ops, spec)
            if not ops:
                marks = self._numbered(spec, items, {})
                self.turn.spec = self.turn.spec or spec
                self.turn.marks += [m["id"] for m in marks if m["id"] not in self.turn.marks]
                return {"ok": True, "changed": False, "note": "nothing to change: the page already shows it",
                        "marks": marks}
            env = self.view.envelope(ops, message[:4000], base_version=spec["version"])
            problems = self.view.validate(env)
            if problems:
                return {"ok": False, "errors": problems[:8]}
            errs = contract().validate_ops(env, spec, self.meta())
            if errs:
                return {"ok": False, "errors": [f"{e['path']}: {e['code']}: {e['message']}" for e in errs[:8]]}
            figures: dict = {}
            if check_facts:
                figures, fact_errors = self._check_facts(ops, spec)
                if fact_errors:
                    return {"ok": False, "errors": fact_errors[:8],
                            "hint": "nothing was drawn; fix or drop these figures and call again"}
            signed = json.dumps(ops, sort_keys=True)
            for op in ops:   # no double sign after a verb of direction in a note or a text ('rose +8.6%')
                for obj in (op.get("mark"), op.get("panel"), op.get("set")):
                    if isinstance(obj, dict):
                        plain_signs(obj)
            if json.dumps(ops, sort_keys=True) != signed:
                env = self.view.envelope(ops, message[:4000], base_version=spec["version"])
                if self.view.validate(env) or contract().validate_ops(env, spec, self.meta()):
                    ops = json.loads(signed)   # the page would not take it: the signs stay as they were
            try:
                result = self.view.send(ops, message=message[:4000], base_version=spec["version"],
                                        run_id=env["agent"]["run_id"])
            except ViewError as exc:
                if exc.status == 409 and attempt < 3:
                    continue  # someone else changed the page: build again on the new one
                return {"ok": False, "status": exc.status,
                        "errors": [f"{e.get('path')}: {e.get('code')}: {e.get('message')}" for e in exc.errors][:8]
                        or [str(exc)]}
            after = result.get("spec") or spec
            self.turn.version, self.turn.spec = result.get("version"), after
            marks = self._numbered(after, items, result.get("assigned") or {})
            for m in marks:
                if m.get("id") and m["id"] not in self.turn.marks:
                    self.turn.marks.append(m["id"])
            for op in ops:
                if op["op"] == "upsert_panel" and op["panel"]["id"] not in self.turn.panels:
                    self.turn.panels.append(op["panel"]["id"])
            self.turn.actions.append(message)
            out = {"ok": True, "version": result.get("version"), "marks": marks}
            if renamed:
                out["names"] = ("the page names these goods by their short name: " + "; ".join(
                    f"'{r['phrase']}' became '{r['name']}'" for r in renamed) + ". An HS2 chapter's official title "
                    "is not what it holds: use the same names in your reply")
            if figures:
                out["figures_on_page"] = figures
            if result.get("warnings"):
                out["warnings"] = result["warnings"][:4]
            gone = [r for r in result.get("replaced") or [] if r.get("id") not in self.turn.panels]
            if gone:   # the page keeps a few agent charts: the oldest not kept made room for this one
                self.turn.replaced += [r for r in gone if r["id"] not in {x["id"] for x in self.turn.replaced}]
                out["replaced"] = [{"chart": r.get("title"), "type": r.get("type")} for r in gone]
                out["note_on_replaced"] = ("the reply says which chart this one took the place of for you: never "
                                           "write about replacing charts yourself")
                self.refresh_title([r.get("title") for r in gone])
            return out
        return {"ok": False, "errors": ["the page kept changing; try again"]}

    def refresh_title(self, gone_titles: list) -> Optional[dict]:
        """When a chart the page title was about is gone (replaced, removed), the title goes back to the page's
        own ("…where the 2025 export rise came from" never stays above a page without that waterfall). Not when
        this turn set the title, nor when another chart on the page still has that subject."""
        if "set_title" in self.turn.done:
            return None
        spec = self.turn.spec
        try:
            base = contract().load_baseline()["title"]
        except Exception:  # no baseline to go back to
            return None
        title = (spec or {}).get("title")
        if not title or title == base or not any(title_about(title, t) for t in gone_titles if t):
            return None
        if any(title_about(title, p.get("title")) for p in (spec or {}).get("panels", [])
               if p.get("type") != "text"):
            return None
        log.info("%s: the title %r was about a chart that is gone: back to %r", self.agent, title, base)
        return self.commit(lambda s_: ([self.view.set_title(base)] if s_.get("title") != base else [], []),
                           "The title went back to the page's own: the chart it was about is gone.",
                           check_facts=False)

    @staticmethod
    def _panel_codes(p: Optional[dict]) -> list[str]:
        """The product codes a panel draws (its queries' product and parent)."""
        vals = []
        for q in (p or {}).get("queries", []):
            prm = q.get("params") or {}
            vals += str(prm.get("product") or "").split(",") + str(prm.get("parent") or "").split(",")
        return products.product_codes(vals)

    def _named_right(self, ops: list[dict], spec: dict) -> list[dict]:
        """Every text a batch puts on the page (titles, subtitles, notes, labels, the headline) names its goods
        right, in code: a chapter's official-title words that are not about the codes drawn ('nuclear reactors'
        on chapter 84) become the chapter's short name ('machinery (HS 84)'). A chart or a mark of goods with no
        recorded trade (turn.no_trade), or under their name, is refused. Returns what was renamed."""
        no_codes = {c for n in self.turn.no_trade for c in n["codes"]}
        no_goods = {n["goods"] for n in self.turn.no_trade}
        new_panels = {op["panel"]["id"]: op["panel"] for op in ops if op.get("op") == "upsert_panel"}
        marks = {m["id"]: m for m in spec.get("marks", [])}
        renamed: list[dict] = []
        for op in ops:
            kind, texts, codes = op.get("op"), [], []
            if kind == "upsert_panel":
                p = op["panel"]
                codes = self._panel_codes(p)
                texts = [(p, "title"), (p, "subtitle"), (p, "text")]
            elif kind in ("add_mark", "update_mark"):
                m = op.get("mark") if kind == "add_mark" else {**marks.get(op.get("id"), {}), **(op.get("set") or {})}
                target = (m or {}).get("target")
                prm = (m or {}).get("params") or {}
                codes = self._panel_codes(new_panels.get(target) or page.panel(spec, target)) + \
                    products.product_codes(list(prm.get("codes") or []) + [prm.get("product")])
                d = op["mark"] if kind == "add_mark" else (op.get("set") or {})
                texts = [(d, "note"), (d.get("params") or {}, "label")]
            elif kind == "set_title":
                texts = [(op, "title")]
            bad = [c for c in codes if c in no_codes]
            if bad:
                raise ToolProblem(next(n["sentence"] for n in self.turn.no_trade if set(bad) & set(n["codes"]))
                                  + " Draw nothing for these goods: say so plainly in the reply.")
            codes = codes or list(self.turn.products)
            for d, k in texts:
                text = d.get(k)
                if not isinstance(text, str) or not text:
                    continue
                for sp in products.specific_in(text):
                    if sp["goods"] in no_goods and not products.misnamed(text, codes):
                        raise ToolProblem(next(n["sentence"] for n in self.turn.no_trade if n["goods"] == sp["goods"])
                                          + f" Nothing on the page may be named after them ({text!r}): say so "
                                            "plainly in the reply instead.")
                found = products.misnamed(text, codes)
                if found:
                    d[k] = products.rename(text, codes)
                    for f in found:
                        r = {"phrase": f["phrase"], "name": f["name"]}
                        if r not in renamed:
                            renamed.append(r)
                        if r not in self.turn.renamed:
                            self.turn.renamed.append(r)
        return renamed

    def _numbered(self, spec: dict, items: list, assigned: dict) -> list[dict]:
        out = []
        for it in items:
            mid = it.get("id") or (assigned.get(it.get("tmp")) or {}).get("id")
            if not mid:
                continue
            nums = page.page_numbers(spec, [mid])
            out.append({"number": nums[0] if nums else None, "id": mid, "what": it.get("what"),
                        **({"already_there": True} if it.get("existing") else {})})
        return out

    # ---- helpers for builders
    def _mark_op(self, spec: dict, kind: str, target: str, note: str, params: dict, *, key: Iterable[str],
                 series: Optional[str] = None, thread: Optional[str] = None, query_: Optional[dict] = None,
                 facts: Optional[dict] = None, period: Optional[str] = None, what: str = "",
                 tmp: Optional[str] = None) -> tuple[Optional[dict], dict]:
        """add_mark, or update_mark when this agent already drew the same mark (no duplicates)."""
        same = next((m for m in spec.get("marks", []) if m.get("agent") == self.agent and m["kind"] == kind
                     and m["target"] == target and (m.get("series") or None) == (series or None)
                     and all((m.get("params") or {}).get(k) == params.get(k) for k in key)), None)
        sources = [{"url": u} for u in self.turn.sources[-8:]]
        if same:
            changes = {}
            if same.get("note") != note:
                changes["note"] = note
            if (same.get("facts") or None) != (facts or None) and facts is not None:
                changes["facts"] = facts
            if same.get("params") != params:
                changes["params"] = params
            if query_ is not None and same.get("query") != query_:
                changes["query"] = query_
            if period and same.get("period") != period:   # the same codes for another year: the mark moves with it
                changes["period"] = period
            item = {"id": same["id"], "what": what, "existing": not changes}
            return (self.view.update_mark(same["id"], **changes) if changes else None), item
        tmp = tmp or f"t{uuid.uuid4().hex[:6]}"
        op = self.view.add_mark(kind, target, note, params, series=series, thread=thread, query=query_, facts=facts,
                                period=period, sources=sources or None, tmp_id=tmp)
        return op, {"tmp": tmp, "what": what}

    def _panel_or_problem(self, spec: dict, target: str, kinds: Iterable[str]) -> dict:
        p = page.panel(spec, target)
        if p is None:
            ids = ", ".join(f"{q['id']} ({q['type']})" for q in spec.get("panels", [])) or "none, the page is empty"
            if target in page.BASELINE_PANELS:
                raise ToolProblem(
                    f"{target!r} is not on the page: the user removed it. Draw a chart for this first with show_panel "
                    "(the partners of a year: a butterfly by partner or a flowmap; a series over time: a line), then "
                    f"put the mark on that chart, or leave the mark out. Never mention the missing chart in the "
                    f"reply. Panels now: {ids}")
            raise ToolProblem(f"there is no panel {target!r} on the page. Panels: {ids}")
        if p["type"] not in kinds:
            raise ToolProblem(f"panel {target!r} is a {p['type']}; this mark goes on: {', '.join(sorted(kinds))}")
        return p

    @staticmethod
    def _can_upsert(spec: dict, pid: str) -> bool:
        p = page.panel(spec, pid)
        return not (p and p.get("pinned"))

    # ======================================================================= read tools
    @tool("Read the page as it is now: title, context (year, goods), panels with their queries, and every "
          "numbered mark with who drew it. Call it before you refer to or change what is there.")
    def read_page(self) -> str:
        spec = self.spec()
        return page.summary(spec, me=self.agent)

    @tool("Find partner or product codes by name ('Belgium', 'smartphones', 'pharmaceuticals', 'nuclear'). ALWAYS "
          "use it for goods the user names in words before you read or draw them: it finds the heading that is "
          "really about them (nuclear -> HS 8401, not chapter 84, whose official title starts 'Nuclear reactors' "
          "but which is machinery), checks that Luxembourg traded them, and gives each code the name to use.",
          {"words": S("the name or words to look for"),
           "kind": S("partner or product", enum=["partner", "product"]),
           "flow": S("products: the flow to check (default both)", enum=["EXPORT", "IMPORT"]),
           "period": S("products: the year to check (default the last full year)")}, ["words", "kind"])
    def find_codes(self, words: str, kind: str, flow: Optional[str] = None, period: Optional[str] = None) -> dict:
        if kind == "product":
            return self.find_products(words, flow=flow, period=period)
        words_l = [w for w in re.findall(r"[a-z0-9]+", words.lower()) if len(w) > 2]
        data = self.get("/insights/ranking", by="partner", period="2025", flow="IMPORT", max_items=300, threshold=0)
        hits = []
        for item in data.get("items") or []:
            name = (item.get("name") or "").lower()
            if all(w.rstrip("s") in name for w in words_l):
                hits.append({"code": item["code"], "name": fmt.short(item.get("name") or "", 70)})
        if not hits and words.strip().upper() and CODE_RE.match(words.strip()):
            hits = [{"code": words.strip().upper(), "name": "(as given)"}]
        return {"matches": hits[:10]}

    # ---- goods named in words (products.py): the heading really about them, and whether it was traded
    def last_year(self) -> str:
        try:
            return str(self.coverage().get("last_year") or "2025")[:4]
        except Exception:  # no meta: the last full year of the data when this was written
            return "2025"

    def traded(self, code: str, flow: str, year: str) -> Optional[float]:
        """The value of one code in one flow and year, or None when there is no recorded trade (absent or zero).
        Read straight from the API: a look-up, not a figure the agent reads about the goods."""
        if str(flow).upper() == "BOTH":   # bought or sold
            got = [v for v in (self.traded(code, "IMPORT", year), self.traded(code, "EXPORT", year)) if v]
            return sum(got) if got else None
        try:
            rows, _ = self.api.get("/insights/series", flow=flow, freq="A", start=year, end=year, product=code)
        except InsightsError:
            return None
        rows = rows.get("rows") if isinstance(rows, dict) else rows
        v = next((r.get("value") for r in rows or [] if str(r.get("period")) == year), None)
        return v if v else None

    def _codelist(self, words: list[str]) -> list[dict]:
        """Codes whose name holds every word: the product codelist (traded or not) when the API has it, else the
        codes traded in the last year."""
        if not words:
            return []
        longest = max(words, key=len)
        pool: list[dict] = []
        try:
            for level in (1, 2, 3, 4):
                rows, _ = self.api.get("/product/search/", label=longest.rstrip("s"), level=level)
                pool += [{"code": str(r["code"]), "name": r.get("label") or "", "level": r.get("level", level)}
                         for r in rows or [] if r.get("code")]
        except Exception:  # no codelist: the codes traded in the last year
            pool = []
            for level in (1, 2, 3):
                try:
                    rows = self.get("/insights/ranking/records", by="product", period=self.last_year(), flow="EXPORT",
                                    level=level, max_items=400, threshold=0) or []
                except InsightsError:
                    rows = []
                pool += [{"code": str(r["code"]), "name": r.get("name") or "", "level": level} for r in rows
                         if r.get("code") not in (None, "OTHER")]
        return [r for r in pool if all(w.rstrip("s") in r["name"].lower() for w in words)]

    _NOT_GOODS = {"goods", "products", "the", "and", "luxembourg", "trade", "imports", "exports", "import", "export",
                  "of", "for", "map"}

    def find_products(self, words: str, flow: Optional[str] = None, period: Optional[str] = None) -> dict:
        """The codes for goods named in words, the most specific heading that is really about them first, each
        with its value (or 'none recorded') in the year and flow(s) asked. Goods with no recorded trade are
        remembered (turn.no_trade) so the reply says so and nothing is drawn under their name."""
        year = _year(str(period)) if period else self.last_year()
        # 'BOTH' is no flow of the data: a line is traded if it was bought or sold
        flows = [str(flow).upper()] if flow and str(flow).upper() in ("IMPORT", "EXPORT") else ["IMPORT", "EXPORT"]
        words = re.sub(r"(?<=\b[a-z])-(?=[a-z])", " ", words or "", flags=re.IGNORECASE)   # 'H-sections'
        wl = [w for w in re.findall(r"[a-z0-9-]+", (words or "").lower()) if len(w) > 2 and w not in self._NOT_GOODS]
        special = products.specific_in(words)
        found: list[dict] = []
        for sp in special:
            for h in sp["headings"]:
                found.append({"code": h, "name": products.name_of(h, sp["goods"]), "rank": 0, "goods": sp["goods"]})
        not_this: list[dict] = []
        for r in self._codelist(wl):
            code, name = r["code"], r["name"]
            if not code.isdigit() or any(f["code"] == code for f in found):
                continue
            if len(code) == 2 and products.omnibus_for(code, words):
                # the words are only in the chapter's official title ('nuclear' in 84's): not what the user means
                short = products.HS_SHORT.get(code, "").lower()
                not_this.append({"code": code, "name": products.chapter_name(code),
                                 "why": f"its official title holds '{words}', but chapter {code} is {short}: never "
                                        f"call it '{words}'"})
                continue
            if len(code) == 2:   # a chapter: about the goods when its short name says them ('steel' -> 72)
                about = all(w.rstrip("s") in products.HS_SHORT.get(code, "").lower() for w in wl)
            else:                # a heading or line: when its first words say them, not only an '(excl. ...)'
                about = all(w.rstrip("s") in products.heading_words(name, 200).lower() for w in wl)
            found.append({"code": code, "name": products.name_of(code, name), "rank": 1 if about else 2})
        # the heading really about the goods first; among those, the broadest (a chapter whose short name says them,
        # then HS4, HS6, CN8)
        found.sort(key=lambda f: (f["rank"], len(f["code"])))
        matches, unt = [], []
        for f in found[:6]:
            m = {"code": f["code"], "name": f["name"],
                 "level": {2: "HS2 chapter", 4: "HS4 heading", 6: "HS6 line", 8: "CN8 line"}.get(len(f["code"]), "")}
            pre = products.predecessor(f["code"])
            if pre and year <= pre["until"] and len(matches) < 4:
                # the code did not exist yet: the goods were filed under the earlier one
                vals = {fl: self.traded(pre["before"], fl, year) for fl in flows}
                m["code_starts"] = pre["from"]
                m["in_" + year] = {"filed_under": f"{pre['level']} {pre['before']} ({pre['before_name']})",
                                   **{f"{'imports' if fl == 'IMPORT' else 'exports'}": fmt.eur(v) if v else
                                      "none recorded" for fl, v in vals.items()}}
                m["say"] = pre["say"] + (f"; in {year} use {pre['before']}, or show_panel line with product="
                                         f"{f['code']} (both codes chained, the change marked)")
                matches.append(m)
                continue
            if len(matches) < 4:
                values = {fl: self.traded(f["code"], fl, year) for fl in flows}
                for fl, v in values.items():
                    m[f"{'imports' if fl == 'IMPORT' else 'exports'}_{year}"] = fmt.eur(v) if v else "none recorded"
                if not any(values.values()):
                    m["traded"] = False
                    unt.append(f["code"])
                self._names[f["code"]] = (products.HS_SHORT[f["code"]].lower() if f["code"] in products.HS_SHORT
                                          else products.heading_words(f["name"]))
            pre = products.predecessor(f["code"])
            if pre:
                m["before_" + pre["from"]] = f"{pre['level']} {pre['before']} ({pre['before_name']}) until {pre['until']}"
            matches.append(m)
        out: dict = {"matches": matches}
        if matches:
            out["best"] = matches[0]["code"]
            out["name_it"] = (f"call {matches[0]['code']} '{matches[0]['name'].lower()}'; name an HS2 chapter by its "
                              "short name as written here, never by its official title")
        if not_this:
            out["not_this"] = not_this
        best = found[0] if found else None
        if best and best["code"] in unt:
            # the goods asked for were not traded: every heading of the same rank that was not traded, said plainly
            codes = [f["code"] for f in found if f["rank"] == best["rank"] and f["code"] in unt] or [best["code"]]
            goods = best.get("goods") or products.heading_words(best["name"])
            sentence = products.no_trade_sentence(goods, codes, flows, year)
            if not any(n["codes"] == codes for n in self.turn.no_trade):
                self.turn.no_trade.append({"goods": goods, "codes": codes, "flows": flows, "year": year,
                                           "sentence": sentence})
            out["no_trade"] = (sentence + " Say exactly that, plainly, first; draw no chart and no mark for these "
                               "goods, and never draw another code under their name"
                               + (f" (chapter {not_this[0]['code']} is {not_this[0]['name']}, a different thing: you "
                                  "may offer it, named that way)" if not_this else "") + ".")
        return out

    def asked_product(self, question: str) -> Optional[dict]:
        """Goods with a heading of their own that the question asks for ('nuclear' -> HS 8401), checked in the
        flow(s) and year asked: {goods, codes, flows, year, traded: {code: {flow: value}}, no_trade, sentence,
        chapter}. None when the question names no such goods. Read before any LLM: the conversation answers a
        question about goods Luxembourg did not trade plainly, without a chart (conversation.Conversation.plan)."""
        hits = products.specific_in(question)
        if not hits:
            return None
        sp = hits[0]
        flows = products.asked_flows(question)
        years = sorted(years_in(question))
        last = self.last_year()
        # a year after the last full one ('a forecast for 2026') asks about goods traded until now: checked there
        year = min(years[-1], last) if years else last
        traded = {h: {fl: self.traded(h, fl, year) for fl in flows} for h in sp["headings"]}
        none = not any(v for t in traded.values() for v in t.values())
        out = {**sp, "codes": sp["headings"], "flows": flows, "year": year, "traded": traded, "no_trade": none,
               "sentence": products.no_trade_sentence(sp["goods"], sp["headings"], flows, year) if none else None}
        # the chapter whose official title holds the words but which is about something else (84 for 'nuclear')
        ch = sp["headings"][0][:2]
        if len(sp["headings"][0]) > 2 and products.omnibus_for(ch, sp["words"]):
            out["chapter"] = ch
        return out

    def no_trade_reply(self, hit: dict, question: str) -> str:
        """The plain answer to a question about goods Luxembourg did not trade: no chart, the figure that is
        there (none), and the chapter whose official title holds the word, clearly named, as another thing."""
        drawn = "on a map" if re.search(r"\bmap\b", question or "", re.I) else "on a chart"
        text = hit["sentence"].rstrip(".") + f", so there is nothing to draw {drawn}."
        ch = hit.get("chapter")
        if ch:
            year, flow = hit["year"], hit["flows"][0]
            word = "imported" if flow == "IMPORT" else "exported"
            short = products.HS_SHORT.get(ch, f"HS {ch}").lower()
            try:
                data = self.get("/insights/ranking", by="product", period=year, flow=flow, parent=ch, max_items=3,
                                threshold=0)
                total = self.traded(ch, flow, year)
                tops = [i for i in data.get("items") or [] if i.get("code") not in (None, "OTHER")][:2]
                inside = " and ".join(f"{products.heading_words(i.get('name'))} ({fmt.eur(i['value'])})" for i in tops)
                text += (f" Chapter {ch}, {short}, is a different thing: its official title begins with "
                         f"\"{hit['goods'].split(' or ')[0]}\", but what Luxembourg {word} under it in {year} was "
                         f"{short}" + (f", above all {inside}" if inside else "")
                         + (f", {fmt.eur(total)} in all" if total else "") + ".")
            except InsightsError:
                text += (f" Chapter {ch}, {short}, is a different thing: its official title begins with "
                         f"\"{hit['goods'].split(' or ')[0]}\", but it holds {short}.")
            flow_word = "imports" if flow == "IMPORT" else "exports"
            nxt = (f"Show {flow_word} of {short} by partner on a map" if drawn == "on a map"
                   else f"What does Luxembourg {'buy' if flow == 'IMPORT' else 'sell'}")
        else:
            nxt = "What does Luxembourg buy" if hit["flows"] == ["IMPORT"] else "What does Luxembourg sell"
        return text + f"\n\n_Next: {nxt}?_"

    @tool("The trend analysis of one series (STL, PELT change points): direction, shape, seasonality, and the "
          "change points (lasting shifts in level) with the level before and after each. Use it before drawing "
          "trend breaks.",
          {"flow": FLOW, "freq": S("M (months, default) or A (years)", enum=["M", "A"]),
           "start": PERIOD, "end": PERIOD, "partner": S("partner code, e.g. CN"),
           "product": S("HS or CN8 code"), "penalty": {"type": "number", "description": "lower finds more breaks; "
                                                                                        "1.5 reads well (default)"},
           "min_size": I("shortest regime, in periods (default 12)")}, ["flow"])
    def analyze_trend(self, flow: str, freq: str = "M", start: Optional[str] = None, end: Optional[str] = None,
                      partner: Optional[str] = None, product: Optional[str] = None, penalty: float = 1.5,
                      min_size: int = 12) -> dict:
        start = self._start(start, product, monthly=freq == "M")
        # A monthly analysis runs to the last month of the data, as a monthly chart does, unless the user asked
        # for an end: the trend's change of a volatile series moves with its end month (15 Oct: H-sections to the
        # United States since 2023, -27.0% to June 2026, -2.5% to July), so one span for every answer.
        end = self.asked.get("end") if freq == "M" else self._end(end)
        if end and freq == "M" and len(end) == 4:
            end = f"{end}-12"
        if self.shared_spans is not None:
            span = (flow, freq, str(partner or "").upper(), str(product or ""))
            start, end = self.shared_spans.setdefault(span, (start, end))
        params = {k: v for k, v in {"flow": flow, "freq": freq, "start": start, "end": end, "partner": partner,
                                    "product": product, "penalty": penalty, "min_size": min_size}.items()
                  if v is not None}
        data = self.get("/trends/analyze", **params)
        self._analysis[self._key(flow, partner, product)] = params
        return summarize("/trends/analyze", data)

    @tool("A time series of trade values, summarised (first, last, highest, lowest, recent values).",
          {"flow": FLOW, "freq": S("A (years) or M (months)", enum=["A", "M"]), "start": PERIOD, "end": PERIOD,
           "partner": S("partner code"), "product": S("HS or CN8 code"),
           "reporter": S("another EU country as reporter (BE, DE, ...); default Luxembourg")}, ["flow"])
    def get_series(self, flow: str, freq: str = "A", start: Optional[str] = None, end: Optional[str] = None,
                   partner: Optional[str] = None, product: Optional[str] = None, reporter: Optional[str] = None) -> dict:
        data = self.get("/insights/series", flow=flow, freq=freq, start=start, end=end, partner=partner,
                        product=product, reporter=reporter)
        out = summarize("/insights/series", data)
        if freq == "M" and isinstance(out, dict):
            same = same_months(data)
            if same:
                out["same_months"] = same
        if product:
            history = self.code_history(str(product), flow, start, end, partner=partner, rows=data)
            if history:
                out["code_history"] = history
        return out

    @tool("HOW MANY: the quantities of one product (a CN8 line, an HS6 or HS4 heading, or a chapter) next to its "
          "value, per year: the count in the line's own unit (number of items for cars, litres, pairs...), the "
          "weight in tonnes, and the average value per unit and per tonne, all computed by the backend. Says which "
          "measure answers 'how many' (use) and what to write (say).",
          {"product": S("HS or CN8 code (find_codes first): 8703 cars, 72 iron and steel"), "flow": FLOW,
           "period": PERIOD, "partner": S("only trade with this partner"), "start": PERIOD},
          ["product", "flow"])
    def get_quantities(self, product: str, flow: str, period: Optional[str] = None, partner: Optional[str] = None,
                       start: Optional[str] = None) -> dict:
        year = _year(str(period)) if period else self.last_year()
        first = _year(str(start)) if start else str(int(year) - 4)
        data = self.get("/insights/series", flow=flow, product=product, partner=partner, measure="all",
                        start=first, end=year, envelope="true")
        rows = (data.get("rows") if isinstance(data, dict) else data) or []
        unit = next((r.get("unit") for r in rows if r.get("unit")), None)
        units_note = next((r.get("units_note") for r in rows if r.get("units_note")), None)

        def count(v: Optional[float], word: str) -> Optional[str]:
            return None if v is None else f"{v:,.0f} {word}"

        def per(v: Optional[float]) -> Optional[str]:
            return None if v is None else (fmt.eur(v) if v >= 1e6 else f"€{v:,.0f}")

        years = [{"year": r["period"], "value": fmt.eur(r.get("value")),
                  "units": count(r.get("units"), unit or "units"), "weight": count(r.get("weight"), "t"),
                  "per_unit": per(r.get("eur_per_unit")), "per_tonne": per(r.get("eur_per_tonne"))}
                 for r in rows]
        now = next((y for y in years if y["year"] == year), None)
        has_units = bool(now and now["units"])
        has_weight = bool(now and now["weight"])
        use = "units" if has_units else "weight" if has_weight else None
        where = f" to {self.country(partner)}" if partner and flow == "EXPORT" else \
            f" from {self.country(partner)}" if partner else ""
        if use == "units":
            say = (f"In {year}: {now['units']} ({unit}: the goods' own unit of count), worth "
                   f"{now['value']}, about {now['per_unit']} per unit on average (value divided by the count: an "
                   f"average over models and grades, not a price). Weight: {now['weight']}.")
        elif use == "weight":
            say = (f"In {year}: {now['weight']} (tonnes, net mass), worth {now['value']}, about {now['per_tonne']} "
                   f"per tonne on average (not a price). "
                   + ("No count in units: " + units_note.rstrip(".") + "." if units_note else
                      f"No count in units is published for these goods{where}."))
        else:
            say = (f"No quantity is published for these goods{where} in {year}: the data has their value only "
                   f"({now['value'] if now and now['value'] else 'none recorded'}). Say so plainly.")
        self.turn.quantities.append({"product": str(product), "use": use, "unit": unit})
        out = {"product": str(product), "flow": flow, "partner": partner, "year": year, "use": use,
               "unit": unit if use == "units" else "t" if use == "weight" else None, "say": say, "years": years,
               "how": ("value in euros, weight (QUANTITY_IN_100KG x 0.1 = tonnes) and the supplementary quantity "
                       "of Eurostat ds-045409; unit values computed by the backend"),
               "draw_it": ("a chart of the count: show_panel line with quantity='units' (or 'weight'); a table "
                           "of value, quantity and unit value: show_panel table with quantity='all'")}
        if units_note and use != "units":
            out["units_note"] = units_note
        return out

    def code_history(self, product: str, flow: str, start: Optional[str], end: Optional[str],
                     partner: Optional[str] = None, rows: Any = None) -> Optional[dict]:
        """When a product code has no data for (part of) the years asked: why, and the series of the code its
        goods were filed under before (85171300, smartphones, is 85171200 until 2021). None when nothing is
        missing."""
        rows = rows.get("rows") if isinstance(rows, dict) else rows
        have = sorted(str(r.get("period"))[:4] for r in rows or [] if r.get("value"))
        asked_from = _year(str(start)) if start else None
        if have and (not asked_from or have[0] <= asked_from):
            return None
        pre = products.predecessor(product)
        out: dict = {}
        if pre and (not asked_from or asked_from <= pre["until"]):
            before = self.get("/insights/series", flow=flow, freq="A", start=asked_from or "2015", end=pre["until"],
                              product=pre["before"], partner=partner)
            vals = {str(r["period"]): fmt.eur(r["value"]) for r in before or [] if r.get("value")}
            out = {"code_starts": pre["from"], "say": pre["say"] + ".",
                   f"before_{pre['from']}": {"code": pre["before"], "goods": pre["goods"], "values": vals,
                                             "call_them": pre["before_name"]},
                   "chart": f"show_panel line with product={product} and start={asked_from or '2015'} draws both codes "
                            f"as one chained series, the change of code marked at {pre['from']}"}
        elif have:
            out = {"first_year_with_data": have[0],
                   "say": f"this code has data only from {have[0]}: it may have been created then (the HS was revised "
                          f"in 2017 and 2022) or there was no trade before"}
        else:
            out = {"say": "no recorded trade for this code in these years"}
        return out

    @tool("The biggest movers between two periods (up and down), with totals of both periods, among the lines "
          "big enough to count (the significance threshold: significance says how many lines pass it and what "
          "they cover). At any product level: level 2 HS4, 3 HS6, 4 CN8 ('which CN8 lines grew fastest'). "
          "rank_by growth ranks the risers by growth rate instead of by euros.",
          {"by": S("product or partner", enum=["product", "partner"]), "period": PERIOD, "previous": PERIOD,
           "flow": FLOW, "level": I("product level: 1 HS2, 2 HS4, 3 HS6, 4 CN8"), "parent": S("drill into this code"),
           "partner": S("only trade with this partner"), "product": S("only this product"),
           "limit": I("rows per side (default 6)"),
           "rank_by": S("change (euros, default) or growth (fastest growth rate)", enum=["change", "growth"])},
          ["by", "period", "previous", "flow"])
    def get_movers(self, by: str, period: str, previous: str, flow: str, level: Optional[int] = None,
                   parent: Optional[str] = None, partner: Optional[str] = None, product: Optional[str] = None,
                   limit: int = 6, rank_by: str = "change") -> dict:
        if by == "product" and self.deep(parent, level):
            # the CN8 lines of an HS4 heading: through its HS6 lines (the API reads one level below a parent)
            merged: dict = {"up": [], "down": [], "new": []}
            data = {}
            for c in self.children(parent, int(level), period, flow, partner, previous=previous):
                r = self.get("/insights/movers", by=by, period=period, previous=previous, flow=flow, level=level,
                             parent=c, partner=partner, limit=40)
                for k in merged:
                    merged[k] += r.get(k) or []
            data = {"period": period, "previous": previous, "threshold": None,
                    "up": sorted(merged["up"], key=lambda r: -(r.get("change") or 0)),
                    "down": sorted(merged["down"], key=lambda r: r.get("change") or 0), "new": merged["new"]}
            if parent:
                tot = self.get("/insights/movers", by=by, period=period, previous=previous, flow=flow,
                               parent=parent, partner=partner, limit=1)
                data.update(total=tot.get("total"), previous_total=tot.get("previous_total"))
        else:
            data = self.get("/insights/movers", by=by, period=period, previous=previous, flow=flow, level=level,
                            parent=parent, partner=partner, product=product,
                            limit=max(limit, 40 if rank_by == "growth" else 6))
        if rank_by == "growth":   # the fastest risers among lines that already counted the year before (a line
            # growing from almost nothing grows by thousands of percent), new lines have no growth rate
            floor = 1e-3 * (data.get("previous_total") or 0)
            data = {**data, "up": sorted([r for r in data.get("up") or [] if r.get("growth") is not None
                                          and (r.get("previous") or 0) >= floor], key=lambda r: -r["growth"])}
        out = summarize("/insights/movers", data, n=limit)
        if rank_by == "growth":
            out["ranked_by"] = ("growth rate, among the lines worth at least 0.1% of the flow the year before (the "
                                "chart of the change, a waterfall, ranks by euros)")
        if by == "product" and level and int(level) >= 2 and data.get("threshold") is not None:
            try:
                lad = self.get("/insights/ladder", by=by, period=period, flow=flow, level=level, parent=parent,
                               partner=partner)
                cut = next((r for r in lad.get("ladder") or [] if r.get("threshold") == data["threshold"]), None)
                out["significance"] = {
                    "lines_in_all": lad.get("n_items"),
                    "threshold": f"{fmt.pct(data['threshold'], digits=3)} of the flow",
                    **({"lines_above_it": cut["items"], "they_cover": fmt.pct(cut["coverage"])} if cut else {}),
                    "say": "only lines above the threshold in either year are ranked: smaller ones can grow by "
                           "thousands of percent from almost nothing"}
            except InsightsError:
                pass
        return out

    @tool("Drill a change down from the HS2 chapter to the CN8 product line that moved most, then the partners "
          "it was bought from and sold to. The trend finder's main tool for 'why did exports/imports rise or fall'.",
          {"flow": FLOW, "period": PERIOD, "previous": PERIOD,
           "direction": S("up (a rise, default) or down (a fall)", enum=["up", "down"]),
           "partner": S("only trade with this partner")}, ["flow", "period", "previous"])
    def drill_down(self, flow: str, period: str, previous: str, direction: Optional[str] = None,
                   partner: Optional[str] = None) -> dict:
        totals = self.get("/insights/movers", by="product", period=period, previous=previous, flow=flow,
                          partner=partner, limit=6)
        before, now = totals.get("previous_total"), totals.get("total")
        if before is None or now is None:
            ser = self.get("/insights/series", flow=flow, freq="A", start=previous, end=period, partner=partner)
            before, now = ser[0]["value"], ser[-1]["value"]
        change = now - before
        side = direction or ("up" if change >= 0 else "down")
        path, movers = [], totals
        while movers.get(side):
            top = movers[side][0]
            if path and (top["code"] == path[-1]["code"] or not top["code"].startswith(path[-1]["code"])):
                break
            path.append(top)
            if len(top["code"]) >= 8:
                break
            movers = self.get("/insights/movers", by="product", period=period, previous=previous, flow=flow,
                              parent=top["code"], partner=partner, limit=6)
        out: dict = {"flow": flow, "period": period, "previous": previous, "total_before": fmt.eur(before),
                     "total_after": fmt.eur(now), "change": fmt.eur(change, signed=True),
                     "growth": fmt.pct(now / before - 1, signed=True) if before else None,
                     "path": [{"code": p["code"], "name": _name(p, 70), "before": fmt.eur(p["previous"]),
                               "after": fmt.eur(p["value"]), "change": fmt.eur(p["change"], signed=True),
                               "share_of_total_change": fmt.pct(p["change"] / change) if change else None}
                              for p in path]}
        if not path:
            return out
        leaf = path[-1]
        pre = products.predecessor(str(leaf["code"]))
        for p in path:
            self._names[str(p["code"])] = goods_words(_name(p, 70))
        goods = self._names[str(leaf["code"])]

        # the reads below do not depend on each other: the line's earlier history and its two partner rankings
        # go together (about a second saved on a first question)
        def history():
            return self.get("/insights/series", flow=flow, freq="A", start="2015", end=pre["until"],
                            product=pre["before"], partner=partner)

        def ranking(f):
            return self.get("/insights/ranking", by="partner", period=period, flow=f, product=leaf["code"],
                            max_items=5, threshold=0)
        jobs = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            if pre:
                jobs["history"] = pool.submit(history)
            if partner is None:
                for f in ("IMPORT", "EXPORT"):
                    jobs[f] = pool.submit(ranking, f)
        if pre:   # the line's code is young: its goods' earlier history, under the code they had before
            try:
                rows = jobs["history"].result()
                vals = {str(r["period"]): fmt.eur(r["value"]) for r in rows or [] if r.get("value")}
                if vals:
                    out["line_history"] = {"say": pre["say"] + ". Say in one sentence that the line's level is not "
                                                  "new: give the earlier years below, as " + pre["before_name"]
                                                  + ", never as smartphone figures.",
                                           f"{pre['goods']}_{pre['level']}_{pre['before']}": vals}
            except InsightsError:
                pass
        rest_before, rest_now = before - leaf["previous"], now - leaf["value"]
        out["total_without_line"] = {"before": fmt.eur(rest_before), "after": fmt.eur(rest_now),
                                     "growth": fmt.pct(rest_now / rest_before - 1, signed=True) if rest_before else None}
        if partner is None:
            for f, key in (("IMPORT", "bought_from"), ("EXPORT", "sold_to")):
                try:
                    r = jobs[f].result()
                    items = [i for i in (r.get("items") or []) if i.get("code") != "OTHER"]
                    out[key] = _rows(items, 3, {"code": lambda i: i["code"], "name": _name,
                                                "value": lambda i: fmt.eur(i["value"]),
                                                "share": lambda i: fmt.pct(i["share"])})
                    for i in items[:3]:
                        if i.get("share") is not None:
                            self.turn.shares.append(line_share(i["share"], _name(i), goods, f))
                except InsightsError:
                    out[key] = []
            out["what_the_shares_are"] = (
                f"bought_from share = that partner's share of all the {goods} Luxembourg bought; sold_to share = its "
                f"share of all the {goods} Luxembourg sold. They are NOT shares of everything bought from or sold to "
                f"that partner: never write 'X% of imports from <partner>' or 'X% of exports to <partner>'. Say it "
                f"like: " + "; ".join(next((s["say"] for s in self.turn.shares if s["flow"] == f), "")
                                      for f in ("IMPORT", "EXPORT")).strip("; ") + ".")
        return out

    def children(self, parent: str, level: int, period: str, flow: str, partner: Optional[str] = None,
                 previous: Optional[str] = None) -> list[str]:
        """The codes one level above ``level`` under ``parent`` (the parents to ask at ``level``), one level at a
        time: the API ranks the children of a code at the level just below it only (a CN8 line's parent is its HS6
        line, so parent=7216 with level 4 is empty; parent=721633 with level 4 is not)."""
        codes = [str(parent)]
        for lv in range(level_of(parent) + 1, int(level)):
            nxt: list[str] = []
            for c in codes:
                r = self.get("/insights/ranking", by="product", period=period, flow=flow, level=lv, parent=c,
                             partner=partner, max_items=40, threshold=0)
                nxt += [str(i["code"]) for i in (r.get("items") or []) if str(i.get("code", "")).isdigit()]
                if previous:   # a line that was there the year before only
                    r = self.get("/insights/ranking", by="product", period=previous, flow=flow, level=lv, parent=c,
                                 partner=partner, max_items=40, threshold=0)
                    nxt += [str(i["code"]) for i in (r.get("items") or []) if str(i.get("code", "")).isdigit()
                            and str(i["code"]) not in nxt]
            codes = nxt
        return codes

    def deep(self, parent: Optional[str], level: Optional[int]) -> bool:
        """A parent two levels or more above the level asked (7216 with level 4, CN8)."""
        return bool(parent and level and level_of(parent) and int(level) > level_of(parent) + 1)

    @tool("Rank partners or products by value in one period (shares of the total).",
          {"by": S("partner or product", enum=["partner", "product"]), "period": PERIOD, "flow": FLOW,
           "level": I("product level: 1 HS2 (default), 2 HS4, 3 HS6, 4 CN8"), "parent": S("children of this code"),
           "partner": S("only trade with this partner"), "product": S("only this product"),
           "max_items": I("rows (default 8)")}, ["by", "period", "flow"])
    def get_ranking(self, by: str, period: str, flow: str, level: Optional[int] = None, parent: Optional[str] = None,
                    partner: Optional[str] = None, product: Optional[str] = None, max_items: int = 8) -> dict:
        if by == "product" and self.deep(parent, level):
            # the CN8 lines of an HS4 heading: through its HS6 lines, the partner filter kept
            items, total = [], None
            for c in self.children(parent, int(level), period, flow, partner):
                r = self.get("/insights/ranking", by=by, period=period, flow=flow, level=level, parent=c,
                             partner=partner, max_items=max_items, threshold=0)
                items += [i for i in r.get("items") or [] if str(i.get("code", "")).isdigit()]
                total = r.get("total") if total is None else total
            items.sort(key=lambda i: -(i.get("value") or 0))
            data = {"period": period, "by": by, "total": None, "items": items[:max_items]}
            out = summarize("/insights/ranking", data, n=max_items)
            out["how"] = (f"the lines of {parent} at level {level}, read through its HS6 lines, every line kept (no "
                          f"significance cut)" + (f", trade with {self.country(partner)} only" if partner else ""))
            if not items:
                out["say"] = (f"no {'line' if int(level) == 4 else 'code'} under {parent} has recorded trade in "
                              f"{period}" + (f" with {self.country(partner)}" if partner else ""))
            return out
        data = self.get("/insights/ranking", by=by, period=period, flow=flow, level=level, parent=parent,
                        partner=partner, product=product, max_items=max_items)
        if by == "partner" and (data.get("unallocated") or {}).get("value") is not None:
            self.turn.unspecified += [("eur", data["unallocated"]["value"]), ("pct", data["unallocated"].get("share"))]
        return summarize("/insights/ranking", data, n=max_items)

    @tool("Growth per year of each partner or product over a span (who grew fastest).",
          {"by": S("partner or product", enum=["partner", "product"]), "flow": FLOW, "start": START, "end": PERIOD,
           "level": I("product level"), "limit": I("rows (default 6)"),
           "product": S("by partner: only this product line (HS/CN8 code), e.g. the markets of one CN8 line"),
           "partner": S("by product: only the trade with this partner (ISO code)")}, ["by", "flow"])
    def get_trends(self, by: str, flow: str, start: Optional[str] = None, end: Optional[str] = None,
                   level: Optional[int] = None, limit: int = 6, product: Optional[str] = None,
                   partner: Optional[str] = None) -> dict:
        start = self._start(start, *(["000000"] if by == "product" and (level or 1) >= 3 else []))
        product = product if by == "partner" and product and str(product).upper() != "TOTAL" else None
        partner = partner if by == "product" and partner else None
        data = self.get("/insights/trends", by=by, flow=flow, start=start, end=self._end(end), level=level,
                        limit=limit, product=product, partner=partner)
        out = summarize("/insights/trends", data, n=limit)
        if isinstance(out, dict) and out.get("items"):
            what = "partner countries" if by == "partner" else {1: "product chapters (HS2)", 2: "HS4 headings",
                                                               3: "HS6 lines", 4: "CN8 lines"}.get(level or 1)
            out["significance"] = (f"ranked among the {data.get('n_significant')} {what} with at least "
                                   f"{fmt.pct(data.get('threshold'))} of {flow.lower()}s at the start or the end; "
                                   "new ones (nothing at the start) have no growth rate and are not ranked")
            out["draw_it"] = (f"show_panel type='bar', measure='growth', by='{by}', flow='{flow}', start='{start}'"
                              + (f", level={level}" if level else "") + (f", product='{product}'" if product else "")
                              + (f", partner='{partner}'" if partner else "") + " draws this ranking (growth per year, with "
                              "each one's first and last value); then highlight the fastest with {<code>_growth} "
                              "{<code>_then} {<code>_now}")
        return out

    @tool("The fitted long-run growth of one series (log-linear trend): growth per year and fit.",
          {"flow": FLOW, "start": START, "end": PERIOD, "partner": S("partner code"), "product": S("product code")},
          ["flow"])
    def get_trend(self, flow: str, start: Optional[str] = None, end: Optional[str] = None,
                  partner: Optional[str] = None, product: Optional[str] = None) -> dict:
        data = self.get("/insights/trend", flow=flow, freq="A", start=self._start(start, product), end=self._end(end),
                        partner=partner, product=product)
        return summarize("/insights/trend", data)

    @tool("Dated events from the curated list (agreements, tariffs, crises, the pandemic, wars) with their ids.",
          {"start": PERIOD, "end": PERIOD, "category": S("agreement, tariff, pandemic, war, shock, policy, ...")})
    def get_events(self, start: str = "1999", end: Optional[str] = None, category: Optional[str] = None) -> Any:
        data = self.get("/insights/events", start=start, end=end, category=category)
        items = data.get("items") if isinstance(data, dict) else data
        if str(start)[:4] <= FIRST_YEAR:
            self._note_events_from(items)
        out = summarize("/insights/events", data)
        by_id = {str(e.get("id")): e for e in items or []}
        scoped = [(row, c, self.event_line(c)) for row in out if (c := by_id.get(str(row.get("id"))))]
        scoped = [(row, c, line) for row, c, line in scoped if line]

        def figures(item):
            row, c, line = item
            try:
                return self.line_figures(line, str(c["at"])[:4])
            except (InsightsError, KeyError, TypeError, ValueError) as exc:
                return f"could not read: {exc}"
        try:   # the trend breaks on the page, so the model knows which events are near one before it says so
            spec = self.spec()
            on_page = sorted({str((m.get("params") or {}).get("at"))[:7] for m in spec.get("marks", [])
                              if m.get("kind") == "trend_break" and (m.get("params") or {}).get("at")})
        except Exception:
            on_page = []
        for row in out if on_page else []:
            near = min(on_page, key=lambda b: abs(months_between(b, str(row["at"])[:7])))
            months = abs(months_between(near, str(row["at"])[:7]))
            row.update(nearest_trend_break=fmt.period_words(near), months_apart=months,
                       lines_up_with_a_break=months <= 12)
        with ThreadPoolExecutor(max_workers=8) as pool:   # one small series call per product: read them together
            found = list(pool.map(figures, scoped))
        for (row, c, line), fig in zip(scoped, found):
            # an event about one product or partner: its own line's change that year, ready to quote
            row["about_line"] = line["label"]
            row["line_that_year"] = fig
            row["quote"] = ("this event is about that line, not all trade: its add_events note uses the placeholders "
                            "{line_change} {line_diff} (never typed figures, never {exp_change}); in your reply quote "
                            "line_that_year, never the change of total exports or imports")
        return out

    def _note_events_from(self, items: Optional[list]) -> None:
        years = sorted(str(e.get("at"))[:4] for e in items or [] if e.get("at"))
        if years and (self.turn.events_from is None or years[0] < self.turn.events_from):
            self.turn.events_from = years[0]

    # ---- events about one product or partner (the scope of /insights/events)
    @staticmethod
    def event_line(c: Optional[dict], e: Optional[dict] = None) -> Optional[dict]:
        """The trade line a curated event is about, from the model's product/partner or else the event's own scope:
        {"partner", "products", "flow", "both_flows", "label"}; None for a broad event (the pandemic, a canal).

        US steel tariffs 2025 (US; 72, 73, 76; EXPORT): iron and steel, articles of iron or steel and aluminium
        exported to the United States. Sanctions on Russia (RU, UA; 27, 72, 31): trade with Russia, the first
        partner (with several partners the products are theirs together, too thin to read). EU-UK TCA (GB): trade
        with the United Kingdom, both flows."""
        if not c and not e:
            return None
        e = e or {}
        scope = (c or {}).get("scope") or {}
        partners = [str(p).upper() for p in scope.get("partners") or [] if p]
        products = [str(p) for p in scope.get("products") or [] if p]
        flows = [f for f in scope.get("flows") or [] if f in ("EXPORT", "IMPORT")]
        partner = str(e["partner"]).upper() if e.get("partner") else (partners[0] if partners else None)
        if e.get("product"):
            prods = [str(e["product"])]
        elif products and (len(partners) <= 1 or e.get("partner")):
            prods = products[:4]
        else:
            prods = []
        if not partner and not prods:
            return None
        flow = e["flow"] if e.get("flow") in ("EXPORT", "IMPORT") else flows[0] if len(flows) == 1 else "EXPORT"
        names = [HS_SHORT.get(p, f"HS {p}") for p in prods]
        names = [n if n.startswith("HS") else n[:1].lower() + n[1:] for n in names]
        goods = ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else (names[0] if names else "")
        word = "exports" if flow == "EXPORT" else "imports"
        where = {**COUNTRY_NAMES, "CA": "Canada", "UA": "Ukraine"}.get(partner, partner) if partner else ""
        if where.startswith(("United ", "Netherlands")):
            where = "the " + where
        label = (f"{goods} {word}" if goods else word) + (f" {'to' if flow == 'EXPORT' else 'from'} {where}"
                                                          if where else "")
        if prods:
            label += f" (HS {', '.join(prods)})"
        return {"partner": partner, "products": prods, "flow": flow, "both_flows": not flows and not e.get("flow"),
                "label": label}

    def _line_values(self, line: dict, flow: str, prev: str, year: str) -> tuple[Optional[float], Optional[float]]:
        """The line's value the year before and in ``year``: the sum of its products (with its partner), one call
        per product."""
        sums: dict[str, Optional[float]] = {prev: None, year: None}
        for p in line["products"] or [None]:
            rows = self.get("/insights/series", flow=flow, freq="A", start=prev, end=year, partner=line["partner"],
                            product=p)
            for r in rows or []:
                k = str(r.get("period"))
                if k in sums and r.get("value") is not None:
                    sums[k] = (sums[k] or 0.0) + r["value"]
        return sums[prev], sums[year]

    def line_figures(self, line: dict, year: str) -> dict:
        """The line's change in ``year`` against the year before, written out: what a scoped event's note shows."""
        prev = str(int(year) - 1)
        out = {}
        for flow in (("EXPORT", "IMPORT") if line["both_flows"] else (line["flow"],)):
            before, now = self._line_values(line, flow, prev, year)
            key = "exports" if flow == "EXPORT" else "imports"
            out[key] = {prev: fmt.eur(before), year: fmt.eur(now),
                        "change": fmt.pct(now / before - 1, signed=True) if now is not None and before else None,
                        "diff": fmt.eur(now - before, signed=True) if now is not None and before is not None else None}
        return out

    def _remember_scoped(self, e: dict, line: dict, year: str) -> None:
        """Note an event about one line, with the total-trade changes of its year written as the tools write them,
        so the reply can be checked for one of them quoted as the event's own figure."""
        totals = []
        for flow in ("EXPORT", "IMPORT"):
            try:
                rows = self.get("/insights/series", flow=flow, freq="A", start=str(int(year) - 1), end=year)
                vals = {str(r["period"]): r["value"] for r in rows or [] if r.get("value") is not None}
                before, now = vals.get(str(int(year) - 1)), vals.get(year)
                if before and now is not None:
                    totals.append(fmt.pct(now / before - 1, signed=True))
            except (InsightsError, KeyError, TypeError, ValueError):
                pass
        try:
            figures = self.line_figures(line, year)
        except (InsightsError, KeyError, TypeError, ValueError):
            figures = {}
        self.turn.see(figures)   # the model may quote the line's figures in its reply
        self.turn.scoped.append({"label": e.get("label") or "", "line": line["label"], "year": year,
                                 "totals": [t for t in totals if t], "line_figures": figures})

    def _line_vocab(self, line: dict, year: str, prev: str) -> dict:
        """{line_before} {line_now} {line_change} {line_diff} (the line's flow), and for an event about both flows
        {line_exp_change} {line_imp_change} {line_exp_diff} {line_imp_diff} ..., each the sum of its products."""
        vocab: dict = {}
        flows = [(line["flow"], "line")]
        if line["both_flows"]:
            flows += [("EXPORT", "line_exp"), ("IMPORT", "line_imp")]
        for flow, pre in flows:
            for when, period in (("before", prev), ("now", year)):
                parts = []
                for i, p in enumerate(line["products"] or [None]):
                    ps = {"flow": flow, "freq": "A", "start": period, "end": period,
                          **({"partner": line["partner"]} if line["partner"] else {}), **({"product": p} if p else {})}
                    name = f"{pre}_{when}" if len(line["products"]) <= 1 else f"{pre}_{when}_{i}"
                    vocab[name] = fact_api("/insights/series", ps, "0.value", "eur")
                    parts.append(name)
                acc = parts[0]
                for j, nxt in enumerate(parts[1:], 1):
                    name = f"{pre}_{when}" if j == len(parts) - 1 else f"{pre}_{when}_s{j}"
                    vocab[name] = fact_op("sum", acc, nxt, "eur")
                    acc = name
            vocab[f"{pre}_change"] = fact_op("growth", f"{pre}_now", f"{pre}_before", "pct_signed")
            vocab[f"{pre}_diff"] = fact_op("diff", f"{pre}_now", f"{pre}_before", "eur_signed")
        return vocab

    @tool("Forecast one series: next year's value with its interval, the months already observed, the method "
          "(by default the backtest's winner: Holt-Winters averaged with gradient boosting trained on every "
          "chapter), its rolling backtest against the seasonal naive forecast at 1, 3, 6 and 12 months ahead, "
          "every method's score, and scenarios for a line that started recently (the smartphone line, from "
          "September 2025: it stays at its recent level, or stops). At most 36 months ahead.",
          {"flow": FLOW, "partner": S("partner code"), "product": S("product code"),
           "horizon": I("months ahead (default 12, at most 36)"),
           "method": S("auto (default, the backtest's winner), ets, theta, gbm, airline, snaive",
                       enum=["auto", "ensemble", "ets", "theta", "gbm", "airline", "stl_ets", "snaive"])}, ["flow"])
    def get_forecast(self, flow: str, partner: Optional[str] = None, product: Optional[str] = None,
                     horizon: int = 12, method: Optional[str] = None) -> dict:
        if TWO_YEARS_RE.search(self.question or "") and horizon < 24:
            horizon = 24          # 'the next two years': both calendar years
        horizon = max(horizon, self.months_to(self.asked_year()))   # the year asked, as far as the API goes
        horizon = max(1, min(int(horizon or 12), MAX_HORIZON))       # the API refuses more than 36 months
        try:
            return self._forecast_of(flow, partner, product, horizon, method)
        except InsightsError as exc:
            if exc.status not in (400, 422) or not (product or partner):
                raise
            reason = str(exc.detail)
        # no tested forecast for this line (too young, a one-off spike): the honest alternative, at once, so the
        # model never retries the same call: its chapter, else all trade in the flow
        tries = ([str(product)[:2]] if product and len(str(product)) > 2 else []) + [None]
        for alt in tries:
            try:
                out = self._forecast_of(flow, None if alt else partner, alt, horizon, method)
            except InsightsError:
                continue
            what = products.chapter_name(alt) if alt else ("all exports" if flow == "EXPORT" else "all imports")
            name = self._names.get(str(product)) or (f"CN8 {product}" if product else partner)
            out["asked_for"] = {"product": product, "partner": partner,
                                "no_forecast": f"no tested forecast for {name}: {reason}"}
            out["instead"] = (f"the forecast of {what}, the closest series with enough history; say plainly that "
                              f"{name} itself cannot be forecast honestly (too short or a one-off), give this forecast "
                              "and the scenarios if there are any. Do not call get_forecast again for the line.")
            out["forecast_of"] = what
            # a band asked for the line goes on the series forecast instead
            self._fc_redirect[self._key(flow, partner, product)] = (None if alt else partner, alt)
            return out
        raise ToolProblem(f"no tested forecast for this series: {reason}. Do not call get_forecast again: answer "
                          "with what you have")

    def asked_year(self) -> Optional[str]:
        """The year a forecast question names after the last full year ('What will exports be in 2030?')."""
        last = str(self.coverage().get("last_year") or "2025")[:4]
        years = sorted(y for y in re.findall(r"\b20\d\d\b", self.question or "") if y > last)
        return years[-1] if years else None

    def months_to(self, year: Optional[str]) -> int:
        """Months from the last observed month to the end of ``year`` (0 without a year)."""
        if not year:
            return 0
        last = str(self.coverage().get("last_month") or "")
        if not re.fullmatch(r"\d{4}-\d{2}", last):
            return 0
        return (int(year) - int(last[:4])) * 12 + (12 - int(last[5:]))

    def _forecast_of(self, flow: str, partner: Optional[str], product: Optional[str], horizon: int,
                     method: Optional[str]) -> dict:
        horizon = max(1, min(int(horizon or 12), MAX_HORIZON))
        params = {k: v for k, v in {"flow": flow, "freq": "M", "horizon": horizon, "interval": 0.8, "aggregate": "A",
                                    "method": method if method not in (None, "auto") else None,
                                    "partner": partner, "product": product}.items() if v is not None}
        data = self.get("/insights/forecast", **params)
        self._forecast[self._key(flow, partner, product)] = params
        rows = data.get("rows") or []
        ahead = [r for r in rows if r.get("low") is not None or r.get("high") is not None] or rows[-1:]
        hist = [r for r in rows if r not in ahead]
        bt = data.get("backtest") or {}
        out = {"method": data.get("method_label") or data.get("method"),
               "interval": fmt.pct(data.get("interval") or 0.8, digits=0),
               "last_actual": ({"period": hist[-1]["period"], "value": fmt.eur(hist[-1]["value"])} if hist else None),
               "forecast": [{"period": r["period"], "value": fmt.eur(r.get("value")), "low": fmt.eur(r.get("low")),
                             "high": fmt.eur(r.get("high")),
                             **({"observed_so_far": fmt.eur(r["observed"]), "months_observed": r.get("observed_months")}
                                if r.get("observed") else {})} for r in ahead],
               "last_observed_month": data.get("last_observed")}
        pct = lambda v: fmt.pct(v) if isinstance(v, (int, float)) else None  # noqa: E731
        if bt:
            out["backtest"] = {
                "two_tests": two_tests(bt),
                "tested": tested_span(bt),
                "beats_naive_by": {f"{h} month{'s' if h > 1 else ''} ahead": pct(bt.get(f"skill_h{h}"))
                                   for h in (1, 3, 6, 12) if bt.get(f"skill_h{h}") is not None},
                "error_12_months_ahead": pct(bt.get("mape_h12")),
                "naive_error_12_months_ahead": pct(bt.get("naive_mape_h12")),
                "band_held": pct(bt.get("coverage")),
                **({"last_full_year": {"year": bt.get("period"), "error": pct(bt.get("mape")),
                                       "naive_error": pct(bt.get("naive_mape")),
                                       "beat_naive": bt["mape"] < bt["naive_mape"]}}
                   if bt.get("mape") is not None and bt.get("naive_mape") is not None else {})}
            methods = sorted(bt.get("methods") or [], key=lambda m: m.get("mase") if m.get("mase") is not None else 9)
            if methods:
                out["methods_tested"] = [{"method": m.get("label") or m.get("method"), "chosen": bool(m.get("chosen")),
                                          "beats_naive_by": pct(m.get("skill")),
                                          "error_12_months_ahead": pct(m.get("mape_h12"))} for m in methods[:5]]
        year = str(ahead[0]["period"])[:4] if ahead else None
        years = [str(r["period"])[:4] for r in ahead]
        asked = self.asked_year()
        if asked and years and asked not in years:
            out["furthest_year"] = (f"the forecast reaches {years[-1]} at most ({MAX_HORIZON} months after the last "
                                    f"observed month), not {asked}: say so plainly and give {years[-1]}")
        if len(years) > 1 or (asked and years and asked != years[0]):
            out["tested_up_to"] = (f"the method was tested up to {TESTED_UP_TO} months ahead only: the backtest "
                                   f"scores are about {years[0]}; say plainly that the later years are less certain")
            self.turn.beyond_tested = True
        scen = []
        for s in data.get("scenarios") or []:
            row = next((r for r in s.get("rows") or [] if str(r.get("period")) == year), None)
            if row:
                scen.append({"id": s.get("id"), "label": s.get("label"), "value": fmt.eur(row.get("value")),
                             "low": fmt.eur(row.get("low")), "high": fmt.eur(row.get("high"))})
        if scen:
            out["scenarios"] = scen
            said = scenarios_words(data.get("scenarios") or [], year, (ahead[0] or {}).get("value"))
            if said and (not asked or asked == year):   # a reply about 2028 says no 2026 scenarios
                self.turn.scenarios = said
            out["scenarios_note"] = (
                (said + " " if said else "")
                + "Say it in this order: the central figure with its 80% interval, then the two scenarios as the "
                  "line's level, as written here. Never call the line one-off. add_forecast_band draws them with "
                  "{continues} and {stops}.")
        if data.get("caveat"):
            out["caveat"] = data["caveat"]
        if len(ahead) > 1:
            out["years_ahead"] = [r["period"] for r in ahead]
        return out

    METHOD_NAMES = {"ensemble": "The ensemble of Holt-Winters and gradient boosting", "ets": "Holt-Winters",
                    "theta": "The Theta method", "gbm": "Gradient boosting", "nn": "The neural network generated by BESSER",
                    "snaive": "The seasonal naive forecast", "airline": "SARIMA (airline)", "stl_ets": "STL with ETS"}

    @tool("Which forecasting method works best here: the rolling backtest of EVERY method on this series (the "
          "default ensemble, Holt-Winters, Theta, gradient boosting, the neural network generated by BESSER, and the "
          "seasonal naive benchmark): how much smaller each one's errors are than the seasonal naive forecast's at "
          "1, 3, 6 and 12 months ahead, its error 12 months ahead and how often its band held, as ready-made lines "
          "(table) to quote. Use it for 'which method is best', 'how good / reliable is the forecast'.",
          {"flow": FLOW, "partner": S("partner code"), "product": S("product code")})
    def get_backtest(self, flow: str = "EXPORT", partner: Optional[str] = None, product: Optional[str] = None) -> dict:
        base = {k: v for k, v in {"flow": flow, "freq": "M", "horizon": 12, "interval": 0.8, "aggregate": "A",
                                  "partner": partner, "product": product}.items() if v is not None}
        data = self.get("/insights/forecast", **base)
        self._forecast[self._key(flow, partner, product)] = base   # the band, if drawn, uses the default
        bt = data.get("backtest") or {}
        methods = {m["method"]: m for m in bt.get("methods") or []}
        chosen = next((m["method"] for m in bt.get("methods") or [] if m.get("chosen")), data.get("method"))
        nn_note = None
        if "nn" not in methods:
            try:
                other = self.get("/insights/forecast", **base, method="nn")
                for m in (other.get("backtest") or {}).get("methods") or []:
                    methods.setdefault(m["method"], m)
            except InsightsError:
                nn_note = "the neural network generated by BESSER is not available on this backend"
        naive = methods.get("snaive") or {}

        def skill(m: dict, h: int) -> Optional[float]:
            a, b = m.get(f"mase_h{h}"), naive.get(f"mase_h{h}")
            return 1 - a / b if a is not None and b else None

        order = [chosen] + sorted((k for k in methods if k not in (chosen, "snaive")),
                                  key=lambda k: methods[k].get("mase") if methods[k].get("mase") is not None else 9)
        order += ["snaive"] if "snaive" in methods else []
        rows, bullets = [], []
        for k in order:
            m = methods[k]
            name = self.METHOD_NAMES.get(k, fmt.sentence(m.get("label") or k).capitalize())
            sk = {h: skill(m, h) for h in (1, 3, 6, 12)}
            row = {"method": name, "default": k == chosen,
                   "beats_naive_by": {f"{h} month{'s' if h > 1 else ''}": fmt.pct(v, signed=True)
                                      for h, v in sk.items() if v is not None},
                   "error_12_months_ahead": fmt.pct(m.get("mape_h12")) if m.get("mape_h12") is not None else None,
                   "band_held": fmt.pct(m.get("coverage")) if m.get("coverage") is not None else None}
            rows.append(row)
            tail = f"; off by {row['error_12_months_ahead']} 12 months ahead" if row["error_12_months_ahead"] else ""
            if k == "snaive":
                bullets.append(f"- {name} (the same month a year earlier): the benchmark{tail}")
            else:
                parts = [f"{fmt.pct(sk[h], signed=True)} at {h} month{'s' if h > 1 else ''}" for h in (1, 3, 6, 12)
                         if sk[h] is not None]
                bullets.append(f"- {name}{' (the default)' if k == chosen else ''}: " + ", ".join(parts) + tail)
        intro = ("How much smaller each method's errors were than the seasonal naive forecast's in the rolling "
                 "backtest (negative: larger), 1, 3, 6 and 12 months ahead:")
        out = {"default_method": self.METHOD_NAMES.get(chosen, chosen), "summary": bt.get("summary"),
               "tested": tested_span(bt), "two_tests": two_tests(bt),
               "table_intro": intro, "table": bullets, "methods": rows,
               "how_to_answer": "Name the default and why (why_the_default; its edge over the naive forecast), say "
                                "plainly where a method does not beat the naive forecast, and quote the table lines "
                                "as they are, one per line."}
        if nn_note:
            out["note"] = nn_note
        best = min((k for k in methods if k != "snaive"),
                   key=lambda k: methods[k].get("mase") if methods[k].get("mase") is not None else 9, default=chosen)
        out["why_the_default"] = (
            "It won the longer backtest that chose it (exports and imports, every quarter end since December 2009)."
            + ("" if best == chosen else
               f" On this series and these quarter ends, {_inline(self.METHOD_NAMES.get(best, best))} alone did "
               "slightly better on average: say so plainly; the default stays the one that won the longer test."))
        self.turn.backtest = {"intro": intro, "bullets": bullets, "default": out["default_method"]}
        return out

    @tool("Where the goods come from and go to in one period: top partners on each side and the balance.",
          {"period": PERIOD, "max_items": I("partners per side (default 6)")}, ["period"])
    def get_flows(self, period: str, max_items: int = 6) -> dict:
        data = self.get("/insights/flows", period=period, max_items=max_items)
        return summarize("/insights/flows", data, n=max_items)

    @tool("Luxembourg against the other EU countries (as reporters): value and growth since a base year.",
          {"period": PERIOD, "flow": FLOW, "base": S("base year for growth (default 1999, the first year of the data)"),
           "product": S("only this product")}, ["period", "flow"])
    def get_compare(self, period: str, flow: str, base: Optional[str] = None, product: Optional[str] = None) -> dict:
        base = self._base(base, self._start(None, product))
        data = self.get("/insights/compare/records", by="reporter", period=period, flow=flow, base=base,
                        product=product, limit=27)
        return summarize("/insights/compare/records", data)

    @tool("Specialisation: products where Luxembourg's share is far above the EU's (RCA > 1).",
          {"period": PERIOD, "flow": FLOW, "max_items": I("rows (default 6)")}, ["period", "flow"])
    def get_specialisation(self, period: str, flow: str, max_items: int = 6) -> dict:
        data = self.get("/insights/specialisation", by="product", period=period, flow=flow)
        return summarize("/insights/specialisation", data, n=max_items)

    @tool("The product tree of one period: chapters and what is inside them, with shares.",
          {"period": PERIOD, "flow": FLOW, "partner": S("only trade with this partner")}, ["period", "flow"])
    def get_tree(self, period: str, flow: str, partner: Optional[str] = None) -> dict:
        data = self.get("/insights/tree", by="product", period=period, flow=flow, depth=2, partner=partner)
        return summarize("/insights/tree", data)

    @tool("Codes by years (value, share, rank or growth), e.g. who were the biggest buyers each year.",
          {"by": S("partner or product", enum=["partner", "product"]), "start": PERIOD, "end": PERIOD,
           "flow": FLOW, "measure": S("value, share, rank or growth", enum=["value", "share", "rank", "growth"])},
          ["by", "start", "end", "flow"])
    def get_matrix(self, by: str, start: str, end: str, flow: str, measure: str = "rank") -> dict:
        data = self.get("/insights/matrix", by=by, start=start, end=end, flow=flow, measure=measure, max_items=8)
        return summarize("/insights/matrix", data)

    @tool("The typical year of the main chapters (seasonality): the month each one peaks and dips.",
          {"flow": FLOW, "top": I("chapters (default 8)")}, ["flow"])
    def get_seasonal(self, flow: str, top: int = 8) -> dict:
        data = self.get("/trends/seasonal-heatmap", flow=flow, freq="M", top=top)
        return summarize("/trends/seasonal-heatmap", data, n=top)

    @staticmethod
    def _key(flow: str, partner: Optional[str], product: Optional[str]) -> str:
        return f"{flow}|{partner or ''}|{product or ''}"

    # ======================================================================= act tools
    @tool("Set the page title (sentence case, no figures).",
          {"title": S(f"the new title, at most {TITLE_MAX} characters (two lines at the page's size)")}, ["title"])
    def set_title(self, title: str) -> dict:
        title = re.sub(r"\s+", " ", str(title or "")).strip()
        if len(title) > TITLE_MAX:
            raise ToolProblem(f"the title has {len(title)} characters: at most {TITLE_MAX} (two lines at 1440 px). "
                              "Shorten it, e.g. \"Luxembourg's trade in goods: where the 2025 export rise came from\"")
        title = _label(title, TITLE_MAX)
        return self.commit(lambda spec: ([self.view.set_title(title)] if spec.get("title") != title else [], []),
                           f"New title: {title}")

    @tool("Move the whole page to another year or month and/or goods (the user's Year and Goods controls): "
          "'show me 2020'. Panels that follow the page context redraw for it.",
          {"period": PERIOD, "product": S("TOTAL or an HS/CN8 code"),
           "flow": S("IMPORT, EXPORT or BOTH", enum=["IMPORT", "EXPORT", "BOTH"])})
    def set_context(self, period: Optional[str] = None, product: Optional[str] = None,
                    flow: Optional[str] = None) -> dict:
        if not (period or product or flow):
            raise ToolProblem("give a period, a product or a flow")

        def build(spec):
            ctx = self.context(spec)
            want = {k: v for k, v in (("period", period), ("product", product), ("flow", flow)) if v}
            if all(ctx.get(k) == v for k, v in want.items()):
                return [], []
            return [self.view.set_context(**want)], []
        out = self.commit(build, "The page moves to " + ", ".join(v for v in (period, product, flow) if v) + ".")
        if out.get("ok"):
            spec = self.turn.spec or self.spec()
            ctx = self.context(spec)
            follow = [p["id"] for p in spec.get("panels", []) if "$" in json.dumps(p.get("queries", []))]
            out["context_now"] = ctx
            out["panels_that_follow"] = follow
            if not follow:
                out["note"] = ("no panel on the page follows the context yet: add panels for that period "
                               "(show_panel with the period) so the move is visible")
        return out

    @tool("Rewrite the 'In short' headline of the page about a change between two years. Write the text "
          "with these placeholders only (never typed figures): {total_before} {total_now} {change} (absolute) "
          "{change_signed} {growth}; with product: {line_before} {line_now} {line_change} {line_growth} "
          "{line_share} (its share of the change) {growth_without} (growth of everything else); with product "
          "and partner: {partner_value} {partner_share} (share of the line's flow with that partner).",
          {"text": S("one or two short sentences with placeholders, at most 160 characters as shown (two lines)"),
           "flow": FLOW, "period": PERIOD,
           "previous": PERIOD, "product": S("the product line (HS/CN8 code)"),
           "partner": S("the main partner of that line")}, ["text", "flow", "period", "previous"])
    def write_headline(self, text: str, flow: str, period: str, previous: str, product: Optional[str] = None,
                       partner: Optional[str] = None) -> dict:
        vocab = self._change_vocab(flow, period, previous, product, partner)
        problems = share_base_problems(text)
        if problems:
            raise ToolProblem("; ".join(problems))
        facts = self.pick(text, vocab)
        shown = self._rendered(text, facts)
        if len(shown) > HEADLINE_MAX:
            self._headline_tries += 1
            if self._headline_tries < 2 or not product:
                raise ToolProblem(
                    f"the headline would be {len(shown)} characters on the page: at most {HEADLINE_MAX} (two lines at "
                    "1440 px). Write one or two short sentences, e.g. \"Exports rose {growth} in " + _year(period)
                    + "; one line made up {line_share} of the rise. Without it: {growth_without}.\"")
            # the second long one: the short sentence of the house, so the page still gets its headline
            text = self._short_headline(flow, period, product)
            facts = self.pick(text, vocab)
        ser = {"flow": flow, "freq": "A", "start": previous, "end": period}
        tone = "exports" if flow == "EXPORT" else "imports"
        headline = panel("headline", "text", "In short", [query("/insights/series", ser, key="total", tone=tone)],
                         text=text, facts=facts,
                         sources=[{"url": u} for u in self.turn.sources[-6:]] or [{"url": "/insights/series"}])

        def build(spec):
            if not self._can_upsert(spec, "headline"):
                raise ToolProblem("the headline is pinned by the user; leave it")
            ops = [self.view.upsert_panel(headline)]
            rows = [r for r in (spec.get("layout") or {}).get("rows", []) if r.get("panels")]
            if page.panel(spec, "headline") is None and rows:
                # the user removed the headline: it comes back at the top of the page, not under the charts
                ops.append(self.view.set_layout([{"panels": ["headline"]}] + rows))
            return ops, []
        return self.commit(build, f"Rewrote the headline: {text[:200]}")

    def _rendered(self, text: str, facts: dict) -> str:
        """A text as the page will show it, its placeholders filled (an unresolved one counts as '€0.00 bn')."""
        try:
            values, _ = self.resolve_facts(facts, {}, self.context())
        except Exception:  # the page or the API is down: count each placeholder as a typical figure
            values = {}
        return PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1)) or "€0.00 bn"), text or "")

    def _short_headline(self, flow: str, period: str, product: str) -> str:
        word = "Exports" if flow == "EXPORT" else "Imports"
        goods = self._names.get(str(product)) or "one product line"
        return (f"{word} moved {{growth}} in {_year(period)}; {goods} made up {{line_share}} of the change. "
                "Without that line: {growth_without}.")

    def _change_vocab(self, flow: str, period: str, previous: str, product: Optional[str],
                      partner: Optional[str]) -> dict:
        ser = {"flow": flow, "freq": "A", "start": previous, "end": period}
        v = {"total_before": fact_api("/insights/series", ser, "0.value", "eur"),
             "total_now": fact_api("/insights/series", ser, "-1.value", "eur"),
             "change_signed": fact_op("diff", "total_now", "total_before", "eur_signed"),
             "change": fact_op("abs", "change_signed", None, "eur"),
             "growth": fact_op("growth", "total_now", "total_before", "pct_signed")}
        if product:
            line = {**ser, "product": product}
            v.update({"line_before": fact_api("/insights/series", line, "0.value", "eur"),
                      "line_now": fact_api("/insights/series", line, "-1.value", "eur"),
                      "line_change": fact_op("diff", "line_now", "line_before", "eur_signed"),
                      "line_growth": fact_op("growth", "line_now", "line_before", "pct_signed"),
                      "line_share": fact_op("ratio", "line_change", "change_signed", "pct"),
                      "rest_now": fact_op("diff", "total_now", "line_now", "eur"),
                      "rest_before": fact_op("diff", "total_before", "line_before", "eur"),
                      "growth_without": fact_op("growth", "rest_now", "rest_before", "pct_signed")})
            if partner:
                rows = {"by": "partner", "period": period, "flow": flow, "product": product, "threshold": 0,
                        "max_items": 60}
                v["partner_value"] = fact_api("/insights/ranking", rows, f"items.[code={partner}].value", "eur")
                v["partner_share"] = fact_api("/insights/ranking", rows, f"items.[code={partner}].share", "pct")
        return v

    @tool("Draw a thread: one product line bought mostly from one partner and sold mostly to another, on the "
          "crossroads (or on a flowmap panel). Trade statistics do not link what was bought to what was sold again: "
          "never call it a route, a transit or goods passing through Luxembourg. Its note and label are written for "
          "you, with the shares on the right base: in_code's share of the line's imports, out_code's share of the "
          "line's exports, and the line's share of everything bought from in_code.",
          {"in_code": S("the partner the line is bought from most (ISO code)"),
           "out_code": S("the partner the line is sold to most"),
           "product": S("the product line (HS/CN8 code)"),
           "label": S("ignored: written for you, e.g. 'Smartphones: bought from China, sold to France'"),
           "note": S("ignored: the note is written for you"), "period": PERIOD,
           "target": S("crossroads (default) or a flowmap panel id"),
           "storyline": S("a name to group this mark with others under one number")},
          ["in_code", "out_code", "product", "label"])
    def add_thread(self, in_code: str, out_code: str, product: str, label: str, note: Optional[str] = None,
                   period: Optional[str] = None, target: str = "crossroads", storyline: Optional[str] = None) -> dict:
        if str(product).upper() == "TOTAL":
            raise ToolProblem("a thread follows one product line: give an HS or CN8 code, not TOTAL")
        in_code, out_code = str(in_code).upper(), str(out_code).upper()
        period = period or self.context()["period"]
        note = self.thread_note(in_code, out_code, product, label)
        label = self.thread_label(in_code, out_code, product, label)
        rows ={"by": "partner", "period": period, "product": product, "threshold": 0, "max_items": 60}
        vocab = {"bought": fact_api("/insights/ranking", {**rows, "flow": "IMPORT"}, f"items.[code={in_code}].value", "eur"),
                 "sold": fact_api("/insights/ranking", {**rows, "flow": "EXPORT"}, f"items.[code={out_code}].value", "eur"),
                 "bought_share": fact_api("/insights/ranking", {**rows, "flow": "IMPORT"},
                                          f"items.[code={in_code}].share", "pct"),
                 "sold_share": fact_api("/insights/ranking", {**rows, "flow": "EXPORT"},
                                        f"items.[code={out_code}].share", "pct"),
                 "from_in": fact_api("/insights/series", {"flow": "IMPORT", "freq": "A", "partner": in_code,
                                                          "start": period, "end": period}, "0.value", "eur"),
                 "in_share": fact_op("ratio", "bought", "from_in", "pct")}
        facts = self.pick(note, vocab)

        def build(spec):
            ops = []
            tid = target
            if page.panel(spec, tid) is None:
                # the crossroads (or the map asked for) is not on the page: the user removed it. The thread goes on
                # a flow chart already there, else on a new map of the routes, drawn in the same batch
                there = next((p_ for p_ in spec.get("panels", []) if p_["type"] in ("flow", "flowmap")), None)
                if there is not None:
                    tid = there["id"]
                else:
                    tid = "routes"
                    ops.append(self.view.upsert_panel(flowmap_panel(
                        tid, "Where Luxembourg's goods come from and go", flow="both", extent="world",
                        period="$period", max_items=10)))
            self._panel_or_problem({**spec, "panels": spec.get("panels", []) + [o["panel"] for o in ops]}, tid,
                                   {"flow", "flowmap"})
            op, item = self._mark_op(spec, "thread", tid, note,
                                     {"in_code": in_code, "out_code": out_code, "product": product,
                                      "label": _label(label)}, key=("in_code", "out_code", "product"),
                                     thread=_thread(storyline), facts=facts or None, period=period,
                                     what=f"thread: {label}")
            return ops + ([op] if op else []), [item]
        out = self.commit(build, f"Thread: {label}")
        if out.get("ok") and target == "crossroads" and self.turn.spec is not None \
                and page.panel(self.turn.spec, "crossroads") is None:
            out["drawn_on"] = ("the crossroads is not on the page, so the thread is on the map of partners: "
                               "call it 'the map of partners' in the reply")
        if out.get("ok"):   # the shares the page now shows, with their base, for the check of the reply
            goods = note.split(" of the ", 1)[1].split(" Luxembourg bought", 1)[0] if " of the " in note else "line"
            shown = next(iter((out.get("figures_on_page") or {}).values()), {})
            for key, code, flow in (("bought_share", in_code, "IMPORT"), ("sold_share", out_code, "EXPORT")):
                v = _parse_pct(shown.get(key))
                if v is not None:
                    self.turn.shares.append(line_share(v, self.country(code), goods, flow))
        return out

    @staticmethod
    def country(code: str) -> str:
        name = {**COUNTRY_NAMES, "UA": "Ukraine", "CA": "Canada"}.get(str(code).upper(), str(code).upper())
        return ("the " + name) if name.startswith(("United ", "Netherlands", "Czech Republic")) else name

    def thread_goods(self, product: str, label: str = "") -> str:
        """The goods of a thread in words ('smartphones'), from the names read in this conversation, else the
        model's label, else the chapter's short name."""
        goods = self._names.get(str(product))
        if not goods:
            head = re.split(r",|:|\s+from\s+|\s+through\s+|\s+via\s+|\s+bought\s+", label or "", maxsplit=1)[0].strip()
            goods = goods_words(head) if head and not re.search(r"\d", head) else \
                fmt.sentence(HS_SHORT.get(str(product)[:2], "")) if len(str(product)) == 2 else ""
        return goods or "goods of this line"

    def thread_label(self, in_code: str, out_code: str, product: str, label: str = "") -> str:
        """'Smartphones: bought from China, sold to France': what the data shows, never a route through Luxembourg
        (trade statistics do not link what was bought to what was sold again)."""
        goods = self.thread_goods(product, label)
        return _label(f"{goods[:1].upper() + goods[1:]}: bought from {self.country(in_code)}, sold to "
                      f"{self.country(out_code)}")

    def thread_note(self, in_code: str, out_code: str, product: str, label: str = "") -> str:
        """The thread's note, always the same sentence so each share has its right base: in_code's share of
        the LINE's imports, out_code's share of the LINE's exports, the line's share of all imports from
        in_code ('Smartphones made up 81% of everything Luxembourg bought from China')."""
        goods = self.thread_goods(product, label)
        src, dst = self.country(in_code), self.country(out_code)
        cap = lambda s: s[:1].upper() + s[1:]  # noqa: E731
        return (f"{cap(src)} supplied {{bought_share}} of the {goods} Luxembourg bought ({{bought}}); {dst} took "
                f"{{sold_share}} of those it sold ({{sold}}). {cap(goods)} made up {{in_share}} of everything "
                f"Luxembourg bought from {src}.")

    def trend_text(self, aq: dict) -> tuple[str, dict]:
        """The text under a month-by-month heatmap: the analysis's own plain sentence ('Over the span the trend
        rose, but it rose more slowly in the second half than in the first.') and its seasonality, as facts. An
        older backend without trend_summary: its direction and shape."""
        try:
            data = self.get(aq["endpoint"], **(aq.get("params") or {}))
        except (InsightsError, KeyError, TypeError):
            data = {}
        if isinstance(data, dict) and data.get("trend_summary"):
            return ("{trend_summary} {seasonality}",
                    {"trend_summary": fact_from("analysis", "trend_summary", "text"),
                     "seasonality": fact_from("analysis", "seasonality_summary", "text")})
        return ("Over the span the trend is {direction} and {shape}. {seasonality}",
                {"direction": fact_from("analysis", "direction", "text"),
                 "shape": fact_from("analysis", "shape", "text"),
                 "seasonality": fact_from("analysis", "seasonality_summary", "text")})

    def note_what(self, spec: dict, target: str, x: Any) -> str:
        """'note on electrical machinery, on "What drove the 2025 export rise"': the point and the chart in words."""
        p = page.panel(spec, target) or {}
        x = str(x)
        point = (HS_SHORT.get(x, "").lower() or self._names.get(x) or products.name_of(x).lower()) if x.isdigit() \
            and len(x) in (2, 4, 6, 8) and not re.fullmatch(r"(?:19|20)\d\d", x) else \
            (fmt.period_words(x) if re.fullmatch(r"\d{4}(?:-\d{2})?", x) else COUNTRY_NAMES.get(x.upper(), x))
        chart = p.get("title") or "the chart"
        return f"note on {point}, on “{chart}”"

    @tool("Draw trend breaks (change points of analyze_trend) on a month-by-month heatmap or a monthly line. "
          "Call analyze_trend first with the same flow/partner/product: 'at' must be one of its change points. "
          "Without target, the breaks go on a month-by-month heatmap of that series, added if missing. "
          "Note placeholders: {before} and {after} (typical month before and after), {change} (relative).",
          {"breaks": L("the breaks to draw", {"type": "object", "properties": {
              "at": S("a change point period, e.g. 2009-01"),
              "label": S("short label of what the level did, e.g. 'Step down, 2009' (no causes: that is the cause "
                         "finder's job)"),
              "note": S("one sentence with placeholders")}, "required": ["at", "label", "note"]}),
           "flow": FLOW, "partner": S("as in analyze_trend"), "product": S("as in analyze_trend"),
           "target": S("a heatmap (month_year or product_year) or line panel id"),
           "storyline": S("group name (default 'breaks')")},
          ["breaks", "flow"])
    def add_trend_breaks(self, breaks: list, flow: str, partner: Optional[str] = None, product: Optional[str] = None,
                         target: Optional[str] = None, storyline: Optional[str] = None) -> dict:
        storyline = storyline or ("breaks" if self.agent == "trend_finder" else f"{self.agent}_breaks")
        key = self._key(flow, partner, product)
        params = self._analysis.get(key)
        if params is None:
            self.analyze_trend(flow, partner=partner, product=product)
            params = self._analysis[key]
        data = self.get("/trends/analyze", **params)
        points = {c["period"] for c in data.get("changepoints") or []}
        bad = [b.get("at") for b in breaks if b.get("at") not in points]
        if bad:
            raise ToolProblem(f"{', '.join(map(str, bad))} not among the change points {sorted(points)} of "
                              f"/trends/analyze with {params}")
        aq = query("/trends/analyze", params)
        word = "exports" if flow == "EXPORT" else "imports"
        auto_id = _slug(word, partner, product, "months")
        monthly = params.get("freq", "M") == "M"
        moved = None
        if target is not None and monthly:
            # monthly change points on a chart of yearly totals would put a monthly level on a yearly axis
            tp = page.panel(self.spec(), target)
            if tp is not None and tp["type"] == "line" and all(
                    (q.get("params") or {}).get("freq", "A") != "M" for q in tp.get("queries", [])):
                moved, target = target, None
        built = []
        for b in breaks:
            at = b["at"]
            cp = f"changepoints.[period={at}]"
            note = str(b.get("note") or "")
            if typed_or_empty(note):   # typed figures, or none: the house's note, the figures as placeholders
                note = ("A typical month went from {before} to {after}, {change}." if monthly
                        else "From {before} to {after}, {change}.")
            if monthly and re.search(r"\{(before|after)\}", note) and "month" not in note.lower():
                note = re.sub(r"\{after\}", "{after} a month", note, count=1) if "{after}" in note else                     re.sub(r"\{before\}", "{before} a month", note, count=1)
            need_figure(note, f"trend break {at}")
            b = {**b, "note": note}
            vocab = {"before": fact_from("self", f"{cp}.before_mean", "eur"),
                     "after": fact_from("self", f"{cp}.after_mean", "eur"),
                     "change": fact_from("self", f"{cp}.relative_change", "pct_signed")}
            built.append((b, self.pick(b["note"], vocab)))

        def build(spec):
            ops, items = [], []
            tid = target or auto_id
            if target is None and page.panel(spec, tid) is None:
                filters = {k: v for k, v in (("partner", partner), ("product", product)) if v}
                who = " with " + partner if partner else ""
                what = f" of {product}" if product else ""
                hp = heatmap_panel(tid, _label(f"{word.capitalize()}{what}{who} month by month, since "
                                              f"{_year(params.get('start', '1999'))}", 160),
                                   kind="month_year", flow=flow, start=params.get("start"), end=params.get("end"),
                                   subtitle="One row per year, one column per month; darker is more", **filters)
                hp["queries"].append({**aq, "key": "analysis"})
                hp["text"], hp["facts"] = self.trend_text(aq)
                ops.append(self.view.upsert_panel(hp))
            elif target is not None:
                p = self._panel_or_problem(spec, tid, {"heatmap", "line"})
                if p["type"] == "heatmap" and (p.get("options") or {}).get("kind") == "seasonal":
                    raise ToolProblem("a seasonal heatmap (the typical year) has no time axis and takes no marks: "
                                      "say so plainly in the reply")
                if p["type"] == "heatmap" and (p.get("options") or {}).get("kind") == "month_year":
                    main = next((q.get("params") or {} for q in p.get("queries", []) if q.get("key") == "main"), {})
                    if (main.get("flow"), main.get("partner"), main.get("product")) != (flow, partner, product):
                        raise ToolProblem(f"{tid!r} shows {main.get('flow')} (partner {main.get('partner')}, product "
                                          f"{main.get('product')}): analyse and mark that same series")
            for b, facts in built:
                params_m = {"at": b["at"], "label": _label(b["label"])}
                if facts:
                    params_m.update({k: k for k in ("before", "after") if k in facts})
                op, item = self._mark_op(spec, "trend_break", tid, b["note"], params_m, key=("at",),
                                         thread=_thread(storyline), query_=aq, facts=facts or None,
                                         what=f"trend break {b['at']} on {tid}")
                if op:
                    ops.append(op)
                items.append(item)
            return ops, items
        out = self.commit(build, f"Trend breaks in {word}: " + ", ".join(b["at"] for b in breaks))
        if moved and out.get("ok"):
            out["note"] = (f"the breaks are monthly levels, so they went on the month-by-month grid {auto_id!r}, "
                           f"not on {moved!r}, which shows yearly totals")
        return out

    @tool("Draw dated events on a line of the yearly series (the 'trend' line, or a long line since 1999 "
          "added if the events are older than the trend line). ONLY events get_events returned, by event_id (their "
          "date and span come from the list; the list starts in 2017, so older moves have no event). Placeholders, "
          "all for the event's year against the year before: {exp_change} {imp_change} (growth of ALL of "
          "Luxembourg's exports / imports, every product and partner), {exp_now} {imp_now} {exp_before} "
          "{imp_before} (those totals): ONLY for broad events (the pandemic, a war, a crisis). An event about one "
          "product or partner (get_events shows its about_line and line_that_year: US steel tariffs, sanctions on "
          "Russia, the EU-UK agreement) is drawn with ITS line, computed from the event's own scope: {line_change} "
          "(growth) {line_diff} (euros) {line_now} {line_before}, and {line_exp_change} {line_imp_change} when it "
          "concerns both flows; {exp_change} and {imp_change} are refused on it, and in the reply quote the line's "
          "change, not all exports. Use neutral verbs ('exports moved {exp_change}', 'imports changed by "
          "{imp_change}') unless you know the sign. Dates line up, they do not prove a cause: never write "
          "'caused', 'led to', 'due to'.",
          {"events": L("the events", {"type": "object", "properties": {
              "event_id": S("the id from get_events"), "label": S("short label (default: the list's)"),
              "note": S("one or two sentences with placeholders"),
              "product": S("only to narrow the event's own scope to one HS/CN8 code (for the {line_...} figures)"),
              "partner": S("only to narrow the event's own scope to one partner (for the {line_...} figures)"),
              "flow": S("EXPORT (default) or IMPORT, for the {line_...} figures", enum=["EXPORT", "IMPORT"])},
              "required": ["event_id", "note"]}),
           "target": S("a line panel id (default: chosen for you)"),
           "storyline": S("group name (default 'events')")}, ["events"])
    def add_events(self, events: list, target: Optional[str] = None, storyline: str = "events") -> dict:
        curated = self.get("/insights/events", start="1999")
        curated = curated.get("items") if isinstance(curated, dict) else curated
        self._note_events_from(curated)
        by_id = {str(c.get("id")): c for c in curated or []}
        built = []
        for e in events:
            # only events from the curated list: an event the model remembers is not on the page's record
            c = by_id.get(str(e.get("event_id") or "")) or next(
                (c for c in by_id.values() if str(c.get("label", "")).lower() == str(e.get("label", "")).lower()), None)
            if c is None:
                raise ToolProblem(f"{e.get('label') or e.get('at')!r} is not in the curated list of events (its ids: "
                                  f"{', '.join(sorted(by_id))}). Draw only events get_events returned, with their "
                                  "event_id; when no event fits a move, say so in the reply instead")
            e = {**e, "event_id": c["id"], "at": str(c["at"])[:7], "label": e.get("label") or c.get("label")}
            if c.get("until"):
                e["until"] = str(c["until"])[:7]
            # the line the event is about, from the model's product/partner or the event's own scope (US steel
            # tariffs: iron and steel, articles of iron or steel and aluminium exported to the US)
            line = self.event_line(c, e)
            at = str(e["at"])[:7]
            if not re.fullmatch(r"\d{4}(-\d{2})?", at):
                raise ToolProblem(f"'at' must be YYYY or YYYY-MM, not {e['at']!r}")
            if e.get("until") and str(e["until"])[:7] <= at:
                e = {k: v for k, v in e.items() if k != "until"}   # 'from March 2021 to March 2021'
            year, prev = _year(at), _prev_year(at)
            vocab = {}
            for f, k in (("EXPORT", "exp"), ("IMPORT", "imp")):
                ps = {"flow": f, "freq": "A", "start": prev, "end": year}
                vocab[f"{k}_before"] = fact_api("/insights/series", ps, "0.value", "eur")
                vocab[f"{k}_now"] = fact_api("/insights/series", ps, "-1.value", "eur")
                vocab[f"{k}_change"] = fact_op("growth", f"{k}_now", f"{k}_before", "pct_signed")
            note = e["note"].strip()
            if typed_or_empty(note):   # typed figures, or none: the house's note, the figures as placeholders
                note = (f"{line['label'][:1].upper() + line['label'][1:].split(' (HS')[0]} moved {{line_change}} "
                        "({line_diff}) that year." if line else
                        "Exports moved {exp_change} and imports {imp_change} that year.")
            if line:
                vocab.update(self._line_vocab(line, year, prev))
                totals = sorted({u for u in PLACEHOLDER_RE.findall(note) if u.startswith(("exp_", "imp_"))})
                if totals or not re.search(r"\{line_", note):
                    # a figure of ALL trade under an event about one line reads as that line's own (the steel
                    # tariffs note that said "exports moved +8.6%"): only the line's figures go on such an event
                    try:
                        seen = self.line_figures(line, year)
                    except (InsightsError, KeyError, TypeError, ValueError):
                        seen = {}
                    raise ToolProblem(
                        f"{e.get('label') or at!r} is about {line['label']}, not all trade: "
                        + (f"{', '.join('{' + t + '}' for t in totals)} would quote total exports/imports. "
                           if totals else "")
                        + "write its note with the line's own change as PLACEHOLDERS (never typed figures, never "
                          "'led to'): {line_change} (growth) and {line_diff} (euros)"
                        + (", {line_exp_change} {line_imp_change} for each flow" if line["both_flows"] else "")
                        + f", e.g. note: \"{line['label'][:1].upper() + line['label'][1:].split(' (HS')[0]} moved "
                          "{line_change} ({line_diff}) that year.\". "
                        + f"For your reply only, the line in {year}: {json.dumps(seen, ensure_ascii=False)}; quote "
                          "those, not the change of all exports.")
            no_causal_words(note, f"event {e.get('label') or at}")
            need_figure(note, f"event {e.get('label') or at}")
            if "proof" not in note.lower():
                note += " The dates line up; that is not proof of cause."
            e = {**e, "note": note}
            built.append((e, at, self.pick(note, vocab)))
            if line:
                self._remember_scoped(e, line, year)
        first = min(_year(at) for _, at, _ in built)

        def build(spec):
            ops, items = [], []
            tid = target
            if tid is None:
                trend = page.trend_panel(spec)
                start = None
                if trend:
                    starts = [(q.get("params") or {}).get("start") for q in trend.get("queries", [])]
                    start = min((s for s in starts if s), default=None)
                if trend and (start is None or _year(start) <= first):
                    tid = trend["id"]
                else:
                    tid = "history"
                    if page.panel(spec, tid) is None:
                        ops.append(self.view.upsert_panel(self.history_panel()))
            else:
                self._panel_or_problem(spec, tid, {"line"})
            for e, at, facts in built:
                params = {"at": at, "label": _label(e["label"])}
                if e.get("until"):
                    params["until"] = str(e["until"])[:7]
                if e.get("event_id"):
                    params["event_id"] = re.sub(r"[^a-z0-9_-]", "-", str(e["event_id"]).lower())[:64]
                op, item = self._mark_op(spec, "event", tid, e["note"], params, key=("at", "label"),
                                         thread=_thread(storyline), facts=facts or None,
                                         what=f"event {at} {e['label']} on {tid}")
                if op:
                    ops.append(op)
                items.append(item)
            return ops, items
        out = self.commit(build, "Events that line up with the series: " + ", ".join(e["label"] for e, _, _ in built)
                          + ". Dates that match, not proof of cause.")
        if out.get("ok"):
            out["trend_breaks"] = self.measure_events([(e["label"], at) for e, at, _ in built])
        return out

    @staticmethod
    def history_panel() -> dict:
        """Imports and exports since 1999, a line of the page's kind: where events, a band or a trend line go
        when the page's own trend line is not there (the user removed it, or it starts later)."""
        span = {"freq": "A", "start": "1999", "end": "$period"}
        return panel("history", "line", "Imports and exports since 1999",
                     [query("/insights/series", {"flow": "IMPORT", **span}, key="imports", label="Imports",
                            tone="imports"),
                      query("/insights/series", {"flow": "EXPORT", **span}, key="exports", label="Exports",
                            tone="exports")],
                     subtitle="€, nominal; Luxembourg reports on its own from 1999",
                     options={"x": "period", "y": "value", "format": "eur", "gap_fill": True})

    # ---- events against the trend breaks
    def known_breaks(self, spec: Optional[dict] = None) -> list[str]:
        """The trend breaks to line events up with: those on the page, else the change points of exports and
        imports since 1999 (/trends/analyze, as the trend finder computes them)."""
        try:
            spec = spec or self.spec()
        except Exception:
            spec = {}
        found = sorted({str((m.get("params") or {}).get("at"))[:7] for m in spec.get("marks", [])
                        if m.get("kind") == "trend_break" and (m.get("params") or {}).get("at")})
        if found:
            return found
        out: set = set()
        for flow in ("EXPORT", "IMPORT"):
            params = self._analysis.get(self._key(flow, None, None)) or {
                "flow": flow, "freq": "M", "start": f"{FIRST_YEAR}-01", "penalty": 1.5, "min_size": 12}
            try:
                data = self.get("/trends/analyze", **params)
            except InsightsError:
                continue
            out |= {str(c["period"])[:7] for c in data.get("changepoints") or []}
        return sorted(out)

    def measure_events(self, events: list[tuple[str, str]]) -> dict:
        """Each event's nearest trend break and how many months apart: an event 'lines up with a break' only
        within 12 months of one. Recorded on the turn, so the reply can be checked."""
        breaks = self.known_breaks(self.turn.spec)
        rows = []
        for label, at in events:
            near = min(breaks, key=lambda b: abs(months_between(b, at)), default=None)
            months = abs(months_between(near, at)) if near else None
            rows.append({"label": label, "at": at, "nearest_break": near, "months_apart": months,
                         "lines_up_with_a_break": months is not None and months <= 12,
                         **({"say": f"{label} ({fmt.period_words(at)}) is {months} months from the break of "
                                    f"{fmt.period_words(near)}"} if near else {})})
        self.turn.event_breaks = rows
        self.turn.breaks = breaks
        return {"breaks": [fmt.period_words(b) for b in breaks], "events": rows,
                "rule": "Say that an event lines up with a trend break ONLY when lines_up_with_a_break is true (a break "
                        "within 12 months). For the others say plainly that no break is near them. Name each break "
                        "and event by month and year in words (December 2021), never 2021-12. Next to a broad event "
                        "(no about_line) give no yearly change of all trade: that change is the whole year's, not the "
                        "event's effect; an event about one line may cite its line_that_year change."}

    @tool("Draw the forecast band on a line (the trend line by default) from get_forecast (call it first with "
          "the same flow/partner/product), and optionally a short 'What comes next' text under it. Note "
          "placeholders: {value} {low} {high} (next year), {error} {naive_error} (last full year's error of the "
          "method and of the seasonal naive forecast), {error_12m} {naive_error_12m} (average error 12 months "
          "ahead), {skill_1m} {skill_3m} {skill_6m} {skill_12m} (how much smaller its errors are than the naive's at "
          "1, 3, 6 and 12 months ahead: use the one that matches the horizon you write), {band_held} (share of "
          "months the band held), {origins} (quarter ends in the rolling backtest), {observed} {observed_months} (months "
          "already in), {last} (last full year), and when get_forecast listed scenarios {continues} {stops} "
          "(the year if the smartphone line stays at its recent level, or stops) with {line_level} (its recent "
          "level a month) and {line_since} (when it started). The figures are those of the year the note names "
          "(the first forecast year by default). scenarios=true (default when there are any) also draws "
          "the two scenarios as labelled points at the end of the band.",
          {"flow": FLOW, "note": S("one or two sentences with placeholders"), "partner": S("partner code"),
           "product": S("product code"), "target": S("a line panel id (default: the trend line)"),
           "outlook": S("optional text for a 'What comes next' panel, with the same placeholders plus {method}"),
           "scenarios": B("draw the scenarios at the end of the band (default true when there are any)")},
          ["flow", "note"])
    def add_forecast_band(self, flow: str, note: str, partner: Optional[str] = None, product: Optional[str] = None,
                          target: Optional[str] = None, outlook: Optional[str] = None,
                          scenarios: Optional[bool] = None) -> dict:
        asked = product
        partner, product = self._fc_redirect.get(self._key(flow, partner, product), (partner, product))
        if asked and product != asked:   # the band is the forecast of another series: its words name that one
            pre = products.predecessor(str(asked))
            goods = (pre or {}).get("now") or (self._names.get(str(asked)) or "").split(" ")[0]
            if (goods and goods.lower().rstrip("s") in (note + " " + (outlook or "")).lower()) or str(asked) in note:
                what = products.chapter_name(product, lower=False) if product else \
                    ("All exports" if flow == "EXPORT" else "All imports")
                raise ToolProblem(f"this band is the forecast of {what}, not of {goods or asked}: name it so in the note "
                                  f"and the outlook (e.g. \"{what} in 2026: {{value}}, between {{low}} and {{high}}\")")
        key = self._key(flow, partner, product)
        params = self._forecast.get(key)
        if params is None:
            self.get_forecast(flow, partner=partner, product=product)
            partner, product = self._fc_redirect.get(key, (partner, product))
            key = self._key(flow, partner, product)
            params = self._forecast[key]
        data = self.get("/insights/forecast", **params)
        rows = data.get("rows") or []
        ahead = [r for r in rows if r.get("low") is not None] or rows[-1:]
        if not ahead:
            raise ToolProblem("the forecast came back empty")
        named = [y for y in re.findall(r"\b20\d\d\b", f"{note} {outlook or ''} {self.question}")
                 if any(str(r["period"])[:4] == y for r in ahead)]
        asked = self.asked_year()
        if asked and asked > str(ahead[-1]["period"])[:4] and not named:
            named = [str(ahead[-1]["period"])[:4]]   # 'exports in 2030': the furthest year the forecast reaches
        row = next((r for r in ahead if named and str(r["period"])[:4] == named[0]), ahead[0])
        other = [y for y in re.findall(r"\b20\d\d\b", note) if y != str(row["period"])[:4]
                 and any(str(r["period"])[:4] == y for r in ahead)]
        if other:
            raise ToolProblem(f"the band shows {str(row['period'])[:4]} (the year asked, or the furthest the forecast "
                              f"reaches): write the note for {str(row['period'])[:4]}, not {other[0]}")
        year = str(row["period"])[:4]
        far = [y for y in re.findall(r"\b20\d\d\b", note) if y > str(ahead[-1]["period"])[:4]]
        if far:
            raise ToolProblem(f"the forecast drawn reaches {str(ahead[-1]['period'])[:4]}, not {far[0]}: write the "
                              f"note for {year} (its figures are {year}'s)")
        sel = f"rows.[period={row['period']}]"
        fq = query("/insights/forecast", params)
        last_year = _prev_year(year)
        actual = {"flow": flow, "freq": "A", "start": last_year, "end": last_year,
                  **{k: v for k, v in (("partner", partner), ("product", product)) if v}}
        vocab = {"value": fact_from("self", f"{sel}.value", "eur"), "low": fact_from("self", f"{sel}.low", "eur"),
                 "high": fact_from("self", f"{sel}.high", "eur"),
                 "error": fact_from("self", "backtest.mape", "pct"),
                 "naive_error": fact_from("self", "backtest.naive_mape", "pct"),
                 "observed": fact_from("self", f"{sel}.observed", "eur"),
                 "observed_months": fact_from("self", f"{sel}.observed_months", "int"),
                 "last": fact_api("/insights/series", actual, "0.value", "eur"),
                 "interval": fact_from("self", "interval", "pct")}
        bt = data.get("backtest") or {}
        for name, path, f in (("error_12m", "mape_h12", "pct"), ("naive_error_12m", "naive_mape_h12", "pct"),
                              ("skill_1m", "skill_h1", "pct"), ("skill_3m", "skill_h3", "pct"),
                              ("skill_6m", "skill_h6", "pct"), ("skill_12m", "skill_h12", "pct"),
                              ("band_held", "coverage", "pct"), ("summary", "summary", "text"),
                              ("origins", "origins", "int")):
            if bt.get(path) is not None:
                vocab[name] = fact_from("self", f"backtest.{path}", f)
        has_scenarios = False
        for s in data.get("scenarios") or []:
            if s.get("case") in ("stops", "continues") and any(str(r.get("period")) == str(row["period"])
                                                                for r in s.get("rows") or []):
                vocab[s["case"]] = fact_from("self", f"scenarios.[id={s['id']}].rows.[period={row['period']}].value",
                                             "eur")
                if s.get("recent_extra") is not None:
                    vocab["line_level"] = fact_from("self", f"scenarios.[id={s['id']}].recent_extra", "eur")
                if s.get("since"):
                    vocab["line_since"] = fact_from("self", f"scenarios.[id={s['id']}].since", "period")
                has_scenarios = True
        draw_scenarios = has_scenarios if scenarios is None else bool(scenarios and has_scenarios)
        word_ = "exports" if flow == "EXPORT" else "imports"
        note, outlook = tidy_outlook(note), tidy_outlook(outlook) if outlook else outlook
        if outlook:   # led by the figure and its interval; the method in plain words; the scenarios as the line's level
            outlook = plain_outlook(outlook, word_, year, has_scenarios and "line_level" in vocab
                                    and "line_since" in vocab and draw_scenarios)
        for text in (note, outlook):
            horizon_problem(text or "")
        facts = self.pick(note, vocab)
        word = "exports" if flow == "EXPORT" else "imports"
        out_facts = None
        if outlook:
            ovocab = {k: (fact_from("forecast", v["path"], v.get("format")) if v.get("query") == "self" else v)
                      for k, v in vocab.items()}
            ovocab["method"] = fact_from("forecast", "method_label", "text")
            out_facts = self.pick(outlook, ovocab)
        outlook_id = _slug("outlook", word if word != "exports" else None, partner, product) or "outlook"

        want = products.product_codes([product]) if product else []

        def fits(p_: Optional[dict]) -> bool:
            """A line of the very series forecast (the band of chapter 85 never sits on the smartphone line)."""
            if not p_ or p_.get("type") != "line" or self._panel_codes(p_) != want:
                return False
            parts = {str((q.get("params") or {}).get("partner") or "") for q in p_.get("queries", [])}
            return parts == {partner or ""}

        def build(spec):
            ops, items = [], []
            tp = self._panel_or_problem(spec, target, {"line"}) if target else None
            if tp is not None and not fits(tp):
                tp = None            # a chart of another series: the band goes on a line of its own
            if tp is None and not (partner or product):
                tp = page.trend_panel(spec) or page.panel(spec, "history")
                if tp is None:   # the user removed the trend line: the band goes on a line of its own
                    tp = self.history_panel()
                    ops.append(self.view.upsert_panel(tp))
            if tp is None:
                tp = next((p_ for p_ in reversed(spec.get("panels", [])) if fits(p_)), None)
            if tp is None and product and not partner:
                name = products.chapter_name(product, lower=False) if len(str(product)) == 2 else f"HS {product}"
                tp = self._build_panel("line", f"{name}: {word} and the forecast", None, None, flow, None, None,
                                       None, None, None, "A", None, product, None, None, None, None, None, None,
                                       None, None, None, None, None)
                if not self._can_upsert(spec, tp["id"]):
                    raise ToolProblem(f"the line {tp['id']!r} is pinned: give the forecast in words")
                ops.append(self.view.upsert_panel(tp))
            if tp is None:
                raise ToolProblem("no line panel for this series on the page: give target, or ask the visualizer "
                                  "(show_panel line) first")
            series = word if word in {q["key"] for q in tp.get("queries", [])} else None
            # a band this agent drew before on the same line is replaced, not stacked
            for m in spec.get("marks", []):
                if (m.get("agent") == self.agent and m["kind"] == "forecast" and m["target"] == tp["id"]
                        and m.get("series") == series and m.get("status") == "proposed"
                        and (m.get("query") or {}).get("params") != params):
                    ops.append(self.view.remove_mark(m["id"]))
            mparams = {"label": f"{year} forecast", **({"scenarios": True} if draw_scenarios else {})}
            op, item = self._mark_op(spec, "forecast", tp["id"], note, mparams, key=("label",),
                                     series=series, thread=_thread(f"outlook_{word}"), query_=fq, facts=facts or None,
                                     what=f"{year} band for {word} on {tp['id']}")
            if op:
                ops.append(op)
            items.append(item)
            if outlook and self._can_upsert(spec, outlook_id):
                ops.append(self.view.upsert_panel(panel(
                    outlook_id, "text", "What comes next",
                    [{**fq, "key": "forecast", "tone": word}], text=outlook, facts=out_facts,
                    sources=[{"url": u} for u in self.turn.sources[-4:]] or [{"url": "/insights/forecast"}]),
                    after=tp["id"] if page.panel(spec, outlook_id) is None else None))
            return ops, items
        return self.commit(build, f"A {year} band for {word}, with the backtest error said out loud.")

    @tool("Put a numbered note on one point of a line or compare chart (x = a period), a bar or a waterfall "
          "(x = a row code). Placeholders in the note: {value} (the point's value); on a line or compare chart "
          "also {<key>_at} (each series at x), {<key>_before} (the same period a year before x), {<key>_at_change} "
          "(x against the same period a year before: say 'than a year earlier'; never a trend or a change over the "
          "span), {<key>_now} (last value shown), {<key>_then} (first value shown), {<key>_change} "
          "(growth over the WHOLE span shown, e.g. 1999 to 2025, not on the year before), {<key>_yoy} (the last "
          "value against the one before) (key = the series' query key, e.g. exports, imports, lu, be); on exports "
          "against imports also {balance} (the last year) and {balance_at} (at x); on a line of a quantity "
          "(quantity units or weight) {value} is the count or the tonnes alone (write the word after it: '{value} "
          "cars'), {value_eur} its value in euros and {per_unit} the euros per unit (or tonne); plus any you define "
          "in 'figures'.",
          {"target": S("panel id"), "x": S("a period (line) or a row code (bar, waterfall)"),
           "label": S("short label"), "note": S("one or two sentences; figures only as placeholders"),
           "series": S("on a line with several queries, the query key (exports, imports)"),
           "storyline": S("group name"), "period": PERIOD,
           "figures": L(FIGURES_DOC, {"type": "object"})},
          ["target", "x", "label", "note"])
    def add_annotation(self, target: str, x: str, label: str, note: str, series: Optional[str] = None,
                       storyline: Optional[str] = None, period: Optional[str] = None,
                       figures: Optional[list] = None) -> dict:
        def build(spec):
            p = self._panel_or_problem(spec, target, contract().validate.MARK_ON["annotation"])
            qs = {q["key"]: q for q in p.get("queries", [])}
            ser = series if series in qs else (None if len(qs) == 1 else next(iter(qs), None))
            vocab = {**self._series_vocab(p, at=str(x)), **self._figures(figures)}
            q = qs.get(ser) if ser else next(iter(qs.values()), None)
            if q:
                ctx = self.context(spec)
                params = contract().validate.fill_params(q.get("params") or {}, ctx)
                path = None
                if q["endpoint"] == "/insights/series":
                    path = f"[period={x}].value"
                elif q["endpoint"] in ("/insights/ranking/records", "/insights/compare/records",
                                       "/insights/specialisation/records"):
                    path = f"[code={x}].value"
                elif q["endpoint"] == "/insights/ranking":
                    path = f"items.[code={x}].value"
                elif q["endpoint"] == "/insights/movers":
                    data = self.get(q["endpoint"], **params)
                    side = next((s for s in ("up", "down", "new") if any(i["code"] == x for i in data.get(s) or [])), None)
                    path = f"{side}.[code={x}].change" if side else None
                if path:
                    vocab.setdefault("value", fact_api(q["endpoint"], q.get("params") or {}, path,
                                                       "eur_signed" if q["endpoint"] == "/insights/movers" else "eur"))
            at_change_problem(note)
            facts = self.pick(note, vocab)
            need_figure(note, "note")
            # a note is about one year: the point it sits on, or the year of the bars, waterfall or snapshot
            op, item = self._mark_op(spec, "annotation", target, note, {"x": str(x)[:16], "label": _label(label)},
                                     key=("x",), series=ser if self._timeline(p) else None,
                                     thread=_thread(storyline), facts=facts or None,
                                     period=self._about(spec, p, period, str(x)),
                                     what=self.note_what(spec, target, x))
            return ([op] if op else []), [item]
        return self.commit(build, f"Note on {target}: {label}")

    @staticmethod
    def _timeline(p: dict) -> bool:
        """A panel with a time axis: a line, or a compare panel of series over time (contract 1.2)."""
        if p["type"] == "line":
            return True
        layout = getattr(contract().validate, "compare_layout", None)
        return p["type"] == "compare" and layout is not None and layout(p) == "series"

    def _about(self, spec: dict, p: dict, period: Optional[str] = None, x: Optional[str] = None) -> Optional[str]:
        """The year (or month) a mark on panel ``p`` is about, so the page dims it when it shows another year:
        the one the model gave; on a time axis the point ``x`` it sits on (a span has none); else the period of the
        panel's own queries (the later one of two snapshots), '$period' read from the page's context. A panel of
        many years (a bump, a heatmap) gives none."""
        if period:
            return period
        if self._timeline(p):
            return str(x) if x and PERIOD_RE_LOCAL.match(str(x)) else None
        ctx = self.context(spec)
        found = None
        for q in p.get("queries", []):
            v = (q.get("params") or {}).get("period")
            if v:
                v = contract().validate.fill_params({"period": v}, ctx).get("period")
                if v and PERIOD_RE_LOCAL.match(str(v)) and (found is None or str(v) > found):
                    found = str(v)
        return found

    @staticmethod
    def _series_vocab(p: dict, at: Optional[str] = None, span: Optional[tuple] = None) -> dict:
        """Placeholders a mark on a line or compare panel can use, read from the panel's own series:
        {<key>_now} (last value), {<key>_then} (first), {<key>_change} (growth over the whole span, first to
        last), {<key>_prev} (the value before the last) and {<key>_yoy} (last against the one before); with
        ``at`` {<key>_at}; with ``span`` {<key>_from}, {<key>_to}, {<key>_span_change}. Also {value} for one
        series."""
        vocab: dict = {}
        series = [q for q in p.get("queries", []) if q["endpoint"] == "/insights/series"]
        for q in series:
            k, prm = q["key"], q.get("params") or {}
            # A month against another month of another season is no measure of direction (one measure for a span:
            # the trend's change, conversation.CONSISTENT): a monthly line offers no first-to-last change, no
            # change over a span and no 'yoy' of one month against the month before.
            monthly = str(prm.get("freq") or "A").upper() == "M"
            vocab[f"{k}_now"] = fact_api("/insights/series", prm, "-1.value", "eur")
            vocab[f"{k}_then"] = fact_api("/insights/series", prm, "0.value", "eur")
            vocab[f"{k}_prev"] = fact_api("/insights/series", prm, "-2.value", "eur")
            if not monthly:
                vocab[f"{k}_change"] = fact_op("growth", f"{k}_now", f"{k}_then", "pct_signed")
                vocab[f"{k}_yoy"] = fact_op("growth", f"{k}_now", f"{k}_prev", "pct_signed")
            # Facts about a named period read that period alone, so they stay right when the page moves to
            # another year (the panel's own end may be $period).
            def one(p_: str) -> dict:
                return {**prm, "start": p_, "end": p_}
            if at and PERIOD_RE_LOCAL.match(at):
                before = f"{int(at[:4]) - 1:04d}{at[4:]}"
                vocab[f"{k}_at"] = fact_api("/insights/series", one(at), "0.value", "eur")
                vocab[f"{k}_before"] = fact_api("/insights/series", one(before), "0.value", "eur")
                vocab[f"{k}_at_change"] = fact_op("growth", f"{k}_at", f"{k}_before", "pct_signed")
            if prm.get("measure") in ("units", "weight"):
                # a line of a quantity (contract 1.8): its value in euros too, and the euros per unit (or tonne)
                vocab[f"{k}_eur_now"] = fact_api("/insights/series", prm, "-1.value_eur", "eur")
                if at and PERIOD_RE_LOCAL.match(at):
                    vocab[f"{k}_eur_at"] = fact_api("/insights/series", one(at), "0.value_eur", "eur")
                    vocab[f"{k}_per_unit_at"] = fact_op("ratio", f"{k}_eur_at", f"{k}_at", "eur")
            if span and all(PERIOD_RE_LOCAL.match(str(s)) for s in span):
                vocab[f"{k}_from"] = fact_api("/insights/series", one(span[0]), "0.value", "eur")
                vocab[f"{k}_to"] = fact_api("/insights/series", one(span[1]), "0.value", "eur")
                if not monthly:
                    vocab[f"{k}_span_change"] = fact_op("growth", f"{k}_to", f"{k}_from", "pct_signed")
        if {"exports_now", "imports_now"} <= set(vocab):   # exports against imports: the gap between them
            vocab["balance"] = fact_op("diff", "exports_now", "imports_now", "eur_signed")
            if "exports_at" in vocab and "imports_at" in vocab:
                vocab["balance_at"] = fact_op("diff", "exports_at", "imports_at", "eur_signed")
        if len(series) == 1 and at and f"{series[0]['key']}_at" in vocab:
            vocab["value"] = vocab[f"{series[0]['key']}_at"]
            if f"{series[0]['key']}_eur_at" in vocab:   # a quantity: {value} 37,654 (cars), {value_eur} €827 m
                vocab["value_eur"] = vocab[f"{series[0]['key']}_eur_at"]
                vocab["per_unit"] = fact_op("ratio", "value_eur", "value", "eur")
        return vocab

    def _code_vocab(self, spec: dict, p: dict, codes: list, period: Optional[str] = None) -> dict:
        """Placeholders a highlight of codes can use, read from the panel's own rankings, in the mark's year:
        {<code>_value}, {<code>_share} (one ranking: choropleth, bar) and, where the panel shows both sides
        (the crossroads, a butterfly, a flow map), {<code>_bought}, {<code>_bought_share} (imports from it),
        {<code>_sold}, {<code>_sold_share} (exports to it). Code in lower case: de_value, fr_bought_share."""
        if not codes:
            return {}
        ctx = dict(self.context(spec))
        if period:
            ctx["period"] = period
        vocab: dict = {}
        rankings = [q for q in p.get("queries", []) if q.get("endpoint") == "/insights/ranking"]
        # Two sides (the crossroads 'in' and 'out', a butterfly): one ranking per flow, so a code's figures are
        # {xx_bought} (its share of imports) and {xx_sold} (of exports). One {xx_value} would read one side only.
        two_sided = p.get("type") in ("butterfly", "flow") or \
            len({(q.get("params") or {}).get("flow") for q in rankings}) > 1
        for q in p.get("queries", []):
            ep = q.get("endpoint")
            prm = contract().validate.fill_params(q.get("params") or {}, ctx)
            for c in [str(c) for c in codes][:8]:
                k = code_key(c)
                if ep == "/insights/ranking" and two_sided:
                    word = "bought" if (q.get("params") or {}).get("flow") == "IMPORT" else "sold"
                    vocab[f"{k}_{word}"] = fact_api("/insights/ranking", prm, f"items.[code={c}].value", "eur")
                    vocab[f"{k}_{word}_share"] = fact_api("/insights/ranking", prm, f"items.[code={c}].share", "pct")
                elif ep == "/insights/ranking":
                    vocab[f"{k}_value"] = fact_api("/insights/ranking", prm, f"items.[code={c}].value", "eur")
                    vocab[f"{k}_share"] = fact_api("/insights/ranking", prm, f"items.[code={c}].share", "pct")
                elif ep == "/insights/flows":
                    for side, word in (("out", "sold"), ("in", "bought")):
                        vocab[f"{k}_{word}"] = fact_api(ep, prm, f"{side}.items.[code={c}].value", "eur")
                        vocab[f"{k}_{word}_share"] = fact_api(ep, prm, f"{side}.items.[code={c}].share", "pct")
                elif ep == "/insights/tree":           # treemap tiles: a chapter, or a heading inside it
                    path = f"children.[code={c}]" if len(c) <= 2 else f"children.[code={c[:2]}].children.[code={c}]"
                    vocab[f"{k}_value"] = fact_api(ep, prm, f"{path}.value", "eur")
                    vocab[f"{k}_share"] = fact_api(ep, prm, f"{path}.share", "pct")
                elif ep == "/insights/ranking/records":   # bars
                    vocab[f"{k}_value"] = fact_api(ep, prm, f"[code={c}].value", "eur")
                    vocab[f"{k}_share"] = fact_api(ep, prm, f"[code={c}].share", "percent")
                elif ep == "/insights/trends":            # growth bars (1.7): growth per year since start
                    vocab[f"{k}_growth"] = fact_api(ep, prm, f"items.[code={c}].growth", "pct_signed")
                    vocab[f"{k}_then"] = fact_api(ep, prm, f"items.[code={c}].start_value", "eur")
                    vocab[f"{k}_now"] = fact_api(ep, prm, f"items.[code={c}].value", "eur")
                elif ep == "/insights/compare/records":   # the EU countries' growth
                    vocab[f"{k}_value"] = fact_api(ep, prm, f"[code={c}].value", "eur")
                    vocab[f"{k}_growth"] = fact_api(ep, prm, f"[code={c}].growth", "percent_signed")
                elif ep == "/insights/specialisation":    # scatter bubbles
                    vocab[f"{k}_value"] = fact_api(ep, prm, f"items.[code={c}].value", "eur")
                    vocab[f"{k}_share"] = fact_api(ep, prm, f"items.[code={c}].share", "pct")
                    vocab[f"{k}_eu_share"] = fact_api(ep, prm, f"items.[code={c}].peer_share", "pct")
                    vocab[f"{k}_rca"] = fact_api(ep, prm, f"items.[code={c}].rca", "number")
                elif ep == "/insights/matrix":            # bump ranks, product_year heatmap
                    fmt_ = {"rank": "ordinal", "share": "pct", "growth": "pct_signed"}.get(prm.get("measure"), "eur")
                    vocab[f"{k}_then"] = fact_api(ep, prm, f"rows.[code={c}].values.0", fmt_)
                    vocab[f"{k}_now"] = fact_api(ep, prm, f"rows.[code={c}].values.-1", fmt_)
                elif ep == "/insights/movers":            # waterfall steps, the growth of scatter bubbles
                    try:
                        data = self.get(ep, **prm)
                    except InsightsError:
                        continue
                    side = next((s for s in ("up", "down", "new") if any(i.get("code") == c
                                                                         for i in data.get(s) or [])), None)
                    if side:
                        vocab[f"{k}_change"] = fact_api(ep, prm, f"{side}.[code={c}].change", "eur_signed")
                        if side != "new":
                            vocab[f"{k}_growth"] = fact_api(ep, prm, f"{side}.[code={c}].growth", "pct_signed")
        return vocab

    def _figures(self, figures: Optional[list]) -> dict:
        out = {}
        for f in figures or []:
            if not isinstance(f, dict) or not f.get("name") or not f.get("endpoint") or not f.get("path"):
                raise ToolProblem("each figure needs name, endpoint, params, path (and format)")
            out[f["name"]] = fact_api(f["endpoint"], f.get("params") or {}, f["path"], f.get("format"))
        return out

    @tool("Highlight codes on a panel (bars, countries on a map, tiles, rows, bubbles, flows) or a span of "
          "periods on a line or compare chart (from_period/to_period). On a line or compare chart the note may use "
          "{<key>_from}, {<key>_to}, {<key>_span_change} (the span highlighted), {<key>_now}, {<key>_then}, "
          "{<key>_change} (the whole span shown, first to last), {<key>_yoy} (last against the one before) (key = "
          "the series' query key: exports, imports, lu, be, ...). Highlighted codes come with {<code>_value} "
          "{<code>_share} on a one-sided panel (choropleth, bar), and on a two-sided one (the crossroads, a "
          "butterfly, a flow map) {<code>_bought} {<code>_bought_share} (imports from it) and {<code>_sold} "
          "{<code>_sold_share} (exports to it); code in lower case: {de_value}, {be_bought_share}. On the "
          "crossroads say 'bought from' with the _bought figures and 'sold to' with the _sold ones. Tiles of a "
          "treemap: {<code>_value} {<code>_share}; bubbles of a scatter: {<code>_rca} {<code>_share} "
          "{<code>_eu_share} {<code>_growth} {<code>_change}; rows of a bump or product_year heatmap: "
          "{<code>_then} {<code>_now} (first and last year shown); steps of a waterfall: {<code>_change} "
          "{<code>_growth}; the EU countries' bars: {<code>_growth}. A product code gets 'hs' in front: {hs85_value}. "
          "Always put at least one figure in the note.",
          {"target": S("panel id"), "note": S("one sentence; figures only as placeholders"),
           "codes": L("codes to highlight"), "from_period": PERIOD, "to_period": PERIOD,
           "label": S("short label"), "storyline": S("group name"), "period": PERIOD,
           "figures": L(FIGURES_DOC, {"type": "object"})},
          ["target", "note"])
    def highlight(self, target: str, note: str, codes: Optional[list] = None, from_period: Optional[str] = None,
                  to_period: Optional[str] = None, label: Optional[str] = None, storyline: Optional[str] = None,
                  period: Optional[str] = None, figures: Optional[list] = None) -> dict:
        if not codes and not (from_period and to_period):
            raise ToolProblem("give codes, or from_period and to_period on a line")

        def build(spec):
            p = self._panel_or_problem(spec, target, contract().validate.MARK_ON["highlight"])
            params: dict = {}
            if self._timeline(p):
                if not (from_period and to_period):
                    raise ToolProblem(f"{target!r} has a time axis: highlight a span with from_period and to_period")
                params = {"from": from_period, "to": to_period}
            else:
                if not codes:
                    raise ToolProblem(f"on a {p['type']}, highlight codes")
                params = {"codes": [str(c) for c in codes][:20]}
            vocab = {**self._series_vocab(p, span=(from_period, to_period) if from_period and to_period else None),
                     **self._code_vocab(spec, p, codes or [], period),
                     **self._figures(figures)}
            facts = self.pick(note, vocab)
            need_figure(note, "highlight")
            if label:
                params["label"] = _label(label)
            # codes on a ranking, map, treemap, ...: the figures are of the panel's year (the page's year when it
            # follows the page), so the mark says so and steps back ("about 2025") when the page moves on. A span
            # on a time axis is about no single year.
            when = period
            if when is None and codes and not self._timeline(p):
                when = self._about(spec, p)
            op, item = self._mark_op(spec, "highlight", target, note, params, key=("codes", "from", "to"),
                                     thread=_thread(storyline), facts=facts or None, period=when,
                                     what=f"highlight on {target}")
            return ([op] if op else []), [item]
        return self.commit(build, f"Highlight on {target}" + (f": {label}" if label else ""))

    @tool("Add a fitted trend line (log-linear) over a span to a line panel.",
          {"target": S("line panel id (default trend)"), "flow": FLOW, "start": START, "end": PERIOD,
           "note": S("one sentence; placeholder {growth} = growth per year")}, ["flow", "note"])
    def add_trend_line(self, flow: str, note: str, target: str = "trend", start: Optional[str] = None,
                       end: Optional[str] = None) -> dict:
        start, end = self._start(start), self._end(end)
        if "{growth" in (note or "") and not PER_YEAR_RE.search(note or ""):
            # 15 Oct: 'The fitted trend fell {growth} over the span' showed 7.4% (a year) next to a reply's 23.6%
            # (over the span): the figure is a rate per year and the note must say so
            raise ToolProblem("{growth} is the fitted growth PER YEAR, not over the span: write it as such, e.g. "
                              "'The fitted trend changed {growth} a year.'")
        facts = self.pick(note, {"growth": fact_from("self", "cagr", "pct_signed")})
        word = "exports" if flow == "EXPORT" else "imports"

        def build(spec):
            ops, tid = [], target
            if page.panel(spec, tid) is None and tid == "trend":
                # the user removed the trend line, or means the chart already on screen for this flow
                # (a partner/product-filtered line the visualizer drew): attach there before falling back
                # to the baseline imports+exports panel, or drawing one.
                there = (page.last_agent_line_panel(spec, word) or page.trend_panel(spec)
                         or page.panel(spec, "history"))
                if there is None:
                    there = self.history_panel()
                    ops.append(self.view.upsert_panel(there))
                tid = there["id"]
            p = page.panel(spec, tid) or (ops[0]["panel"] if ops else None)
            if p is None or p["type"] != "line":
                p = self._panel_or_problem(spec, tid, {"line"})
            series_q = next((q for q in p.get("queries", []) if q["key"] == word), None)
            series = word if series_q else None
            # Match the chart it attaches to: its own partner/product/frequency, not just the flow total.
            inherited = {k: v for k, v in (series_q or {}).get("params", {}).items()
                         if k in ("partner", "product", "freq")}
            tparams = {"flow": flow, "start": start, **({"end": end} if end else {}), **inherited}
            tparams.setdefault("freq", "A")
            tq = query("/insights/trend", tparams)
            op, item = self._mark_op(spec, "trend", tid, note, {"label": f"Trend of {word} since {start}",
                                                                   "start": start, **({"end": end} if end else {})},
                                     key=("start",), series=series, query_=tq, facts=facts or None,
                                     what=f"trend line of {word} since {start}")
            return ops + ([op] if op else []), [item]
        return self.commit(build, f"Trend line of {word} since {start}")

    PANEL_TYPES = ["line", "bar", "table", "waterfall", "choropleth", "flowmap", "heatmap", "treemap", "butterfly",
                   "bump", "scatter"]
    ONE_YEAR = {"bar", "table", "waterfall", "choropleth", "flowmap", "treemap", "butterfly", "scatter"}

    @tool("Add (or replace, same id) a chart on the page. Types: line (both flows of a partner/product over "
          "time; or reporters=[LU, BE] to compare countries), bar (a ranking by partner or product; by=reporter "
          "for the EU countries' growth since base; measure='growth' with start: each partner's or product's "
          "growth per year since start, the fastest-growing first), table (the same ranking as rows: name, value, share; for 'as a "
          "table'), waterfall (what made up a change between previous and "
          "period), choropleth (partners on a map; measure share|value|balance), flowmap (arcs from Luxembourg "
          "to its partners), heatmap (kind month_year | product_year | seasonal), treemap (the goods as nested "
          "tiles), butterfly (imports left, exports right), bump (ranks over the years), scatter (specialisation "
          "x growth). Periods left out follow the page (the user's year). Returns what the chart shows, with "
          "figures, and the panel id to put marks on.",
          {"type": S("chart type", enum=PANEL_TYPES), "title": S("sentence case, no figures"),
           "id": S("panel id (snake_case). Leave it out: the default id re-uses your chart of the same kind "
                   "(the map stays the map). The same id re-draws that chart in its place; never a pinned id"),
           "subtitle": S("short subtitle"),
           "flow": S("EXPORT, IMPORT or BOTH", enum=["EXPORT", "IMPORT", "BOTH"]),
           "by": S("partner, product or reporter", enum=["partner", "product", "reporter"]),
           "period": PERIOD, "previous": PERIOD, "start": START, "end": PERIOD,
           "freq": S("A or M (line)", enum=["A", "M"]), "partner": S("partner code filter"),
           "product": S("product code filter"), "reporters": L("countries to compare on a line (LU first)"),
           "level": I("product level 1-4"), "parent": S("children of this code"),
           "measure": S("choropleth: share|value|balance; heatmap product_year: share|value|growth; bar: growth "
                        "(growth per year since start, by partner or product)",
                        enum=["share", "value", "balance", "growth", "rank"]),
           "extent": S("map extent", enum=["europe", "world"]),
           "kind": S("heatmap kind", enum=["month_year", "product_year", "seasonal"]),
           "depth": I("treemap depth 1-3"), "max_items": I("rows / partners / codes"),
           "x": S("scatter x field (rca, share, peer_share, value, growth, change)"),
           "y": S("scatter y field"), "base": S("bar by reporter: base year for growth (default 1999, the first year of the data)"),
           "after": S("put it after this panel id"),
           "quantity": S("line or bar: units (the count in the goods' own unit, for one heading or line) or weight "
                         "(tonnes) instead of euros; table: all (value, quantity and value per unit side by side)",
                         enum=["units", "weight", "all"]),
           "unit_word": S("with quantity units: what is counted, plural ('cars'), when the unit is a number of items")},
          ["type", "title"])
    def show_panel(self, type: str, title: str, id: Optional[str] = None, subtitle: Optional[str] = None,
                   flow: Optional[str] = None, by: Optional[str] = None, period: Optional[str] = None,
                   previous: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                   freq: Optional[str] = None, partner: Optional[str] = None, product: Optional[str] = None,
                   reporters: Optional[list] = None, level: Optional[int] = None, parent: Optional[str] = None,
                   measure: Optional[str] = None, extent: Optional[str] = None, kind: Optional[str] = None,
                   depth: Optional[int] = None, max_items: Optional[int] = None, x: Optional[str] = None,
                   y: Optional[str] = None, base: Optional[str] = None, after: Optional[str] = None,
                   quantity: Optional[str] = None, unit_word: Optional[str] = None) -> dict:
        q = self.question
        if show_year(q) and self._page_follows_year():
            raise ToolProblem(f"this question only moves the page to {show_year(q)}: set_context, then highlight on "
                              "the crossroads. The page's charts follow the year by themselves; add no chart")
        if self.agent != "trend_finder" and type == "waterfall" and "trend_finder" in self.others:
            raise ToolProblem("the trend finder draws the change between the two years in this turn: draw no "
                              "waterfall, answer only your own part")
        growth = self._growth_bar_asked(type, by, measure, start, f"{title} {subtitle or ''}")
        if growth:
            # growth per year over a span (contract 1.7): a bar of /insights/trends, never one year's values
            type, measure, by = "bar", "growth", by if by in ("partner", "product") else "partner"
            end = end or (period if period and _year(period) in years_in(q) else None)
            period = previous = None
        if (by or "product") == "product" and self.deep(parent, level) and type in self.ONE_YEAR and not growth:
            # the page draws one level below a parent: the CN8 lines of an HS4 heading go through its HS6 lines
            yr = _year(str(period)) if period else self.last_year()
            kids = self.children(parent, int(level), yr, flow if flow in ("EXPORT", "IMPORT") else "EXPORT", partner)
            if int(level) - level_of(parent) == 2 and len(kids) == 1:
                parent = kids[0]            # one HS6 line holds them all: draw its CN8 lines
            else:
                names = ", ".join(kids[:8]) or "none with recorded trade"
                raise ToolProblem(f"the page draws the lines just below a code: for level {level} under {parent}, "
                                  f"draw parent=<one of its lines one level down>, level {level} (they are: {names}); "
                                  "or get_ranking(by='product', parent, level, partner) for all of them at once. Keep "
                                  "the partner filter of the question.")
        if type == "bar" and TABLE_RE.search(q) and not growth:
            type = "table"          # 'as a table': the same ranking, as rows
        if type == "line" and not reporters and YOY_RE.search(q):
            raise ToolProblem("the user asks for year-on-year growth: use compare(what='partners' (or 'products'), "
                              "codes=[every partner or product named], mode='yoy') instead of a line of values")
        if type == "line" and not reporters and GAP_RE.search(q):
            raise ToolProblem("the user asks for the gap: use compare(what='flows') for exports against imports (the "
                              "shade is the balance), or compare(what='partners', codes=[...], mode='diff')")
        if type in ("bar", "table") and by != "reporter" and not growth \
                and GROWTH_TITLE_RE.search(f"{title} {subtitle or ''}"):
            raise ToolProblem("a bar or a table of one year ranks by value, so it cannot be titled as growth or change. "
                              "For growth over several years ('fastest-growing since 1999') use type='bar', "
                              "measure='growth', start=<the first year> (by partner or product: each one's growth per "
                              "year since then); for the change between two years use type='waterfall' (by, level, "
                              "parent, period, previous: its steps are the biggest changes in euros); or title the "
                              "bar by what it shows (the largest lines)")
        if type == "treemap" and any(c and str(c).upper() != "TOTAL" for c in (product, parent)):
            raise ToolProblem("a treemap shows all the goods, never one chapter: for what is inside a chapter use "
                              f"show_panel type='bar', by='product', parent='{product or parent}' (its headings), "
                              "with a title that names the chapter by its short name")
        if type in ("bar", "table", "line") and TWO_WAYS_RE.search(q):
            raise ToolProblem("bought against sold is a butterfly: show_panel type='butterfly', by='partner' (or "
                              "'product' for chapters)")
        if type in ("line", "bump", "heatmap") and end and _year(end) not in years_in(q):
            end = None              # over time, the chart ends at the page's year unless the user named the end
        if type in self.ONE_YEAR and not growth and period and _year(period) not in years_in(q):
            # a one-year chart follows the page's year unless the user named that year
            title = _drop_year(title, _year(period)) or title
            subtitle = _drop_year(subtitle, _year(period)) if subtitle else subtitle
            period, previous = None, None
        if type in self.ONE_YEAR and not growth and not period:
            title = _drop_span(title) or title       # nor a span of years it cannot show
            subtitle = _drop_span(subtitle) if subtitle else subtitle
            for y in years_in(title) - years_in(q):   # it follows the page: its title names no year of its own
                title = _drop_year(title, y) or title
        pre = products.predecessor(str(product)) if product else None
        chained = bool(type == "line" and pre and not reporters and (freq or "A") == "A" and not quantity
                       and _year(str(self._start(start, product))) <= pre["until"])
        if chained:
            p = self._chained_line(title, id, subtitle, flow, partner, str(product), pre, start)
        else:
            p = self._build_panel(type, title, id, subtitle, flow, by, period, previous, start, end, freq, partner,
                                  product, reporters, level, parent, measure, extent, kind, depth, max_items, x, y,
                                  base)
            if quantity:
                p = self._with_quantity(p, quantity, unit_word, id)
        why = self._empty_why(p, product)
        if why:
            raise ToolProblem(why)
        if p["id"] in page.BASELINE_PANELS:
            p["id"] = f"{p['id']}_{type}"   # one of the page's own ids: the chart gets one of its own, silently
        if id is None:   # re-draw this agent's own chart of the same kind in its place rather than add one
            again = self._own_chart_of_kind(p)
            if again:
                p["id"] = again
        retitled = self._title_fits(p, title)
        if retitled:
            p["title"] = title = retitled

        def build(spec):
            twin = next((q for q in spec.get("panels", []) if q["id"] != p["id"] and q["type"] == p["type"]
                         and q.get("queries") == p.get("queries") and q.get("options") == p.get("options")), None)
            if twin:   # another agent already drew this very chart in this turn or before: point at it, add none
                p["id"] = twin["id"]
                return [], []
            ops = []
            old = page.panel(spec, p["id"])
            if old is not None and old["type"] == p["type"] and old.get("queries") == p.get("queries") \
                    and old.get("options") == p.get("options"):
                return [], []          # the page shows this very chart already
            on_it = [m for m in spec.get("marks", []) if m["target"] == p["id"]]
            if old is not None and (old.get("pinned") or (old.get("type") != p["type"]
                                                          and any(m.get("status") == "kept" for m in on_it))):
                # the user pinned that chart, or kept a note on it: it stays, the new chart gets an id of its own
                # (never a refusal the reply would have to explain)
                taken = {q["id"] for q in spec.get("panels", [])}
                p["id"] = next(f"{p['id']}_{n}" for n in range(2, 100) if f"{p['id']}_{n}" not in taken)
                old, on_it = None, []
            if old and old.get("type") != p["type"]:
                # another kind of chart in the same place: its marks go (they were drawn for the old one)
                ops += [self.view.remove_mark(m["id"]) for m in on_it]
            ops.append(self.view.upsert_panel(p, after=after if after and page.panel(spec, after) else None))
            return ops, []
        out = self.commit(build, f"Added a {type}: {title}")
        if out.get("ok") and chained and out.get("changed") is not False:   # the change of code, marked once
            key = f"{'imports' if flow == 'IMPORT' else 'exports'}_from_{pre['from']}"
            note = self.add_annotation(p["id"], pre["from"], f"New code from {pre['from']}",
                                       f"From {pre['from']} {pre['now']} have their own code, {pre['level']} {product}: "
                                       "{value} that year; before, they were filed with other " + pre["goods"] + ".",
                                       series=key)
            out["code_change"] = {"say": pre["say"] + ".", "marks": note.get("marks")}
            out["marks"] = (out.get("marks") or []) + (note.get("marks") or [])
        if out.get("ok"):
            out["panel"] = p["id"]
            out["shows"] = self._panel_summary(p)
            if any(str(v).startswith("$") for q_ in p.get("queries", []) for v in (q_.get("params") or {}).values()):
                out["follows_the_page_year"] = True
            out["marks_it_takes"] = self._marks_on(p)
        return out

    def _growth_bar_asked(self, type: str, by: Optional[str], measure: Optional[str], start: Optional[str],
                          words: str) -> bool:
        """Whether show_panel draws growth per year over a span (a growth bar, contract 1.7): a bar or table by partner
        or product asked with measure='growth', or titled as growth ('Fastest-growing partners') over a span: a start
        given, a start in the question ('since 1999'), or a span in the title. A bar titled as growth with no span
        stays refused (one year's values are not growth)."""
        if type not in ("bar", "table") or by == "reporter":
            return False
        if measure == "growth":
            return True
        if not GROWTH_TITLE_RE.search(words):
            return False
        q = self.question or ""
        return bool(start or self.asked.get("start") or GROWTH_SPAN_RE.search(words)
                    or (GROWTH_TITLE_RE.search(q) and GROWTH_SPAN_RE.search(q)))

    def _chained_line(self, title: str, id: Optional[str], subtitle: Optional[str], flow: Optional[str],
                      partner: Optional[str], product: str, pre: dict, start: Optional[str]) -> dict:
        """One line of goods whose code changed: the old code until the revision, the new one after it, as two
        series clearly labelled ('Exports, mobile phones (CN8 85171200), to 2021')."""
        first = self._start(start, product)
        flows = [flow] if flow in ("EXPORT", "IMPORT") else ["EXPORT", "IMPORT"]
        filt = {"partner": partner} if partner else {}
        qs = []
        for f in flows:
            word = "exports" if f == "EXPORT" else "imports"
            qs.append(query("/insights/series", {"flow": f, "freq": "A", "start": first, "end": pre["until"],
                                                 "product": pre["before"], **filt},
                            key=f"{word}_to_{pre['until']}", tone=word,
                            label=f"{word.capitalize()}, {pre['goods']} ({pre['level']} {pre['before']}), to {pre['until']}"))
            qs.append(query("/insights/series", {"flow": f, "freq": "A", "start": pre["from"], "end": "$period",
                                                 "product": product, **filt},
                            key=f"{word}_from_{pre['from']}", tone=word,
                            label=f"{word.capitalize()}, {pre['now']} ({pre['level']} {product}), from {pre['from']}"))
        sub = subtitle or (f"{pre['level']} {pre['before']} until {pre['until']}, {product} from {pre['from']} "
                           f"(the code changed in {pre['from']})")
        return panel(id or _slug("viz_line", partner, product, "chained"), "line", _label(title, 160), qs,
                     subtitle=sub, options={"x": "period", "y": "value", "format": "eur"})

    def _empty_why(self, p: dict, product: Optional[str]) -> Optional[str]:
        """Why a chart would draw nothing (every query empty), or None: a code that did not exist yet, no trade,
        or data not loaded. Read with the page's year, as the page will."""
        try:
            ctx = self.context(self.spec())
        except Exception:
            return None
        empty, statuses = [], []
        for q in p.get("queries", []):
            try:
                data = self.get(q["endpoint"], **contract().validate.fill_params(q.get("params") or {}, ctx))
            except InsightsError:
                return None   # the page reports its own errors
            if isinstance(data, dict):
                statuses.append(data.get("loaded"))
            empty.append(_no_rows(q["endpoint"], data))
        if not empty or not all(e is True for e in empty):
            return None
        if any(s is False for s in statuses):
            return ("nothing to draw: this slice is not loaded and the source could not be reached just now; say so "
                    "plainly and try again later, or draw the chapter")
        why = "nothing to draw: the API has no data for this chart"
        if product:
            prm = next((q.get("params") or {} for q in p.get("queries", []) if q["endpoint"] == "/insights/series"),
                       {})
            hist = self.code_history(str(product), prm.get("flow") or "EXPORT", prm.get("start"), None,
                                     partner=prm.get("partner"))
            if hist:
                why += f" ({hist['say']})"
        return why + ". Draw no empty chart: say why in the reply and offer what there is."

    def _page_follows_year(self) -> bool:
        """Whether the crossroads is on the page and follows its year (so 'show me 2020' needs no new chart). False
        when the user removed it: then the year's partners are shown with a new chart."""
        try:
            spec = self.spec()
        except Exception:
            return True
        return any(p["type"] == "flow" and "$" in json.dumps(p.get("queries", [])) for p in spec.get("panels", []))

    PARTNER_TITLE_RE = re.compile(r"\b(?:destinations?|buyers?|suppliers?|partners?|markets?|countries|customers?|"
                                  r"clients?|where\b[^.]{0,40}\b(?:go(?:es)?|come(?:s)?\s+from|sold|bought)|"
                                  r"by\s+(?:partner|country|destination))\b", re.I)
    PARTNER_FILTER_TITLE_RE = re.compile(r"\b(?:sold\s+to|bought\s+from|exports?\s+to|imports?\s+from)\b", re.I)
    PRODUCT_TITLE_RE = re.compile(r"\bby\s+(?:product|chapter|heading|line|goods)s?\b|\b(?:headings|chapters|"
                                  r"subheadings|product\s+lines)\b", re.I)

    def _title_fits(self, p: dict, title: str) -> Optional[str]:
        """A title that says what the chart shows, when the model's does not: a ranking by product titled with
        partners ('Main destinations for steel exports' over steel's headings), or a ranking by partner titled
        with products. None when the title fits."""
        if p["type"] not in ("bar", "table", "waterfall", "treemap", "bump", "heatmap"):
            return None
        q = next((q for q in p.get("queries", []) if (q.get("params") or {}).get("by")), None)
        if q is None:
            return None
        prm = q.get("params") or {}
        by = str(prm.get("by"))
        partner, product, parent = prm.get("partner"), prm.get("product"), prm.get("parent")
        word = "imports" if str(prm.get("flow") or "EXPORT").upper() == "IMPORT" else "exports"
        if by == "product":
            wrong = self.PARTNER_TITLE_RE.search(title or "") or (
                not partner and self.PARTNER_FILTER_TITLE_RE.search(title or ""))
        elif by == "partner":
            wrong = self.PRODUCT_TITLE_RE.search(title or "")
        else:
            return None
        if not wrong:
            return None
        goods_code = str(parent or (product if str(product or "").upper() != "TOTAL" else "") or "")
        goods = (self._names.get(goods_code) or products.name_of(goods_code)) if goods_code else ""
        goods = goods[:1].lower() + goods[1:] if goods and not goods[:2].isupper() else goods
        if by == "product":
            where = (f" {'to' if word == 'exports' else 'from'} {self.country(str(partner))}" if partner else "")
            new = (f"{goods[:1].upper() + goods[1:]} {word} by product{where}" if goods
                   else f"Luxembourg's {word} by product{where}")
        else:
            new = (f"Where {goods} {word} {'go' if word == 'exports' else 'come from'}" if goods
                   else f"Where Luxembourg's {word} {'go' if word == 'exports' else 'come from'}")
        log.info("retitled %r to %r (the chart is by %s)", title, new, by)
        return _label(new, 160)

    def _own_chart_of_kind(self, p: dict) -> Optional[str]:
        """The id of this agent's chart of the same kind already on the page (not pinned, not drawn in this
        turn), the most recent first: the page keeps three agent charts, so a new map re-draws the old map."""
        try:
            spec = self.spec()
        except Exception:
            return None
        def kind_of(o: Optional[dict]) -> tuple:
            return (o or {}).get("kind"), (o or {}).get("measure") == "growth"
        kind = kind_of(p.get("options"))
        kept = {m["target"] for m in spec.get("marks", []) if m.get("status") == "kept"}
        same = [q for q in spec.get("panels", []) if q.get("agent") == self.agent and q["type"] == p["type"]
                and q["id"] not in page.BASELINE_PANELS and not q.get("pinned") and q["id"] not in kept
                and q["id"] not in self.turn.panels and kind_of(q.get("options")) == kind]
        if any(q["id"] == p["id"] for q in same):
            return p["id"]
        if same:
            return same[-1]["id"]
        taken = {q["id"] for q in spec.get("panels", []) if q.get("pinned") or q["id"] in kept}
        if p["id"] in taken:   # the default id is a chart the user kept: the new one gets an id of its own
            return next(f"{p['id']}_{n}" for n in range(2, 100) if f"{p['id']}_{n}" not in taken)
        return None

    @staticmethod
    def _marks_on(p: dict) -> str:
        """What marks a panel takes (contract), said for the model so it never claims one it cannot draw."""
        kinds = sorted(k for k, on in contract().validate.MARK_ON.items() if p["type"] in on)
        if p["type"] == "heatmap" and (p.get("options") or {}).get("kind") == "seasonal":
            kinds = []
        if p["type"] == "heatmap" and kinds:
            return ("only trend breaks: analyze_trend on the same series, then add_trend_breaks with target=" + p["id"]
                    + "; no highlight, no note")
        return ", ".join(kinds) if kinds else "none: say plainly that this chart takes no marks"

    @tool("Compare things over time or between two years on one chart (a compare panel): partners against "
          "partners, products against products, Luxembourg against EU peers (rebased to 100 so a small and a large "
          "economy compare), two years side by side, or exports against imports with the balance. Modes: "
          "absolute, index (100 at base_period), share, yoy (growth on the year before), diff (minus the first). "
          "Over time (partners, products, peers, flows: key = the code in lower case, 'hs85' for a product, "
          "'exports'/'imports' for flows) the caption 'text' and the note may use {<key>_now} (the last year "
          "shown), {<key>_then} (the FIRST year shown, start), {<key>_change} (growth over the WHOLE span, start to "
          "the last year: never call it the change on the year before), {<key>_prev} and {<key>_yoy} (the last "
          "year against the year before), and for flows {balance}. Two years side by side (what='years'): "
          "{a_total} {b_total} {change}, and for highlight_codes {<code>_a} {<code>_b} {<code>_diff} (euros) "
          "{<code>_growth}. Returns the panel id and what each fact shows.",
          {"what": S("partners, products, peers, years or flows",
                     enum=["partners", "products", "peers", "years", "flows"]),
           "codes": L("partners (DE, FR), products (85, 30) or EU peers (BE, DE): 1 to 5"),
           "flow": FLOW, "start": START, "end": PERIOD,
           "mode": S("absolute, index, share, yoy or diff", enum=["absolute", "index", "share", "yoy", "diff"]),
           "base_period": S("index mode: the year that is 100"),
           "period_a": PERIOD, "period_b": PERIOD,
           "by": S("years: partner, product or month", enum=["partner", "product", "month"]),
           "layout": S("peers: lines (default) or bars", enum=["lines", "bars"]),
           "partner": S("filter: only trade with this partner"), "product": S("filter: only this product"),
           "title": S("sentence case, no figures"), "text": S("a caption with the facts above; no typed figures"),
           "highlight_codes": L("codes to highlight (bars)"), "note": S("the note of the highlight")},
          ["what"])
    def compare(self, what: str, codes: Optional[list] = None, flow: str = "EXPORT", start: Optional[str] = None,
                end: Optional[str] = None, mode: Optional[str] = None, base_period: Optional[str] = None,
                period_a: Optional[str] = None, period_b: Optional[str] = None, by: Optional[str] = None,
                layout: Optional[str] = None, partner: Optional[str] = None, product: Optional[str] = None,
                title: Optional[str] = None, text: Optional[str] = None, highlight_codes: Optional[list] = None,
                note: Optional[str] = None) -> dict:
        v = self.view
        q = self.question
        if show_year(q):
            raise ToolProblem(f"this question only moves the page to {show_year(q)}: set_context; add no chart")
        if TWO_WAYS_RE.search(q) and what in ("flows", "years", "partners", "products") and not partners_in(q):
            by_ = "product" if BY_PRODUCT_RE.search(q) else "partner"
            raise ToolProblem(f"bought against sold, {by_} by {by_}, is a butterfly: show_panel type='butterfly', "
                              f"by='{by_}', then highlight the {by_} with the widest gap")
        if what == "partners" and asks_peers(q):
            raise ToolProblem(
                "the question compares Luxembourg with other countries: those are peers, not partners. "
                "compare(what='partners') would draw Luxembourg's own exports TO those countries, never theirs. "
                + PEERS_WITH_PARTNER)
        if what == "peers" and partner:
            self._peers_with_partner(codes, partner, flow, product, end or period_b)
        if what == "partners":
            named = [c for c in partners_in(q) if c not in [str(x).upper() for x in codes or []]]
            codes = [str(c).upper() for c in codes or []] + named   # every partner the question names
        if end and what != "years" and _year(end) not in years_in(q):
            end = None              # a chart over time ends at the page's year unless the user named the end
        if YOY_RE.search(q) and what in ("partners", "products", "flows") and mode in (None, "absolute"):
            mode = "yoy"
        elif GAP_RE.search(q) and what in ("partners", "products") and mode in (None, "absolute") and                 len(codes or []) >= 2:
            mode = "diff"
        method = {"partners": "compare_partners", "products": "compare_products", "peers": "compare_with_peers",
                  "years": "compare_years", "flows": "compare_flows"}.get(what)
        if method is None:
            raise ToolProblem(f"compare takes what = partners, products, peers, years or flows, not {what!r}. Months "
                              "by year are a heatmap (show_panel type='heatmap'); part of a year against the same "
                              "months a year before is in get_series freq M (same_months)")
        if not hasattr(v, method):
            raise ToolProblem("comparisons are not available in this version of the kit; use show_panel line")
        codes = [str(c).upper() if what != "products" else str(c) for c in (codes or [])]
        if what != "years":
            # the period the user asked for ('since 1999'), else the first year of the data: never a default 2015
            given = start
            start = self._start(start, *(codes if what == "products" else []), product)
            if mode == "yoy" and start <= FIRST_YEAR:
                start = str(int(FIRST_YEAR) + 1)    # growth on the year before needs the year before
            end = self._end(end or period_b)
            if (mode or ("index" if what == "peers" and (layout or "lines") == "lines" else None)) == "index":
                base_period = self._base(base_period, start)
            title = _respan(title, given, start)
            text, note = _respan(text, given, start), _respan(note, given, start)
        kw: dict = {k: val for k, val in (("title", _label(title, 160) if title else None), ("text", text))
                    if val is not None}
        if highlight_codes:
            if not note:
                raise ToolProblem("a highlight needs a note")
            need_figure(note, "highlight")
            kw.update(highlight=[str(c) for c in highlight_codes], note=note)
        try:
            if what in ("partners", "products"):
                if not codes:
                    raise ToolProblem(f"give the {what} to compare in codes")
                extra = {"product": product} if what == "partners" else {"partner": partner}
                ops = getattr(v, method)(codes[:6], flow=flow, start=start, end=end or "$period",
                                         mode=mode or "absolute", base_period=base_period,
                                         **{k: x for k, x in extra.items() if x}, **kw)
            elif what == "peers":
                peers = [c for c in codes if c != "LU"] or ["BE", "DE", "FR", "NL"]
                ops = v.compare_with_peers(peers[:5], "exports" if flow == "EXPORT" else "imports",
                                           start=start, end=end or "$period", mode=mode,
                                           base_period=base_period, layout=layout or "lines", product=product,
                                           **({"partner": str(partner).upper()} if partner else {}), **kw)
            elif what == "years":
                a, b = period_a or "$previous", period_b or "$period"
                if not a.startswith("$") and not b.startswith("$") and a > b:
                    a, b = b, a           # the earlier year is a (light), the later b (full): reads left to right
                ops = v.compare_years(a, b, by=by or "partner", flow=flow,
                                      mode=mode or "absolute", product=product, partner=partner, **kw)
            else:
                ops = v.compare_flows(start=start, end=end or "$period", partner=partner,
                                      product=product,
                                      mode=mode or "absolute", **kw)
        except ValueError as exc:
            raise ToolProblem(str(exc))
        pid = ops[0]["panel"]["id"]
        cpanel = ops[0]["panel"]
        series_keys = [q["key"] for q in cpanel.get("queries", []) if q["endpoint"] == "/insights/series"]
        if series_keys:   # the year before the last, so a caption can say what changed on the year
            cfacts = cpanel.setdefault("facts", {})
            for k in series_keys:
                cfacts.setdefault(f"{k}_now", fact_from(k, "-1.value", "eur"))
                cfacts.setdefault(f"{k}_prev", fact_from(k, "-2.value", "eur"))
                cfacts.setdefault(f"{k}_yoy", fact_op("growth", f"{k}_now", f"{k}_prev", "pct_signed"))
        if text:
            self.pick(text, cpanel.get("facts") or {})
            need_figure(text, "caption")
        items = []
        for i, op in enumerate(ops):
            if op["op"] == "add_mark":
                op["tmp_id"] = f"c{i}"
                items.append({"tmp": f"c{i}", "what": f"{op['mark']['kind']} on {pid}"})
                mvocab = self._compare_mark_vocab(cpanel, op["mark"].get("params", {}).get("codes") or [])
                mfacts = self.pick(op["mark"].get("note") or "", mvocab)
                if mfacts:
                    op["mark"]["facts"] = mfacts

        def build(spec):
            if not self._can_upsert(spec, pid):
                raise ToolProblem(f"panel {pid!r} is pinned by the user")
            batch = copy.deepcopy(ops)
            for op in batch:   # a highlight of rows on two years is about the later year; a point, about its year
                m = op.get("mark") if op["op"] == "add_mark" else None
                if m and not m.get("period") and m["kind"] in ("highlight", "annotation"):
                    about = self._about(spec, cpanel, x=(m.get("params") or {}).get("x"))
                    if about and not (m["kind"] == "highlight" and self._timeline(cpanel)):
                        m["period"] = about
            return batch, items
        out = self.commit(build, f"Compared {what}: {ops[0]['panel'].get('title', '')}")
        if out.get("ok"):
            out["panel"] = pid
            out["facts_available"] = self._facts_meaning(cpanel)
            out["shows"] = self._panel_summary(ops[0]["panel"])
        return out

    def _peers_with_partner(self, codes: Optional[list], partner: str, flow: str, product: Optional[str],
                            end: Optional[str]) -> None:
        """Each peer's own trade with ``partner`` must be readable (the backend fetches a missing slice from
        Eurostat): when it is not, the plain refusal, and the chart of total trade instead."""
        peers = ["LU"] + [str(c).upper() for c in codes or [] if str(c).upper() != "LU"]
        year = _year(str(end)) if end and not str(end).startswith("$") else self.last_year()
        try:
            rows = self.get("/insights/compare", by="reporter", period=year, group=",".join(peers[:6]),
                            partner=str(partner).upper(), flow=flow if flow in ("EXPORT", "IMPORT") else "EXPORT",
                            product=product)
        except InsightsError:
            rows = None
        lost = [r.get("code") for r in rows or [] if r.get("loaded") is False or r.get("value") is None]
        if rows is None or lost:
            raise ToolProblem(PEERS_TOTAL_ONLY + " Say that plainly first in the reply, then leave partner out: "
                              "compare(what='peers') draws each country's TOTAL exports; call it total trade, not "
                              f"trade with {self.country(partner)}.")

    def _compare_mark_vocab(self, p: dict, codes: list) -> dict:
        """Facts a note on a compare panel can use: the series' own ({<key>_change}, {<key>_yoy}, ...), and on
        two years side by side each highlighted code's {<code>_a} {<code>_b} {<code>_diff} {<code>_growth}."""
        vocab = dict(self._series_vocab(p))
        qs = {q["key"]: q for q in p.get("queries", [])}
        if "a" in qs and "b" in qs and qs["a"]["endpoint"] == "/insights/ranking":
            ctx = self.context(self.turn.spec or self.spec())
            for c in [str(c) for c in codes][:8]:
                k = code_key(c)
                for side in ("a", "b"):
                    prm = contract().validate.fill_params(qs[side].get("params") or {}, ctx)
                    vocab[f"{k}_{side}"] = fact_api("/insights/ranking", prm, f"items.[code={c}].value", "eur")
                vocab[f"{k}_diff"] = fact_op("diff", f"{k}_b", f"{k}_a", "eur_signed")
                vocab[f"{k}_growth"] = fact_op("growth", f"{k}_b", f"{k}_a", "pct_signed")
        return vocab

    def _facts_meaning(self, p: dict) -> dict:
        """{fact: what it is, with its years}, so the model never reads a span change as a change on the year."""
        ctx = self.context(self.turn.spec)
        out: dict = {}
        for q in p.get("queries", []):
            if q["endpoint"] != "/insights/series":
                continue
            prm = contract().validate.fill_params(q.get("params") or {}, ctx)
            k, start, end = q["key"], _year(prm.get("start") or "?"), _year(prm.get("end") or ctx.get("period", "?"))
            prev = str(int(end) - 1) if end.isdigit() else "the year before"
            out.update({f"{k}_now": f"{q.get('label') or k} in {end}", f"{k}_then": f"in {start}",
                        f"{k}_change": f"growth {start} to {end} (the whole span)",
                        f"{k}_prev": f"in {prev}", f"{k}_yoy": f"growth {prev} to {end}"})
        for k in (p.get("facts") or {}):
            out.setdefault(k, "see the tool description")
        return dict(list(out.items())[:30])

    def _panel_summary(self, p: dict) -> Any:
        """What a new panel shows, read from its first query (placeholders filled as the page does)."""
        ctx = self.context(self.turn.spec)
        out = {}
        for q in p.get("queries", [])[:6 if p["type"] == "compare" else 2]:
            try:
                data = self.get(q["endpoint"], **contract().validate.fill_params(q.get("params") or {}, ctx))
                out[q["key"]] = summarize(q["endpoint"], data, n=5)
            except InsightsError as exc:
                out[q["key"]] = f"could not read: {exc.status}"
        return out

    def _with_quantity(self, p: dict, quantity: str, unit_word: Optional[str], id: Optional[str]) -> dict:
        """A line or bar of a quantity (contract 1.8: the query reads measure=units or weight, the format carries
        the unit: 'qty:cars', 'tonnes'), or a table of every measure (value, quantity, value per unit)."""
        if p["type"] not in ("line", "bar", "table"):
            raise ToolProblem("quantities go on a line, a bar or a table; leave quantity out for this chart")
        if p["type"] == "table" or quantity == "all":
            if p["type"] != "table":
                raise ToolProblem("quantity='all' is for a table; a line or a bar shows quantity='units' or 'weight'")
            for qr in p["queries"]:
                qr["params"]["measure"] = "all"
            data = self.get(p["queries"][0]["endpoint"], **contract().validate.fill_params(
                p["queries"][0]["params"], self.context(self.turn.spec or self.spec())))
            rows = data if isinstance(data, list) else (data.get("items") or [])
            counted = any(r.get("units") is not None for r in rows)
            p.setdefault("options", {})["columns"] = (["name", "value", "units", "eur_per_unit", "weight"] if counted
                                                     else ["name", "value", "weight", "eur_per_tonne"])
            if counted and unit_word:
                p["options"]["unit"] = _label(str(unit_word), 30)   # '14,871 cars' in the quantity column
        else:
            first = p["queries"][0]
            if first["endpoint"] not in ("/insights/series", "/insights/ranking/records", "/insights/ranking"):
                raise ToolProblem("this chart cannot show a quantity; draw a line or a ranking bar")
            for qr in p["queries"]:
                qr["params"]["measure"] = quantity
            try:
                probe = self.get(first["endpoint"], **contract().validate.fill_params(
                    first["params"], self.context(self.turn.spec or self.spec())))
            except InsightsError as exc:
                raise ToolProblem(f"no {quantity} for this chart: {exc.detail}. "
                                  "Say so plainly; draw weight (tonnes) or the value in euros instead")
            rows = probe if isinstance(probe, list) else (probe.get("rows") or probe.get("items") or [])
            unit = next((r.get("unit") for r in rows if r.get("unit")), None)
            if quantity == "weight":
                fmt_ = "tonnes"
            else:
                word = unit_word if unit_word and unit in (None, "items") else unit or "units"
                fmt_ = f"qty:{_label(str(word), 30)}"
            p.setdefault("options", {})["format"] = fmt_
        if not id:
            p["id"] = f"{p['id']}_{quantity}"[:60]
        return p

    def _build_panel(self, type, title, id, subtitle, flow, by, period, previous, start, end, freq, partner,
                     product, reporters, level, parent, measure, extent, kind, depth, max_items, x, y, base) -> dict:
        title = _label(title, 160)
        filters = {k: v for k, v in (("partner", partner), ("product", product)) if v}
        one_flow = flow if flow in ("EXPORT", "IMPORT") else "EXPORT"
        word = "exports" if one_flow == "EXPORT" else "imports"
        kw = {"subtitle": subtitle} if subtitle else {}
        if type == "line":
            freq = freq or "A"
            if freq == "M":
                start = start or f"{_year(period or '2025')}-01"
                end = end or (f"{_year(period)}-12" if period else None)
            else:
                start, end = self._start(start, product), self._end(end, "$period")
            span = {k: v for k, v in (("freq", freq), ("start", start), ("end", end)) if v}
            if reporters:
                codes = [str(r).upper() for r in reporters][:6]
                qs = [query("/insights/series", {"flow": one_flow, **span, **filters, "reporter": c},
                            key=_slug(c), label=c, tone=word if c == "LU" else None) for c in codes]
                pid = id or _slug("viz", word, *codes)
            else:
                flows = [one_flow] if flow in ("EXPORT", "IMPORT") else ["IMPORT", "EXPORT"]
                qs = [query("/insights/series", {"flow": f, **span, **filters},
                            key="exports" if f == "EXPORT" else "imports",
                            label="Exports" if f == "EXPORT" else "Imports",
                            tone="exports" if f == "EXPORT" else "imports") for f in flows]
                pid = id or _slug("viz_line", partner, product, freq == "M" and start)
            return panel(pid, "line", title, qs, options={"x": "period", "y": "value", "format": "eur",
                                                          "gap_fill": True}, **kw)
        if type == "bar" and measure == "growth":
            by = by if by in ("partner", "product") else "partner"
            first = self._start(start, *(["000000"] if by == "product" and (level or 1) >= 3 else []))
            if not subtitle:
                kw["subtitle"] = f"Average growth per year since {first} (compound)"
            return growth_bar_panel(id or _slug("viz_growth", by, word, partner, product, parent), title, by=by,
                                    start=first, end=self._end(end, "$period"), flow=one_flow, limit=max_items or 8,
                                    level=level, parent=parent, **filters, **kw)
        if type == "bar":
            by = by or "partner"
            if by == "reporter":
                params = {"by": "reporter", "period": period or "$period", "flow": one_flow,
                          "base": self._base(base, self._start(None, product)),
                          "limit": 27, **filters}
                return panel(id or _slug("viz_peers", word, product), "bar", title,
                             [query("/insights/compare/records", params, key="main", tone=word)],
                             options={"x": "code", "y": "growth", "format": "percent", "orientation": "vertical",
                                      "highlight_focus": True}, **kw)
            params = {k: v for k, v in {"by": by, "period": period or "$period", "flow": one_flow,
                                        "max_items": max_items or 8, "level": level, "parent": parent,
                                        **filters}.items() if v is not None}
            return panel(id or _slug("viz_bar", by, word, partner, product, parent, period), "bar", title,
                         [query("/insights/ranking/records", params, key="main", tone=word)],
                         options={"x": "name", "y": "value", "format": "eur", "orientation": "horizontal",
                                  "limit": max_items or 8, "exclude_codes": ["OTHER"]}, **kw)
        if type == "table":
            by = by if by in ("partner", "product") else "partner"
            params = {k: v for k, v in {"by": by, "period": period or "$period", "flow": one_flow,
                                        "max_items": max_items or 10, "level": level, "parent": parent,
                                        **filters}.items() if v is not None}
            return panel(id or _slug("viz_table", by, word, partner, product, parent), "table", title,
                         [query("/insights/ranking/records", params, key="main", tone=word)],
                         options={"columns": ["name", "value", "share"], "format": "eur", "limit": max_items or 10,
                                  "exclude_codes": ["OTHER"]}, **kw)
        if type == "waterfall":
            return waterfall_panel(id or _slug("viz_change", word, partner, product), title,
                                   by=by if by in ("partner", "product") else "product", period=period or "$period",
                                   previous=previous or "$previous", flow=one_flow, level=level, parent=parent,
                                   limit=max_items or 8, **filters, **kw)
        if type == "choropleth":
            measure = measure if measure in ("share", "value", "balance") else "share"
            return choropleth_panel(id or _slug("viz_map", measure if measure == "balance" else word), title,
                                    measure=measure, extent=extent or "europe", period=period or "$period",
                                    flow=one_flow, **({"product": product} if product else {}), **kw)
        if type == "flowmap":
            mode = {"EXPORT": "exports", "IMPORT": "imports"}.get(flow or "", "both")
            return flowmap_panel(id or _slug("viz_routes", product), title, flow=mode, extent=extent or "europe",
                                 period=period or "$period", previous=previous, max_items=max_items or 10,
                                 **({"product": product} if product else {}), **kw)
        if type == "heatmap":
            kind = kind or "month_year"
            if kind == "month_year":
                return heatmap_panel(id or _slug(word, partner, product, "months"), title, kind=kind, flow=one_flow,
                                     start=self._start(start, product, monthly=True),
                                     end=(lambda e: f"{e}-12" if e and len(e) == 4 else e)(self._end(end)),
                                     **filters, **kw)
            if kind == "product_year":
                hm_measure = measure if measure in ("share", "value", "growth") else "share"
                return heatmap_panel(id or _slug("viz_codes_years", by or "product", word), title, kind=kind,
                                     flow=one_flow, by=by if by in ("partner", "product") else "product",
                                     start=self._start(start), end=self._end(end, "$period"), measure=hm_measure,
                                     max_items=max_items or 12, **kw)
            return heatmap_panel(id or _slug("viz_seasons", word), title, kind="seasonal", flow=one_flow,
                                 top=max_items or 12, **({"partner": partner} if partner else {}), **kw)
        if type == "treemap":
            return treemap_panel(id or _slug("viz_goods", word, partner), title, period=period or "$period",
                                 flow=one_flow, depth=depth or 2, **({"partner": partner} if partner else {}), **kw)
        if type == "butterfly":
            return butterfly_panel(id or _slug("viz_two_ways", by or "product", partner), title,
                                   by=by if by in ("partner", "product") else "product", period=period or "$period",
                                   max_items=max_items or 10, **({"partner": partner} if partner else {}), **kw)
        if type == "bump":
            return bump_panel(id or _slug("viz_ranks", by or "partner", word), title,
                              by=by if by in ("partner", "product") else "partner", start=self._start(start),
                              end=self._end(end, "$period"), flow=one_flow, max_items=max_items or 10, **kw)
        if type == "scatter":
            return scatter_panel(id or _slug("viz_strengths", word), title, period=period or "$period", flow=one_flow,
                                 x=x or "rca", y=y or "growth", previous=previous or "$previous",
                                 options={"reference_x": 1, "reference_y": 0, "log_x": True}, **kw)
        raise ToolProblem(f"unknown chart type {type!r}; one of {', '.join(self.PANEL_TYPES)}")

    @tool("Take your own marks off the page: by page number ([n]), by kind (forecast, event, trend_break, ...), "
          "or all your proposed marks. Marks the user kept stay unless you name their number. Removing the last "
          "forecast band also removes its 'What comes next' panel.",
          {"numbers": L("page numbers to remove", {"type": "integer"}),
           "kind": S("remove all your marks of this kind"), "all_mine": B("remove all your proposed marks"),
           "panels": L("also remove these panels you added (ids)")})
    def remove_marks(self, numbers: Optional[list] = None, kind: Optional[str] = None, all_mine: bool = False,
                     panels: Optional[list] = None) -> dict:
        removed: list[int] = []

        def build(spec):
            removed.clear()
            ops, gone = [], set()
            for n in numbers or []:
                for m in page.marks_of_number(spec, int(n)):
                    gone.add(m["id"])
                if page.marks_of_number(spec, int(n)):
                    removed.append(int(n))
            for m in spec.get("marks", []):
                mine = m.get("agent") == self.agent
                if mine and ((kind and m["kind"] == kind) or all_mine) and m.get("status") != "kept":
                    gone.add(m["id"])
            removed.extend(n for n in page.page_numbers(spec, sorted(gone)) if n not in removed)
            ops += [self.view.remove_mark(mid) for mid in sorted(gone)]
            extra = list(panels or [])
            by_id = {m["id"]: m for m in spec.get("marks", [])}
            if any(by_id[mid]["kind"] == "forecast" for mid in gone if mid in by_id):
                # a band goes with its "What comes next" text, unless another band of this agent stays
                left = [m for m in spec.get("marks", []) if m["kind"] == "forecast" and m["id"] not in gone
                        and m.get("agent") == self.agent]
                if not left:
                    extra += [p["id"] for p in spec.get("panels", []) if p["id"].startswith("outlook")
                              and p.get("agent") == self.agent]
            for pid in dict.fromkeys(extra):
                p = page.panel(spec, pid)
                if p and p.get("agent") not in (None, "baseline", "user") and not p.get("pinned"):
                    ops.append(self.view.remove_panel(pid, force=True))
            return ops, []
        out = self.commit(build, "Took marks off the page at the user's request.", check_facts=False)
        out["removed_numbers"] = sorted(set(removed))
        if out.get("ok") and self.turn.spec:
            out["panels_left"] = [p["id"] for p in self.turn.spec.get("panels", [])
                                  if p.get("agent") == self.agent]
        self.turn.removed += out["removed_numbers"]
        if out.get("ok") and not removed and not panels:
            out["note"] = "nothing matched: read_page to see the numbers"
        return out

    @tool("Keep marks on the page at the user's request ('keep 1', 'keep the smartphone story'): kept marks stay "
          "when the page is cleared. It acts for the user, as the Keep button does. With remove=true it takes "
          "those numbers off instead, whoever drew them.",
          {"numbers": L("page numbers [n] to keep (or remove)", {"type": "integer"}),
           "remove": B("remove them instead of keeping them")}, ["numbers"])
    def keep_marks(self, numbers: list, remove: bool = False) -> dict:
        user = copy.copy(self.view)
        user.agent_name = "user"
        done: dict[str, list[int]] = {"kept": [], "removed": [], "unknown": [], "already_kept": []}

        def build(spec):
            for k in done:
                done[k] = []
            ops = []
            for n in dict.fromkeys(int(x) for x in numbers or []):
                members = page.marks_of_number(spec, n)
                if not members:
                    done["unknown"].append(n)
                elif remove:
                    ops += [user.remove_mark(m["id"]) for m in members]
                    done["removed"].append(n)
                else:
                    todo = [user.keep_mark(m["id"]) for m in members if m.get("status") != "kept"]
                    ops += todo
                    done["kept" if todo else "already_kept"].append(n)
            return ops, []
        saved, self.view = self.view, user
        try:
            out = self.commit(build, ("Removed" if remove else "Kept") + " at the user's request, from the chat.",
                              check_facts=False)
        finally:
            self.view = saved
        out.update({k: v for k, v in done.items() if v})
        if remove:
            self.turn.removed += done["removed"]
        return out

    @tool("Change the note (and label) of a mark already on the page, by its page number.",
          {"number": I("the page number [n]"), "note": S("the new note; figures only as placeholders already in "
                                                          "its facts"), "label": S("a new short label")},
          ["number", "note"])
    def update_note(self, number: int, note: str, label: Optional[str] = None) -> dict:
        def build(spec):
            marks = page.marks_of_number(spec, int(number))
            if not marks:
                raise ToolProblem(f"there is no [{number}] on the page")
            m = marks[0]
            changes: dict = {"note": note}
            if label:
                changes["params"] = {**(m.get("params") or {}), "label": _label(label)}
            return [self.view.update_mark(m["id"], **changes)], [{"id": m["id"], "what": f"updated [{number}]"}]
        return self.commit(build, f"Updated the note of [{number}].")

    @tool("Remove a panel an agent added (with its marks).", {"id": S("panel id")}, ["id"])
    def remove_panel(self, id: str) -> dict:
        def build(spec):
            p = page.panel(spec, id)
            if p is None:
                raise ToolProblem(f"no panel {id!r}")
            if p.get("agent") in (None, "baseline", "user"):
                raise ToolProblem(f"{id!r} is one of the page's own panels")
            if p.get("pinned"):
                raise ToolProblem(f"{id!r} is pinned by the user")
            return [self.view.remove_panel(id, force=True)], []
        try:
            gone_title = (page.panel(self.spec(), id) or {}).get("title")
        except Exception:  # the page cannot be read: the title stays
            gone_title = None
        out = self.commit(build, f"Removed the panel {id}.", check_facts=False)
        if out.get("ok") and out.get("changed") is not False and gone_title:
            self.refresh_title([gone_title])
        return out

    @tool("Clear what the agents drew: marks and added panels go, the headline and title go back to the "
          "baseline. Kept marks and pinned panels stay. ONLY when the user asks to clear, reset or start over.")
    def clear_page(self) -> dict:
        if not asks_clear(self.question):
            raise ToolProblem("the page is cleared only when the user asks to clear, reset or start over, and this "
                              "question does not: leave everything on the page")
        baseline = contract().load_baseline()
        base_panels = {p["id"]: p for p in baseline["panels"]}

        def content(p):
            return {k: v for k, v in p.items() if k not in ("agent", "pinned", "provenance")}

        def build(spec):
            marks = spec.get("marks", [])
            kept_targets = {m["target"] for m in marks if m.get("status") == "kept"}
            removed = [p["id"] for p in spec["panels"] if p.get("agent", "baseline") not in ("baseline", "user")
                       and not p.get("pinned") and p["id"] not in kept_targets]
            changed = [pid for pid in removed if not (pid in base_panels and content(page.panel(spec, pid))
                                                      == content(base_panels[pid]))]
            drop = [m for m in marks if m.get("status") != "kept"]
            if not drop and not changed and spec.get("title") == baseline["title"]:
                return [], []
            ops = [self.view.clear("all")]
            restored = [pid for pid in removed if pid in base_panels]
            for pid in restored:
                p = {k: v for k, v in base_panels[pid].items() if k not in ("agent", "pinned")}
                if "provenance" in p:
                    p["provenance"] = {"sources": p["provenance"].get("sources", [])}
                ops.append(self.view.upsert_panel(p))
            if restored:
                gone = set(removed) - set(restored)
                present = {p["id"] for p in spec["panels"]} - gone
                rows = [[pid for pid in r["panels"] if pid in present] for r in baseline["layout"]["rows"]]
                rows = [r for r in rows if r]
                placed = {pid for r in rows for pid in r}
                rows += [[pid] for pid in [p["id"] for p in spec["panels"]] if pid in present and pid not in placed]
                ops.append(self.view.set_layout(rows))
            if spec.get("title") != baseline["title"]:
                ops.append(self.view.set_title(baseline["title"]))
            return ops, []
        out = self.commit(build, "Cleared what the agents drew, at the user's request.", check_facts=False)
        if out.get("ok"):
            after = self.turn.spec or self.spec()
            kept = [m["id"] for m in after.get("marks", []) if m.get("status") == "kept"]
            self.turn.cleared = {"kept": page.page_numbers(after, kept),
                                 "pinned": [p["id"] for p in after.get("panels", []) if p.get("pinned")]}
            out["kept_marks_stay"] = self.turn.cleared["kept"]
            out["pinned_panels_stay"] = self.turn.cleared["pinned"]
        return out

    @tool("Fallback only, when no other tool fits: send raw view ops (contract/ops.schema.json) as a JSON array.",
          {"ops_json": S("a JSON array of ops"), "message": S("what the batch does")}, ["ops_json", "message"])
    def post_view_ops(self, ops_json: str, message: str) -> dict:
        try:
            ops = json.loads(ops_json)
        except json.JSONDecodeError as exc:
            raise ToolProblem(f"ops_json is not JSON: {exc}")
        if not isinstance(ops, list) or not ops:
            raise ToolProblem("ops_json must be a non-empty JSON array of ops")
        items = [{"tmp": op.get("tmp_id"), "what": op["mark"].get("kind")} for op in ops
                 if isinstance(op, dict) and op.get("op") == "add_mark" and op.get("tmp_id")]
        return self.commit(lambda spec: (copy.deepcopy(ops), items), message)


# --------------------------------------------------------------------------- who gets which tools
READ_COMMON = ("read_page", "find_codes", "get_quantities")   # every role can answer "how many"
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "trend_finder": READ_COMMON + ("analyze_trend", "drill_down", "get_movers", "get_series", "get_ranking",
                                   "get_trends", "get_trend", "get_matrix",
                                   "set_title", "write_headline", "add_thread", "add_trend_breaks", "highlight",
                                   "add_annotation", "add_trend_line", "show_panel", "remove_marks", "update_note",
                                   "keep_marks"),
    "cause_finder": READ_COMMON + ("get_events", "analyze_trend", "get_series", "get_movers", "get_ranking",
                                   "add_events", "show_panel", "add_annotation", "highlight", "set_title",
                                   "remove_marks", "update_note", "keep_marks"),
    "forecaster": READ_COMMON + ("get_forecast", "get_backtest", "get_series", "get_movers", "analyze_trend",
                                 "get_seasonal",
                                 "add_forecast_band", "show_panel", "add_annotation", "remove_marks", "update_note",
                                 "remove_panel", "keep_marks"),
    "visualizer": READ_COMMON + ("get_ranking", "get_series", "get_flows", "get_compare", "get_specialisation",
                                 "get_movers", "get_tree", "get_matrix", "get_seasonal", "analyze_trend",
                                 "show_panel", "compare", "set_context", "set_title", "highlight", "add_annotation",
                                 "add_thread", "add_trend_breaks", "remove_marks", "remove_panel", "clear_page",
                                 "update_note", "keep_marks", "post_view_ops"),
    # read tools only; get_forecast so a summary of the conversation ("three key messages") can check its figures
    "general": READ_COMMON + ("get_series", "get_ranking", "get_flows", "get_forecast"),
}
