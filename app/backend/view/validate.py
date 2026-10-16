# SPDX-License-Identifier: MIT
"""Validation of the view spec and of op batches (contract besser-view-spec-1.0 / besser-view-ops-1.0).

Two layers, no network:

1. JSON Schema (schema/view-spec.schema.json, schema/ops.schema.json, copies of contract/).
2. Semantic checks the schema cannot express: the endpoint's own parameters and the dimensions
   of /insights/meta, required parameters, period formats, mark targets that exist (or are added
   earlier in the same batch), series and facts that refer to real queries, no figures typed
   into text, the layout covering every panel exactly once.

Every error is ``{"path": <JSON pointer>, "code": <slug>, "message": <text>}``. Semantic checks
only run on input that passed the schema, so they can trust its shape.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = HERE / "schema"
BASELINE_PATH = HERE / "baseline.json"
SPEC_SCHEMA_ID = "besser-view-spec-1.0"
OPS_SCHEMA_ID = "besser-view-ops-1.0"
MAX_OPS = 50

# Dimensions of the LuxTrade model, used when no /insights/meta is passed in.
DEFAULT_DIMENSIONS = ("freq", "reporter", "partner", "product", "flow", "indicators")

# endpoint -> (required parameters, optional parameters), besides dimension filters.
ENDPOINT_PARAMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "/insights/meta": ((), ()),
    "/insights/ranking": (("by", "period"), ("level", "parent", "threshold", "max_items", "relative_to", "start", "end",
                                             "measure")),
    "/insights/ranking/records": (("by", "period"), ("level", "parent", "threshold", "max_items", "relative_to", "start",
                                                     "end", "measure")),
    "/insights/ladder": (("by", "period"), ("level", "parent", "relative_to", "start", "end")),
    "/insights/series": ((), ("adjust", "start", "end", "envelope", "measure")),
    "/insights/movers": (("by", "period", "previous"), ("level", "parent", "threshold", "limit", "measure")),
    "/insights/compare": (("by", "period"), ("group", "measure")),
    "/insights/compare/records": (("by", "period"), ("base", "limit")),
    "/insights/specialisation": (("by", "period"), ()),
    "/insights/specialisation/records": (("by", "period"), ("limit",)),
    "/insights/trend": ((), ("start", "end", "adjust", "method")),
    "/insights/trends": (("by",), ("start", "end", "level", "parent", "threshold", "limit", "method")),
    "/insights/forecast": ((), ("horizon", "method", "interval", "start", "end", "adjust", "aggregate", "backtest")),
    "/insights/events": ((), ("start", "end", "category", "limit")),
    "/insights/flows": (("period",), ("previous", "max_items", "threshold")),
    # contract 1.1
    "/insights/matrix": (("by", "start", "end"), ("level", "parent", "max_items", "measure", "threshold")),
    "/insights/tree": (("by", "period"), ("depth", "threshold")),
    "/trends/analyze": ((), ("start", "end", "stl_period", "min_size", "penalty")),
    "/trends/seasonal-heatmap": ((), ("start", "end", "top")),
}
ENDPOINTS = frozenset(ENDPOINT_PARAMS)
NO_FILTERS = {"/insights/meta", "/insights/events"}  # endpoints that take no dimension filters
PERIOD_PARAMS = {"period", "previous", "start", "end", "base"}
PERIOD_RE = re.compile(r"^[0-9]{4}(-(0[1-9]|1[0-2]))?$")

# Parameters whose values are a closed set, per endpoint.
PARAM_VALUES: dict[tuple[str, str], set] = {
    ("/insights/matrix", "measure"): {"value", "share", "rank", "growth"},
    # contract 1.8: what a query reads (euros, tonnes, the line's own unit, or all of them with unit values)
    ("/insights/series", "measure"): {"value", "weight", "units", "all"},
    ("/insights/ranking", "measure"): {"value", "weight", "units", "all"},
    ("/insights/ranking/records", "measure"): {"value", "weight", "units", "all"},
    ("/insights/movers", "measure"): {"value", "weight", "units"},
    ("/insights/compare", "measure"): {"value", "weight", "units"},
    ("/insights/tree", "by"): {"product"},
    ("/insights/tree", "depth"): {1, 2, 3, "1", "2", "3"},
}

# Context placeholders a query param may hold, and the params each may stand for. The renderer
# and the backend (query_url) replace them with spec.context: $previous is the year before $period.
PLACEHOLDERS: dict[str, set] = {
    "$period": PERIOD_PARAMS,
    "$previous": PERIOD_PARAMS,
    "$start": {"start"},  # 1.4: the first year of a range (context.start), else the period
    "$product": {"product", "parent"},
}
DEFAULT_CONTEXT = {"period": "2025", "product": "TOTAL"}
# 1.5: the fields of a panel's own context (panel.context, set_panel_context), and the placeholders each
# stands for: a panel may only set what its queries follow.
PANEL_CONTEXT_FIELDS = ("period", "start", "product")
PANEL_CONTEXT_PLACEHOLDERS = {"period": ("$period", "$previous"), "start": ("$start",), "product": ("$product",)}

# Which panel types a mark kind can be drawn on.
MARK_ON = {
    "thread": {"flow", "flowmap"},
    "trend": {"line"},
    "event": {"line", "compare"},
    "forecast": {"line"},
    "annotation": {"line", "bar", "waterfall", "compare"},
    "highlight": {"bar", "flow", "line", "table", "choropleth", "flowmap", "treemap", "butterfly", "bump", "scatter",
                  "compare"},
    "trend_break": {"line", "heatmap"},
}

# Comparisons (contract 1.2): the compare panel, and the comparison options of line and bar panels.
COMPARE_MODES = {"absolute", "index", "share", "yoy", "diff"}
PAIR_ENDPOINTS = {"/insights/ranking", "/insights/compare"}
MAX_COMPARE_SERIES = 6
# Options that turn a line into a comparison (read like a compare panel's series), and a bar into
# two snapshots a and b side by side.
LINE_COMPARE_OPTIONS = ("compare", "base_period", "reference", "focus", "facet", "align", "balance")
BAR_COMPARE_OPTIONS = ("compare", "sort", "focus")
# Contract 1.7: a bar of growth per year over a span (options.measure "growth"), read from /insights/trends.
GROWTH_BAR_ENDPOINT = "/insights/trends"

# Panel types of contract 1.1: {query key: allowed endpoints} required, and optional.
_SERIES = "/insights/series"
PANEL_QUERIES: dict[str, tuple[dict[str, set], dict[str, set]]] = {
    "waterfall": ({"main": {"/insights/movers"}}, {}),
    "choropleth": ({"main": {"/insights/ranking", "/insights/flows"}}, {}),
    "flowmap": ({"main": {"/insights/flows"}}, {}),
    "heatmap": ({"main": {_SERIES, "/insights/matrix", "/trends/seasonal-heatmap"}}, {"analysis": {"/trends/analyze"}}),
    "treemap": ({"main": {"/insights/tree"}}, {}),
    "butterfly": ({"in": {"/insights/ranking", "/insights/ranking/records"},
                   "out": {"/insights/ranking", "/insights/ranking/records"}}, {}),
    "bump": ({"main": {"/insights/matrix"}}, {}),
    "scatter": ({"main": {"/insights/specialisation"}}, {"growth": {"/insights/movers", "/insights/trends"}}),
}
HEATMAP_KINDS = {"month_year": _SERIES, "product_year": "/insights/matrix", "seasonal": "/trends/seasonal-heatmap"}
RESERVED_AGENTS = {"baseline"}
SELF_QUERY = "self"  # in a mark, a fact {"query": "self"} reads the mark's own query

PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}")
# Figures an LLM must not type: amounts and percentages. Years and codes are fine.
FIGURE_RE = re.compile(
    r"[€$£]\s?\d|\d(?:[\d.,]*\d)?\s?(?:%|‰|per ?cent\b|bn\b|billion\b|mn\b|million\b|thousand\b|k\b|m\b|eur\b|euros?\b)",
    re.IGNORECASE)


# --------------------------------------------------------------------------- schemas
def load_schema(name: str) -> dict:
    """``"view-spec"`` or ``"ops"``."""
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def load_baseline() -> dict:
    """The baseline spec (version 0)."""
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- the agents' area
# The agents share a few slots on the page: when an agent adds a NEW panel and the slots are full, the new
# panel takes the place (and the layout slot) of the oldest agent panel the user has not kept. A panel is
# kept when it is pinned or carries a kept mark; kept panels take a slot but are never replaced.
PAGE_OWNERS = ("baseline", "user")   # the page's own panels: never in the agents' area


def max_agent_panels() -> int:
    """How many agent panels the page holds (env VIEW_MAX_AGENT_PANELS, default 3, at least 1)."""
    try:
        return max(1, int(os.getenv("VIEW_MAX_AGENT_PANELS") or 3))
    except ValueError:
        return 3


@lru_cache(maxsize=1)
def baseline_panel_ids() -> frozenset:
    """The ids of the baseline's panels: an agent's rewrite of one (the headline) is not an agent panel."""
    try:
        return frozenset(p["id"] for p in load_baseline().get("panels") or [])
    except (OSError, ValueError):
        return frozenset()


def is_agent_panel(panel: dict, page_ids: Iterable[str]) -> bool:
    """A panel an agent added: owned by an agent (not baseline, not user) and not one of the page's own ids."""
    return panel.get("agent", "baseline") not in PAGE_OWNERS and panel.get("id") not in page_ids


def replaceable_panels(panels: Iterable[dict], marks: Iterable[dict], page_ids: Iterable[str]) -> list[str]:
    """Agent panels an agent may replace, oldest first: not pinned and carrying no kept mark.

    Oldest is the order of ``spec.panels``: a new panel is appended, and re-pointing an agent panel
    (upsert with its id) moves it to the end, so the panel replaced is the one least recently drawn."""
    page_ids = set(page_ids)
    kept = {m.get("target") for m in marks if m.get("status") == "kept"}
    return [p["id"] for p in panels if is_agent_panel(p, page_ids) and not p.get("pinned") and p["id"] not in kept]


def clearable_page_panels(panels: Iterable[dict], marks: Iterable[dict]) -> list[str]:
    """What ``clear`` with scope ``page`` (the user only) removes: every panel that is not kept, the page's
    own included. Kept: pinned, or carrying a kept mark (the mark stays, so its panel does)."""
    marks = list(marks)
    kept = {m["target"] for m in marks if m.get("status") == "kept"}
    return [p["id"] for p in panels if not p.get("pinned") and p["id"] not in kept]


def storyline_key(mark: dict) -> str:
    """The storyline a mark belongs to: its ``thread``, or the mark alone ('#m12')."""
    return mark.get("thread") or f"#{mark.get('id')}"


def number_storylines(spec: dict) -> dict:
    """Give every storyline its page number (contract 1.6): ``story_n`` on each of its marks.

    A storyline keeps the number it got when it first appeared: the number of its earliest mark that
    has one, unless an earlier storyline already holds that number (a mark moved to another thread),
    and a storyline without one takes ``next_story_n``. The counter never goes down here, so a
    number is never given to another storyline; a storyline whose marks are all removed loses its
    number for good. Marks without a number (a state from before 1.6) are numbered in order of ``n``.
    Changes ``spec`` in place and returns it."""
    marks = sorted(spec.get("marks") or [], key=lambda m: m.get("n", 0))
    groups: dict[str, list[dict]] = {}
    for m in marks:
        groups.setdefault(storyline_key(m), []).append(m)
    held = [m["story_n"] for m in marks if isinstance(m.get("story_n"), int)]
    counter = max([spec.get("next_story_n") or 1, *(n + 1 for n in held)])
    claimed: set[int] = set()
    for members in groups.values():
        number = next((m["story_n"] for m in members if isinstance(m.get("story_n"), int)), None)
        if number is None or number in claimed:
            number, counter = counter, counter + 1
        claimed.add(number)
        for m in members:
            m["story_n"] = number
    spec["next_story_n"] = counter
    return spec


def story_numbers(spec: Optional[dict]) -> dict[str, int]:
    """The page number of each mark id: its ``story_n`` (1.6), else its storyline's place in order of first n."""
    marks = sorted((spec or {}).get("marks") or [], key=lambda m: m.get("n", 0))
    order: list[str] = []
    for m in marks:
        if storyline_key(m) not in order:
            order.append(storyline_key(m))
    return {m["id"]: m["story_n"] if isinstance(m.get("story_n"), int) else order.index(storyline_key(m)) + 1
            for m in marks}


def _check_story_numbers(spec: dict) -> list[dict]:
    """1.6: one number per storyline, never two storylines on one number, all below next_story_n."""
    errors: list[dict] = []
    by_key: dict[str, int] = {}
    by_number: dict[int, str] = {}
    top = spec.get("next_story_n")
    for i, mark in enumerate(spec["marks"]):
        n = mark.get("story_n")
        if n is None:
            continue
        ptr, key = f"/marks/{i}/story_n", storyline_key(mark)
        if top is None or n >= top:
            errors.append(_err(ptr, "bad_story_number", f"storyline number {n} is not below next_story_n"))
        if by_key.setdefault(key, n) != n:
            errors.append(_err(ptr, "bad_story_number", f"the marks of storyline {key!r} carry two numbers "
                                                        f"({by_key[key]} and {n})"))
        if by_number.setdefault(n, key) != key:
            errors.append(_err(ptr, "bad_story_number", f"storyline number {n} is used by {by_number[n]!r} "
                                                        f"and {key!r}"))
    return errors


def panels_to_replace(panels: Iterable[dict], marks: Iterable[dict], page_ids: Iterable[str],
                      limit: Optional[int] = None) -> list[str]:
    """The panels a new agent panel pushes out, oldest first; the first gives the new one its layout slot.

    Every agent panel takes a slot, kept or not; only those not kept are replaced. Empty while there is
    room, or when every agent panel is kept (the page then grows: the user chose to keep them all). More
    than one only when the page already holds more than ``limit`` (a page from before the rule): it
    shrinks back to ``limit`` agent panels, as far as the kept ones allow."""
    panels, marks, page_ids = list(panels), list(marks), set(page_ids)
    total = sum(1 for p in panels if is_agent_panel(p, page_ids))
    surplus = total - (limit or max_agent_panels()) + 1
    return replaceable_panels(panels, marks, page_ids)[:surplus] if surplus > 0 else []


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    spec, ops = load_schema("view-spec"), load_schema("ops")
    registry = Registry().with_resources([
        (SPEC_SCHEMA_ID, Resource.from_contents(spec)),
        (OPS_SCHEMA_ID, Resource.from_contents(ops)),
    ])
    checker = FormatChecker()

    def sub(name: str) -> Draft202012Validator:
        return Draft202012Validator({"$ref": f"{SPEC_SCHEMA_ID}#/$defs/{name}"}, registry=registry,
                                    format_checker=checker)

    return {
        "spec": Draft202012Validator(spec, registry=registry, format_checker=checker),
        "ops": Draft202012Validator(ops, registry=registry, format_checker=checker),
        "mark_body": sub("mark_body"),
    }


def _pointer(parts: Iterable[Any]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _err(path: str, code: str, message: str) -> dict:
    return {"path": path, "code": code, "message": message}


def _schema_errors(validator: Draft202012Validator, instance: Any, prefix: str = "") -> list[dict]:
    out: list[dict] = []
    for e in validator.iter_errors(instance):
        parts = list(e.absolute_path)
        if e.validator == "additionalProperties" and isinstance(e.instance, dict):
            allowed = set((e.schema or {}).get("properties", {}))
            for k in e.instance:
                if k not in allowed:
                    out.append(_err(prefix + _pointer(parts + [k]), "unexpected",
                                    f"{k!r} is not allowed here (allowed: {', '.join(sorted(allowed))})"))
            continue
        if e.validator == "required" and isinstance(e.instance, dict):
            for k in e.validator_value:
                if k not in e.instance:
                    out.append(_err(prefix + _pointer(parts + [k]), "required", f"{k!r} is required"))
            continue
        if e.validator == "not" and e.validator_value == {}:  # a forbidden property, e.g. "y": {"not": {}}
            name = parts[-1] if parts else "this value"
            out.append(_err(prefix + _pointer(parts), "unexpected", f"{name!r} is not allowed here"))
            continue
        code = "unknown_endpoint" if e.validator == "enum" and parts and parts[-1] == "endpoint" else "schema"
        out.append(_err(prefix + _pointer(parts), code, e.message))
    seen, unique = set(), []
    for x in out:
        key = (x["path"], x["message"])
        if key not in seen:
            seen.add(key)
            unique.append(x)
    return sorted(unique, key=lambda x: x["path"])


# --------------------------------------------------------------------------- queries
def _dims(meta: Optional[dict]) -> tuple[set, dict[str, set]]:
    """(dimension ids, allowed values of enumerated dimensions) from /insights/meta."""
    if not meta:
        return set(DEFAULT_DIMENSIONS), {}
    ids, values = set(), {}
    for d in meta.get("dimensions", []):
        ids.add(d["id"])
        if d.get("values"):
            values[d["id"]] = {v["code"] for v in d["values"]}
    return ids, values


def _check_query(q: dict, ptr: str, meta: Optional[dict], errors: list[dict]) -> None:
    endpoint, params = q["endpoint"], q.get("params") or {}
    if endpoint not in ENDPOINT_PARAMS:
        errors.append(_err(ptr + "/endpoint", "unknown_endpoint", f"{endpoint!r} is not a known endpoint"))
        return
    required, optional = ENDPOINT_PARAMS[endpoint]
    dims, enum_values = _dims(meta)
    filters = set() if endpoint in NO_FILTERS else dims
    allowed = set(required) | set(optional) | filters
    for name, value in params.items():
        p = f"{ptr}/params/{name}"
        if name not in allowed:
            errors.append(_err(p, "unknown_param", f"{endpoint} does not take {name!r}; it takes "
                                                   f"{', '.join(sorted(allowed)) or 'no parameters'}"))
            continue
        if isinstance(value, str) and value.startswith("$"):
            if value not in PLACEHOLDERS:
                errors.append(_err(p, "bad_placeholder", f"{value!r} is not a placeholder; use "
                                                         f"{', '.join(sorted(PLACEHOLDERS))}"))
            elif name not in PLACEHOLDERS[value]:
                errors.append(_err(p, "bad_placeholder", f"{value} can stand for "
                                                         f"{', '.join(sorted(PLACEHOLDERS[value]))}, not {name!r}"))
            continue
        allowed_values = PARAM_VALUES.get((endpoint, name))
        if allowed_values is not None and value not in allowed_values:
            shown = sorted({str(v) for v in allowed_values})
            errors.append(_err(p, "bad_param", f"{endpoint} takes {name} {' or '.join(shown)}, not {value!r}"))
            continue
        if endpoint == "/insights/tree" and name == "threshold" and value != "auto" \
                and not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1):
            errors.append(_err(p, "bad_param", f"threshold is auto or a share between 0 and 1, not {value!r}"))
            continue
        if name in PERIOD_PARAMS and not (isinstance(value, str) and PERIOD_RE.match(value)):
            errors.append(_err(p, "bad_period", f"{name} must be a year (2025) or a month (2025-03), not {value!r}"))
        if name == "by" and value not in dims:
            errors.append(_err(p, "bad_param", f"by must be a dimension ({', '.join(sorted(dims))}), not {value!r}"))
        if name in enum_values and name in filters:
            bad = [v for v in str(value).split(",") if v not in enum_values[name] and not v.isdigit()]
            if bad:
                errors.append(_err(p, "bad_param", f"unknown {name} value(s) {bad}; known: "
                                                   f"{', '.join(sorted(enum_values[name]))}"))
    if endpoint in {"/insights/ranking", "/insights/ranking/records", "/insights/ladder"} and ("start" in params or "end" in params):
        start, end = params.get("start", params.get("period", "")), params.get("end", params.get("period", ""))
        # $start / $period follow the page context (1.4): range_start never puts start after the period.
        if any(isinstance(v, str) and v.startswith("$") for v in (start, end)):
            pass
        elif not (isinstance(start, str) and isinstance(end, str) and re.fullmatch(r"[0-9]{4}", start) and re.fullmatch(r"[0-9]{4}", end) and "0001" <= start <= end):
            errors.append(_err(ptr + "/params/start", "bad_period", "Use an annual range with start <= end (YYYY)."))
    for name in required:
        if name not in params:
            errors.append(_err(f"{ptr}/params/{name}", "missing_param", f"{endpoint} needs {name!r}"))


def check_figures(values: Iterable[tuple[str, Any]]) -> list[dict]:
    """No figure typed into what an agent writes beside its texts: chart titles, subtitles, mark labels and the
    page title, as (JSON pointer, value). Years (2025, 1999-2025) and codes are not figures."""
    errors = []
    for ptr, value in values:
        figure = FIGURE_RE.search(str(value)) if isinstance(value, str) and value else None
        if figure:
            errors.append(_err(ptr, "figure_in_text", f"{figure.group(0)!r} is typed in; a title or label names what "
                                                      "is shown, and figures come from the API through a {fact}"))
    return errors


def _check_text(owner: dict, ptr: str, facts: dict, errors: list[dict]) -> None:
    for field in ("text", "note"):
        text = owner.get(field)
        if not text:
            continue
        for name in PLACEHOLDER_RE.findall(text):
            if name not in facts:
                errors.append(_err(f"{ptr}/{field}", "unknown_fact", f"{{{name}}} has no fact; add it to 'facts'"))
        figure = FIGURE_RE.search(PLACEHOLDER_RE.sub("X", text))
        if figure:
            errors.append(_err(f"{ptr}/{field}", "figure_in_text",
                               f"{figure.group(0)!r} is typed in; figures must come from the API through a {{fact}}"))


def _check_facts(owner: dict, ptr: str, local_queries: set, meta: Optional[dict], errors: list[dict]) -> None:
    facts = owner.get("facts") or {}
    for name, fact in facts.items():
        fp = f"{ptr}/facts/{name}"
        if "op" in fact:
            for i, ref in enumerate(fact["of"]):
                if ref not in facts or ref == name:
                    errors.append(_err(f"{fp}/of/{i}", "unknown_fact", f"{ref!r} is not another fact here"))
        elif "query" in fact:
            if fact["query"] not in local_queries:
                where = ", ".join(sorted(local_queries)) or "none"
                errors.append(_err(f"{fp}/query", "unknown_query", f"no query {fact['query']!r} here (queries: {where})"))
        else:
            _check_query(fact, fp, meta, errors)
    # derived facts must not loop
    def deps(n: str, seen: tuple) -> bool:
        f = facts.get(n) or {}
        for ref in f.get("of", []):
            if ref in seen or deps(ref, seen + (ref,)):
                return True
        return False
    for name, fact in facts.items():
        if "op" in fact and deps(name, (name,)):
            errors.append(_err(f"{ptr}/facts/{name}", "fact_cycle", f"{name!r} depends on itself"))
    _check_text(owner, ptr, facts, errors)


def check_panel(panel: dict, ptr: str, meta: Optional[dict] = None) -> list[dict]:
    """Semantic checks of one (schema-valid) panel."""
    errors: list[dict] = []
    keys: list[str] = []
    for i, q in enumerate(panel.get("queries", [])):
        if q["key"] in keys:
            errors.append(_err(f"{ptr}/queries/{i}/key", "duplicate_key", f"query key {q['key']!r} is used twice"))
        keys.append(q["key"])
        _check_query(q, f"{ptr}/queries/{i}", meta, errors)
    if panel["type"] == "flow" and set(keys) != {"in", "out"}:
        errors.append(_err(f"{ptr}/queries", "bad_queries", "a flow panel has two queries keyed 'in' (imports by "
                                                            "partner) and 'out' (exports by partner)"))
    if panel["type"] in PANEL_QUERIES:
        _check_panel_queries(panel, ptr, errors)
    if is_comparison(panel):
        _check_compare(panel, ptr, errors)
    if is_growth_bar(panel):
        _check_growth_bar(panel, ptr, errors)
    _check_facts(panel, ptr, set(keys), meta, errors)
    if panel["type"] == "kpi" and panel.get("value") not in (panel.get("facts") or {}):
        errors.append(_err(f"{ptr}/value", "unknown_fact", f"value {panel.get('value')!r} is not one of the facts"))
    if panel.get("context"):
        errors += check_panel_context(panel["context"], panel, f"{ptr}/context")
    return errors


def panel_placeholders(panel: Optional[dict]) -> set:
    """The context placeholders a panel follows: those in its queries and inline facts ($period, ...)."""
    found = set()
    body = {k: v for k, v in (panel or {}).items() if k in ("queries", "facts")}
    for _, q in iter_queries(body):
        found |= {v for v in (q.get("params") or {}).values() if isinstance(v, str) and v in PLACEHOLDERS}
    return found


def check_panel_context(own: dict, panel: dict, ptr: str) -> list[dict]:
    """A panel's own context (1.5): only what the panel follows, a start only with a yearly period, no later."""
    errors: list[dict] = []
    uses = panel_placeholders(panel)
    words = {"period": "year", "start": "From year", "product": "goods"}
    for name in PANEL_CONTEXT_FIELDS:
        if own.get(name) is not None and not uses & set(PANEL_CONTEXT_PLACEHOLDERS[name]):
            errors.append(_err(f"{ptr}/{name}", "no_placeholder",
                               f"{panel.get('id')!r} does not follow the page's {words[name]} (its queries use no "
                               f"{' or '.join(PANEL_CONTEXT_PLACEHOLDERS[name])}), so it cannot have its own"))
    start, period = own.get("start"), own.get("period")
    if start is not None:
        if period is None:
            errors.append(_err(f"{ptr}/start", "bad_period", "a From year (start) needs its To year (period)"))
        elif not re.fullmatch(r"[0-9]{4}", str(period)):
            errors.append(_err(f"{ptr}/start", "bad_period", "a range runs over years: period is a year (2025)"))
        elif str(start) > str(period):
            errors.append(_err(f"{ptr}/start", "bad_period", f"start {start} is after period {period}"))
    return errors


def _check_panel_queries(panel: dict, ptr: str, errors: list[dict]) -> None:
    """The query keys, endpoints and options of the panel types of contract 1.1."""
    kind = panel["type"]
    required, optional = PANEL_QUERIES[kind]
    shapes = {**required, **optional}
    queries = panel.get("queries", [])
    keys = [q["key"] for q in queries]
    want = " and ".join(f"{k!r} ({' or '.join(sorted(e))})" for k, e in required.items())
    for k in required:
        if k not in keys:
            errors.append(_err(f"{ptr}/queries", "bad_queries", f"a {kind} panel needs a query keyed {want}"))
            break
    options = panel.get("options") or {}
    for i, q in enumerate(queries):
        qp, endpoint, params = f"{ptr}/queries/{i}", q["endpoint"], q.get("params") or {}
        if q["key"] not in shapes:
            errors.append(_err(f"{qp}/key", "bad_queries", f"a {kind} panel has no query {q['key']!r}; its queries "
                                                           f"are {', '.join(sorted(shapes))}"))
            continue
        if endpoint not in shapes[q["key"]]:
            errors.append(_err(f"{qp}/endpoint", "bad_queries", f"the {q['key']!r} query of a {kind} panel is on "
                                                                f"{' or '.join(sorted(shapes[q['key']]))}"))
            continue
        if kind == "heatmap" and q["key"] == "main":
            hk = options.get("kind")
            if hk in HEATMAP_KINDS and HEATMAP_KINDS[hk] != endpoint:
                errors.append(_err(f"{qp}/endpoint", "bad_queries",
                                   f"a {hk} heatmap reads {HEATMAP_KINDS[hk]}, not {endpoint}"))
            if endpoint == _SERIES and params.get("freq") != "M":
                errors.append(_err(f"{qp}/params/freq", "bad_param", "a month_year heatmap reads monthly data: freq M"))
            if endpoint == "/trends/seasonal-heatmap" and params.get("freq") not in ("M", "Q"):
                errors.append(_err(f"{qp}/params/freq", "bad_param", "a seasonal heatmap reads monthly or quarterly "
                                                                     "data: freq M or Q"))
        elif kind == "choropleth":
            measure = options.get("measure", "share")
            if measure == "balance" and endpoint != "/insights/flows":
                errors.append(_err(f"{qp}/endpoint", "bad_queries", "a balance map reads /insights/flows"))
            elif measure != "balance" and endpoint != "/insights/ranking":
                errors.append(_err(f"{qp}/endpoint", "bad_queries", f"a {measure} map reads /insights/ranking"))
            elif endpoint == "/insights/ranking" and params.get("by") != "partner":
                errors.append(_err(f"{qp}/params/by", "bad_param", "a map ranks partners: by partner"))
        elif kind == "bump" and params.get("measure") != "rank":
            errors.append(_err(f"{qp}/params/measure", "bad_param", "a bump chart reads ranks: measure rank"))
        elif kind == "butterfly":
            flow = {"in": "IMPORT", "out": "EXPORT"}[q["key"]]
            if params.get("flow") != flow:
                errors.append(_err(f"{qp}/params/flow", "bad_param", f"the {q['key']!r} side of a butterfly is "
                                                                     f"flow {flow}"))
    if kind == "butterfly" and {"in", "out"} <= set(keys):
        sides = {q["key"]: q.get("params") or {} for q in queries}
        for name in ("by", "period"):
            if sides["in"].get(name) != sides["out"].get(name):
                errors.append(_err(f"{ptr}/queries", "bad_queries", f"the two sides of a butterfly share {name}: "
                                                                    f"{sides['in'].get(name)!r} and "
                                                                    f"{sides['out'].get(name)!r} differ"))


def is_growth_bar(panel: dict) -> bool:
    """A bar of growth per year over a span (contract 1.7): options.measure "growth"."""
    return panel.get("type") == "bar" and (panel.get("options") or {}).get("measure") == "growth"


def _check_growth_bar(panel: dict, ptr: str, errors: list[dict]) -> None:
    """A growth bar (1.7) reads one query 'main' on /insights/trends, by partner or product, from a start year: the
    bars are each item's compound annual growth from start to end. A bar of one year's values cannot be one."""
    options = panel.get("options") or {}
    for name in BAR_COMPARE_OPTIONS:
        if name in options:
            errors.append(_err(f"{ptr}/options/{name}", "bad_param", "a growth bar ranks growth per year over a span; "
                                                                      "it takes no comparison options"))
    queries = panel.get("queries", [])
    if len(queries) != 1 or queries[0]["key"] != "main" or queries[0]["endpoint"] != GROWTH_BAR_ENDPOINT:
        errors.append(_err(f"{ptr}/queries", "bad_queries", "a growth bar (measure growth) reads one query keyed "
                                                            f"'main' on {GROWTH_BAR_ENDPOINT}"))
        return
    qp, params = f"{ptr}/queries/0/params", queries[0].get("params") or {}
    if params.get("by") not in ("partner", "product"):
        errors.append(_err(f"{qp}/by", "bad_param", "a growth bar ranks partners or products: by partner or product"))
    if params.get("method", "cagr") != "cagr":
        errors.append(_err(f"{qp}/method", "bad_param", "a growth bar draws the compound annual growth: method cagr "
                                                        "(or leave it out)"))
    start, end = params.get("start"), params.get("end")
    if start is None:
        errors.append(_err(f"{qp}/start", "missing_param", "a growth bar needs start: the growth per year since that "
                                                           "year"))
    elif end is not None and not str(start).startswith("$") and not str(end).startswith("$")             and str(start) >= str(end):
        errors.append(_err(f"{qp}/start", "bad_period", f"start {start} must be before end {end}"))


def is_comparison(panel: dict) -> bool:
    """A compare panel, a line with comparison options or a bar with comparison options (contract 1.2)."""
    options = panel.get("options") or {}
    if panel["type"] == "compare":
        return True
    if panel["type"] == "line":
        return any(k in options for k in LINE_COMPARE_OPTIONS)
    if panel["type"] == "bar":
        return any(k in options for k in BAR_COMPARE_OPTIONS)
    return False


def compare_layout(panel: dict) -> Optional[str]:
    """How a comparison is drawn: "series" (every query on /insights/series, one line each), "pair" (two
    snapshots keyed a and b, side by side) or None (neither)."""
    queries = panel.get("queries") or []
    pair = sorted(q.get("key") or "" for q in queries) == ["a", "b"]
    if panel["type"] == "line":
        return "series"
    if panel["type"] == "bar":
        return "pair" if pair else None
    if queries and all(q["endpoint"] == _SERIES for q in queries):
        return "series"
    return "pair" if pair else None


def _check_compare(panel: dict, ptr: str, errors: list[dict]) -> None:
    """The queries and options of a comparison (contract 1.2)."""
    kind, options = panel["type"], panel.get("options") or {}
    queries = panel.get("queries", [])
    keys = [q["key"] for q in queries]
    layout = compare_layout(panel)
    mode = options.get("compare", "absolute")
    op = f"{ptr}/options"

    def bad(path: str, code: str, message: str) -> None:
        errors.append(_err(path, code, message))

    if layout is None:
        if kind == "bar":
            bad(f"{ptr}/queries", "bad_queries", "a bar compares two snapshots: queries keyed 'a' and 'b' on the same "
                                                 "/insights/ranking or /insights/compare")
        else:
            bad(f"{ptr}/queries", "bad_queries", "a compare panel reads 1 to 6 series (every query on /insights/series) "
                                                 "or two snapshots keyed 'a' and 'b' (/insights/ranking or "
                                                 "/insights/compare)")
        return
    if layout == "series":
        for i, q in enumerate(queries):
            if q["endpoint"] != _SERIES:
                bad(f"{ptr}/queries/{i}/endpoint", "bad_queries", "the series of a comparison are read from "
                                                                  "/insights/series")
        if len(queries) > MAX_COMPARE_SERIES:
            bad(f"{ptr}/queries", "bad_queries", f"a comparison draws at most {MAX_COMPARE_SERIES} series")
        freqs = [str((q.get("params") or {}).get("freq", "A")) for q in queries]
        for i, f in enumerate(freqs):
            if f != freqs[0]:
                bad(f"{ptr}/queries/{i}/params/freq", "bad_param", f"the series of a comparison share one freq: "
                                                                   f"{freqs[0]} and {f} differ")
        ref = options.get("reference")
        if isinstance(ref, str):
            if ref not in keys:
                bad(f"{op}/reference", "unknown_query", f"no query {ref!r} here (queries: {', '.join(keys)})")
            elif mode not in ("diff", "share"):
                bad(f"{op}/reference", "bad_param", "reference is the series a diff subtracts or a share divides by; "
                                                    f"it means nothing in {mode} mode")
        if mode == "diff":
            if not isinstance(ref, str):
                bad(f"{op}/reference", "required", "a diff needs options.reference: the query subtracted from the "
                                                   "others")
            if len(queries) < 2:
                bad(f"{ptr}/queries", "bad_queries", "a diff needs at least two series")
        focus = options.get("focus")
        if focus is not None and focus not in keys:
            bad(f"{op}/focus", "unknown_query", f"focus names a query; no query {focus!r} here "
                                                f"(queries: {', '.join(keys)})")
        if options.get("facet") and len(queries) < 2:
            bad(f"{op}/facet", "bad_param", "small multiples need at least two series")
        if options.get("align") == "month":
            for i, f in enumerate(freqs):
                if f != "M":
                    bad(f"{ptr}/queries/{i}/params/freq", "bad_param", "align month lays monthly series over one "
                                                                       "January-to-December axis: freq M")
        if options.get("balance"):
            flows = sorted(str((q.get("params") or {}).get("flow", "")) for q in queries)
            if flows != ["EXPORT", "IMPORT"]:
                bad(f"{ptr}/queries", "bad_queries", "a balance compares exactly two series: one with flow EXPORT "
                                                     "and one with flow IMPORT")
            if mode != "absolute":
                bad(f"{op}/balance", "bad_param", f"the balance is drawn in absolute mode, not {mode}")
        for name in ("sort", "limit"):
            if name in options and kind == "compare":
                bad(f"{op}/{name}", "bad_param", f"{name} orders the rows of two snapshots; series over time have none")
        return
    # layout == "pair": two snapshots side by side
    sides = {q["key"]: q for q in queries}
    a, b = sides["a"], sides["b"]
    for side in (a, b):
        if side["endpoint"] not in PAIR_ENDPOINTS:
            i = queries.index(side)
            bad(f"{ptr}/queries/{i}/endpoint", "bad_queries", "two snapshots are read from /insights/ranking or "
                                                              "/insights/compare")
    if a["endpoint"] != b["endpoint"] or (a.get("params") or {}).get("by") != (b.get("params") or {}).get("by"):
        bad(f"{ptr}/queries", "bad_queries", "the two snapshots a and b share their endpoint and 'by'")
    elif (a.get("params") or {}) == (b.get("params") or {}):
        bad(f"{ptr}/queries", "bad_queries", "a and b are the same query; change the period, the flow or a filter")
    if mode == "index":
        bad(f"{op}/compare", "bad_param", "two snapshots compare in absolute, share, yoy (growth from a to b) or diff "
                                          "(b minus a) mode, not index")
    for name in ("facet", "align", "balance", "base_period", "reference"):
        if name in options:
            bad(f"{op}/{name}", "bad_param", f"{name} is for series over time, not for two snapshots a and b")


def _time_axis_name(panel: dict) -> str:
    return "line" if panel["type"] == "line" else "comparison over time"


def check_mark(mark: dict, ptr: str, panels: dict[str, dict], meta: Optional[dict] = None) -> list[dict]:
    """Semantic checks of one (schema-valid) mark against the panels it may target."""
    errors: list[dict] = []
    target = panels.get(mark["target"])
    kind, params = mark["kind"], mark.get("params") or {}
    if target is None:
        errors.append(_err(f"{ptr}/target", "unknown_target",
                           f"no panel {mark['target']!r} (panels: {', '.join(sorted(panels))})"))
    else:
        # marks on time: a line, or a compare panel of series over time (contract 1.2)
        timeline = target["type"] == "line" or (target["type"] == "compare" and compare_layout(target) == "series")
        if target["type"] not in MARK_ON[kind]:
            errors.append(_err(f"{ptr}/target", "wrong_target", f"a {kind} mark goes on a "
                                                                f"{' or '.join(sorted(MARK_ON[kind]))} panel, and "
                                                                f"{target['id']!r} is a {target['type']} panel"))
        elif kind == "highlight":
            if "codes" in params and timeline:
                errors.append(_err(f"{ptr}/params/codes", "wrong_target",
                                   f"on a {_time_axis_name(target)}, highlight a span (from, to)"))
            if "codes" not in params and not timeline:
                errors.append(_err(f"{ptr}/params", "wrong_target", f"on a {target['type']} panel, highlight codes"))
        elif kind == "event" and not timeline:
            errors.append(_err(f"{ptr}/target", "wrong_target", "an event goes on a time axis: a line, or a compare "
                                                                "panel of series over time"))
        elif kind == "trend_break" and target["type"] == "heatmap" \
                and (target.get("options") or {}).get("kind") == "seasonal":
            errors.append(_err(f"{ptr}/target", "wrong_target", "a seasonal heatmap has no time axis; put the trend "
                                                                "break on a month_year or product_year heatmap, or a line"))
        keys = {q["key"] for q in target.get("queries", [])}
        if mark.get("series") is not None and mark["series"] not in keys:
            errors.append(_err(f"{ptr}/series", "unknown_series", f"{target['id']!r} has no query "
                                                                  f"{mark['series']!r} (it has {', '.join(sorted(keys))})"))
        if kind == "annotation" and timeline and not PERIOD_RE.match(params.get("x", "")):
            errors.append(_err(f"{ptr}/params/x", "bad_period",
                               f"on a {_time_axis_name(target)}, x is a period (2020 or 2020-03)"))
    if kind == "event" and params.get("until") and params["until"] < params["at"]:
        errors.append(_err(f"{ptr}/params/until", "bad_period", "until is before at"))
    if kind == "highlight" and params.get("from") and params.get("to") and params["to"] < params["from"]:
        errors.append(_err(f"{ptr}/params/to", "bad_period", "to is before from"))
    if kind == "trend_break":
        facts = mark.get("facts") or {}
        for side in ("before", "after"):
            if params.get(side) and params[side] not in facts:
                errors.append(_err(f"{ptr}/params/{side}", "unknown_fact",
                                   f"{side} names the fact that holds the level {side} the break; "
                                   f"{params[side]!r} is not in 'facts'"))
        at = params.get("at", "")
        if params.get("from") and params["from"] > at:
            errors.append(_err(f"{ptr}/params/from", "bad_period", "from is after at"))
        if params.get("to") and params["to"] < at:
            errors.append(_err(f"{ptr}/params/to", "bad_period", "to is before at"))
    if mark.get("query"):
        _check_query(mark["query"], f"{ptr}/query", meta, errors)
    _check_facts(mark, ptr, {SELF_QUERY} if mark.get("query") else set(), meta, errors)
    return errors


# --------------------------------------------------------------------------- spec
def validate_spec(spec: dict, meta: Optional[dict] = None) -> list[dict]:
    """Errors of a whole spec (empty list: valid)."""
    errors = _schema_errors(_validators()["spec"], spec)
    if errors:
        return errors
    panels: dict[str, dict] = {}
    for i, panel in enumerate(spec["panels"]):
        if panel["id"] in panels:
            errors.append(_err(f"/panels/{i}/id", "duplicate_id", f"panel id {panel['id']!r} is used twice"))
        panels[panel["id"]] = panel
        errors += check_panel(panel, f"/panels/{i}", meta)
    errors += _check_layout(spec["layout"]["rows"], "/layout/rows", panels)
    numbers: set[int] = set()
    for i, mark in enumerate(spec["marks"]):
        ptr = f"/marks/{i}"
        if mark["id"] != f"m{mark['n']}":
            errors.append(_err(f"{ptr}/id", "bad_mark_id", f"mark {mark['n']} must have id 'm{mark['n']}'"))
        if mark["n"] in numbers:
            errors.append(_err(f"{ptr}/n", "duplicate_id", f"mark number {mark['n']} is used twice"))
        if mark["n"] >= spec["next_mark_n"]:
            errors.append(_err(f"{ptr}/n", "bad_mark_id", f"mark number {mark['n']} is not below next_mark_n"))
        numbers.add(mark["n"])
        errors += check_mark(mark, ptr, panels, meta)
    errors += _check_story_numbers(spec)
    return errors


def _check_layout(rows: list[dict], ptr: str, panels: dict[str, dict]) -> list[dict]:
    errors: list[dict] = []
    placed: set[str] = set()
    for r, row in enumerate(rows):
        for j, pid in enumerate(row["panels"]):
            p = f"{ptr}/{r}/panels/{j}"
            if pid not in panels:
                errors.append(_err(p, "unknown_panel", f"no panel {pid!r}"))
            elif pid in placed:
                errors.append(_err(p, "duplicate_in_layout", f"{pid!r} is placed twice"))
            placed.add(pid)
    missing = [pid for pid in panels if pid not in placed]
    if missing:
        errors.append(_err(ptr, "not_in_layout", f"the layout leaves out {', '.join(missing)}"))
    return errors


# --------------------------------------------------------------------------- ops
def validate_ops(envelope: dict, spec: dict, meta: Optional[dict] = None,
                 page_ids: Optional[Iterable[str]] = None, limit: Optional[int] = None) -> list[dict]:
    """Errors of a batch of ops against the current spec (empty list: it can be applied).

    Ops are checked in order on a simulated copy of the spec, so a mark may target a panel
    upserted earlier in the same batch, and update/keep/remove may name an earlier tmp_id.
    The simulation replaces agent panels as the store does (``panels_to_replace``): a later op
    that names a replaced panel or one of its marks is refused. ``page_ids``: the baseline's panel
    ids (default: baseline.json's); ``limit``: the agents' slots (default ``max_agent_panels()``).
    """
    page_ids = set(baseline_panel_ids() if page_ids is None else page_ids)
    if isinstance(envelope, dict) and isinstance(envelope.get("ops"), list) and len(envelope["ops"]) > MAX_OPS:
        return [_err("/ops", "too_many_ops", f"{len(envelope['ops'])} ops; a batch has at most {MAX_OPS}")]
    errors = _schema_errors(_validators()["ops"], envelope)
    if errors:
        return errors
    agent = envelope["agent"]["name"]
    if agent in RESERVED_AGENTS:
        errors.append(_err("/agent/name", "reserved_agent", f"{agent!r} is reserved for the baseline"))
    panels: dict[str, dict] = {p["id"]: p for p in spec.get("panels", [])}
    layout: list[list[str]] = [list(r["panels"]) for r in spec.get("layout", {}).get("rows", [])]
    marks: dict[str, dict] = {m["id"]: m for m in spec.get("marks", [])}
    tmp_ids: set[str] = set()

    def drop_panel(pid: str) -> None:
        panels.pop(pid, None)
        for row in layout:
            if pid in row:
                row.remove(pid)
        layout[:] = [row for row in layout if row]

    for i, op in enumerate(envelope["ops"]):
        p = f"/ops/{i}"
        kind = op["op"]
        if kind == "upsert_panel":
            panel = op["panel"]
            errors += check_panel(panel, f"{p}/panel", meta)
            if agent != "user":   # the page's own panels (and the person's) are not the assistant's words
                errors += check_figures((f"{p}/panel/{k}", panel.get(k)) for k in ("title", "subtitle"))
            old = panels.get(panel["id"])
            if old and old.get("pinned") and agent != "user":
                errors.append(_err(f"{p}/panel/id", "pinned", f"{panel['id']!r} is pinned by the user"))
            if op.get("after") and op["after"] not in panels:
                errors.append(_err(f"{p}/after", "unknown_panel", f"no panel {op['after']!r}"))
            pinned = panel["pinned"] if agent == "user" and "pinned" in panel else bool(old and old.get("pinned"))
            new = {**panel, "agent": agent, "pinned": bool(pinned)}
            if old is None:
                gone = (panels_to_replace(panels.values(), marks.values(), page_ids, limit)
                        if is_agent_panel(new, page_ids) else [])
                slot = next((row for row in layout if gone and gone[0] in row), None)
                for pid in gone:
                    for mid in [mid for mid, m in marks.items() if m["target"] == pid]:
                        marks.pop(mid)
                    if slot is not None and pid == gone[0]:
                        slot[slot.index(pid)] = panel["id"]
                        panels.pop(pid, None)
                    else:
                        drop_panel(pid)
                if slot is None:
                    layout.append([panel["id"]])
            elif is_agent_panel(new, page_ids):
                panels.pop(panel["id"])   # re-pointed: the newest agent panel now
            panels[panel["id"]] = new
        elif kind == "remove_panel":
            pid = op["id"]
            if pid not in panels:
                errors.append(_err(f"{p}/id", "unknown_panel", f"no panel {pid!r}"))
                continue
            if panels[pid].get("pinned"):
                errors.append(_err(f"{p}/id", "pinned", f"{pid!r} is pinned; unpin it first"))
            on_it = [mid for mid, m in marks.items() if m["target"] == pid]
            if on_it and not op.get("force"):
                errors.append(_err(f"{p}/id", "has_marks", f"{pid!r} carries marks {', '.join(on_it)}; "
                                                           "remove them or pass force"))
            for mid in on_it:
                marks.pop(mid)
            drop_panel(pid)
        elif kind == "set_layout":
            errors += _check_layout(op["rows"], f"{p}/rows", panels)
            layout[:] = [list(r["panels"]) for r in op["rows"]]
        elif kind == "add_mark":
            mark = op["mark"]
            if mark.get("agent") not in (None, agent):
                errors.append(_err(f"{p}/mark/agent", "agent_mismatch", f"the batch is sent by {agent!r}"))
            errors += check_mark(mark, f"{p}/mark", panels, meta)
            if agent != "user":
                errors += check_figures([(f"{p}/mark/params/label", (mark.get("params") or {}).get("label"))])
            tmp = op.get("tmp_id")
            if tmp:
                if tmp in tmp_ids:
                    errors.append(_err(f"{p}/tmp_id", "duplicate_tmp_id", f"tmp_id {tmp!r} is used twice"))
                tmp_ids.add(tmp)
            marks[tmp or f"#new{i}"] = {**mark, "status": "proposed", "agent": agent}
        elif kind == "update_mark":
            mid = op["id"]
            if mid not in marks:
                errors.append(_err(f"{p}/id", "unknown_mark", f"no mark {mid!r}"))
                continue
            merged = {k: v for k, v in {**marks[mid], **op["set"]}.items()
                      if k not in ("id", "n", "story_n", "status", "agent", "provenance")}
            sub_errors = _schema_errors(_validators()["mark_body"], merged)
            sub_errors = sub_errors or check_mark(merged, "", panels, meta)
            for e in sub_errors:
                head = e["path"].split("/")[1] if e["path"] else ""
                e["path"] = f"{p}/set{e['path']}" if head in op["set"] else f"{p}/id"
            errors += sub_errors
            if agent != "user" and isinstance(op["set"].get("params"), dict):
                errors += check_figures([(f"{p}/set/params/label", op["set"]["params"].get("label"))])
            marks[mid] = {**marks[mid], **op["set"]}
        elif kind in ("keep_mark", "remove_mark"):
            mid = op["id"]
            if mid not in marks:
                errors.append(_err(f"{p}/id", "unknown_mark", f"no mark {mid!r}"))
                continue
            if kind == "keep_mark":
                marks[mid] = {**marks[mid], "status": "kept"}
            else:
                marks.pop(mid)
        elif kind == "pin_panel":
            if op["id"] not in panels:
                errors.append(_err(f"{p}/id", "unknown_panel", f"no panel {op['id']!r}"))
            elif agent != "user" and panels[op["id"]].get("pinned") and not op["pinned"]:
                # only the person unpins: a pinned panel is never changed by an agent
                errors.append(_err(f"{p}/pinned", "pinned", f"{op['id']!r} is pinned by the user; only the user "
                                                            "unpins it"))
            else:
                panels[op["id"]] = {**panels[op["id"]], "pinned": op["pinned"]}
        elif kind == "clear" and op["scope"] == "page":
            # The person empties the page: every panel not kept goes, the page's own included.
            if agent != "user":
                errors.append(_err(f"{p}/scope", "user_only", "only the user clears the whole page; an agent "
                                                              "clears its own work (agent_panels, marks, all)"))
                continue
            for pid in clearable_page_panels(panels.values(), marks.values()):
                for mid in [mid for mid, m in marks.items() if m["target"] == pid]:
                    marks.pop(mid)
                drop_panel(pid)
            for mid, m in list(marks.items()):
                if m.get("status") != "kept":
                    marks.pop(mid)
        elif kind == "clear":
            who = op.get("agent")
            if op["scope"] in ("agent_panels", "all"):
                for pid, panel in list(panels.items()):
                    owner = panel.get("agent", "baseline")
                    kept = any(m["target"] == pid and m.get("status") == "kept" for m in marks.values())
                    # the page's own edits (agent "user", e.g. the year control) are not agent panels
                    if owner not in ("baseline", "user") and not panel.get("pinned") and not kept \
                            and who in (None, owner):
                        for mid in [mid for mid, m in marks.items() if m["target"] == pid]:
                            marks.pop(mid)
                        if pid in page_ids:   # a rewritten baseline panel goes back to its baseline version
                            panels[pid] = {**panel, "agent": "baseline"}
                        else:
                            drop_panel(pid)
            if op["scope"] in ("marks", "all"):
                for mid, m in list(marks.items()):
                    if m.get("status") != "kept" and who in (None, m.get("agent")):
                        marks.pop(mid)
        elif kind == "set_title":
            if agent != "user":
                errors += check_figures([(f"{p}/title", op["title"])])
        elif kind == "set_panel_context":
            # 1.5: one panel's own year, range or goods; data range and product codes are the router's checks
            pid = op["id"]
            if pid not in panels:
                errors.append(_err(f"{p}/id", "unknown_panel", f"no panel {pid!r}"))
                continue
            if panels[pid].get("pinned") and agent != "user":
                errors.append(_err(f"{p}/id", "pinned", f"{pid!r} is pinned by the user"))
            own = {k: op[k] for k in PANEL_CONTEXT_FIELDS if op.get(k) is not None}
            errors += check_panel_context(own, panels[pid], p)
            panels[pid] = {**{k: v for k, v in panels[pid].items() if k != "context"}, **({"context": own} if own else {})}
        # set_context (persona included: its pattern is the schema's): nothing to check beyond the schema
    return errors


# --------------------------------------------------------------------------- helpers for the store
def iter_queries(obj: Any, ptr: str = "") -> list[tuple[str, dict]]:
    """Every query in a spec, an envelope, a panel or a mark, as (JSON pointer, {endpoint, params}).

    This is what the backend pre-flights: panel queries, mark queries and inline facts.
    """
    found: list[tuple[str, dict]] = []
    if isinstance(obj, dict):
        if isinstance(obj.get("endpoint"), str) and isinstance(obj.get("params"), dict):
            found.append((ptr, {"endpoint": obj["endpoint"], "params": obj["params"]}))
        for k, v in obj.items():
            if k != "params":
                found += iter_queries(v, f"{ptr}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += iter_queries(v, f"{ptr}/{i}")
    return found


def context_of(spec: Optional[dict]) -> dict:
    """The page context of a spec: {period, product, flow?, persona?}; a spec without one reads as 2025, TOTAL."""
    return {**DEFAULT_CONTEXT, **((spec or {}).get("context") or {})}


def panel_context(context: Optional[dict], panel: Optional[dict]) -> dict:
    """The context a panel's queries, facts and marks are filled from (1.5): the page's, with the panel's own
    context on top. A panel period without its own start is a single period (the page's From does not apply)."""
    ctx = {**DEFAULT_CONTEXT, **(context or {})}
    own = {k: v for k, v in ((panel or {}).get("context") or {}).items()
           if k in PANEL_CONTEXT_FIELDS and isinstance(v, str) and v}
    if "period" in own and "start" not in own:
        ctx.pop("start", None)
    return {**ctx, **own}


def batch_query_contexts(envelope: dict, spec: Optional[dict]) -> list[tuple[str, dict, dict]]:
    """(JSON pointer, query, context) of every query a batch adds or changes (in an upserted panel, an added
    mark or a mark update), each with the context the page will fill it from once the batch is applied: the
    page's (with every set_context of the batch), and the own context (1.5) of the panel it is on."""
    ops = [op for op in (envelope or {}).get("ops") or [] if isinstance(op, dict)]
    page = context_of(spec)
    own = {p.get("id"): p.get("context") for p in (spec or {}).get("panels") or [] if isinstance(p, dict)}
    targets = {m.get("id"): m.get("target") for m in (spec or {}).get("marks") or [] if isinstance(m, dict)}
    for op in ops:
        kind = op.get("op")
        if kind == "set_context":
            page = {**page, **{k: op[k] for k in ("period", "product", "flow", "start", "persona") if k in op}}
        elif kind == "upsert_panel" and isinstance(op.get("panel"), dict):
            own[op["panel"].get("id")] = op["panel"].get("context")
        elif kind == "set_panel_context":
            own[op.get("id")] = {k: op[k] for k in PANEL_CONTEXT_FIELDS if op.get(k) is not None} or None
        elif kind == "add_mark" and op.get("tmp_id") and isinstance(op.get("mark"), dict):
            targets[op["tmp_id"]] = op["mark"].get("target")
    out = []
    for i, op in enumerate(ops):
        for field in ("panel", "mark", "set"):
            body = op.get(field)
            if not isinstance(body, dict):
                continue
            if field == "panel":
                pid = body.get("id")
            elif field == "mark":
                pid = body.get("target")
            else:
                pid = body.get("target") or targets.get(op.get("id"))
            ctx = panel_context(page, {"context": own.get(pid)})
            body = {k: v for k, v in body.items() if k != "provenance"}
            out += [(ptr, q, ctx) for ptr, q in iter_queries(body, f"/ops/{i}/{field}")]
    return out


def previous_period(period: str) -> str:
    """The same period a year earlier: 2025 -> 2024, 2025-03 -> 2024-03."""
    return f"{int(period[:4]) - 1:04d}{period[4:]}"


def fill_params(params: Optional[dict], context: Optional[dict] = None) -> dict:
    """Query params with the placeholders $period, $previous and $product replaced from a page context
    (DEFAULT_CONTEXT where it says nothing)."""
    ctx = {**DEFAULT_CONTEXT, **(context or {})}
    values = {"$period": ctx["period"], "$previous": previous_period(ctx["period"]), "$start": range_start(ctx),
              "$product": ctx["product"]}
    return {k: values.get(v, v) if isinstance(v, str) else v for k, v in (params or {}).items()}


def range_start(context: Optional[dict]) -> str:
    """What $start stands for (contract 1.4): context.start when it is a year no later than a yearly
    period, else the period itself (a single period). So moving the page before the range's start, or
    to a month, never asks for a range that ends before it begins."""
    ctx = {**DEFAULT_CONTEXT, **(context or {})}
    period, start = str(ctx["period"]), ctx.get("start")
    if isinstance(start, str) and re.fullmatch(r"[0-9]{4}", start) and re.fullmatch(r"[0-9]{4}", period) and start <= period:
        return start
    return period


def query_url(query: dict, context: Optional[dict] = None) -> str:
    """The relative URL of a query: /insights/ranking?by=partner&period=2025&flow=IMPORT.

    Context placeholders are filled from ``context`` (the spec's, see ``context_of``; by default
    2025 and TOTAL), so the pre-flight asks the API what the page will ask.
    """
    params = {k: (str(v).lower() if isinstance(v, bool) else v)
              for k, v in fill_params(query.get("params"), context).items()}
    return query["endpoint"] + (f"?{urlencode(params, safe=',')}" if params else "")


_SEGMENT_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)=([^\].]+)\]|(#length|#position)|(-?\d+)|([A-Za-z_][A-Za-z0-9_]*)")


def resolve_path(data: Any, path: str) -> Any:
    """The value at a fact path (see $defs/path in the spec schema); KeyError when it is not there."""
    cur, position = data, None
    for seg in path.split("."):
        m = _SEGMENT_RE.fullmatch(seg)
        if not m:
            raise KeyError(f"bad path segment {seg!r}")
        field, want, special, index, key = m.groups()
        if field:
            if not isinstance(cur, list):
                raise KeyError(f"{seg}: not a list")
            for pos, item in enumerate(cur, 1):
                got = item.get(field) if isinstance(item, dict) else None
                if (str(got).lower() if isinstance(got, bool) else str(got)) == want:
                    cur, position = item, pos
                    break
            else:
                raise KeyError(f"no item with {field}={want}")
            continue
        if special == "#length":
            cur = len(cur)
        elif special == "#position":
            if position is None:
                raise KeyError("#position must follow [field=value]")
            cur = position
        elif index is not None:
            if not isinstance(cur, list) or not -len(cur) <= int(index) < len(cur):
                raise KeyError(f"{seg}: no such item")
            cur = cur[int(index)]
        else:
            if not isinstance(cur, dict) or key not in cur:
                raise KeyError(f"no key {key!r}")
            cur = cur[key]
        position = None
    return cur
