# SPDX-License-Identifier: MIT
"""Reading the page (GET /view): what is drawn, by whom, under which number.

The page numbers a storyline (marks that share ``thread``) once: the backend gives it a number when its
first mark is added (``story_n``, contract 1.6) and the storyline keeps it, so replacing a chart or
removing another storyline never renumbers it. A chat reply cites that number ("[2]"), not the raw
``n``. ``summary`` is the compact text a specialist reads before it acts, so a follow-up ("tell me more about [1]", "remove the forecast")
can refer to what is already there and nothing is drawn twice.
"""
from __future__ import annotations

import json
import os
from typing import Iterable, Optional

from .settings import ROOT

BASELINE_PANELS = ("headline", "kpi_imports", "kpi_balance", "kpi_exports", "crossroads", "trend", "mix_export",
                   "specialisation", "peers")


# The page's own panels, in words, for a page where the user removed some of them.
PANEL_WORDS = {"headline": "the 'In short' headline", "kpi_imports": "the imports figure",
               "kpi_balance": "the balance figure", "kpi_exports": "the exports figure",
               "crossroads": "the crossroads of partners", "trend": "the trend line of imports and exports",
               "mix_export": "what Luxembourg sells", "specialisation": "where it stands out",
               "peers": "among the 27 EU countries"}


def missing_panels(spec: dict) -> list[str]:
    """The page's own panels the user removed (ids, in the baseline's order)."""
    there = {p["id"] for p in spec.get("panels", [])}
    return [pid for pid in BASELINE_PANELS if pid not in there]


def baseline() -> dict:
    """The page as it starts (version 0)."""
    path = ROOT / "app" / "backend" / "view" / "baseline.json"
    if not path.exists():
        path = ROOT / "contract" / "examples" / "initial.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _key(m: dict) -> str:
    return m.get("thread") or m["id"]


def number_of(spec: dict) -> dict[str, int]:
    """The page number of each mark id: its storyline's ``story_n`` (1.6); a spec from before 1.6 has
    none, and then the number is the storyline's place in order of first ``n``, as the page shows it."""
    marks = sorted(spec.get("marks", []), key=lambda m: m["n"])
    order: list[str] = []
    for m in marks:
        if _key(m) not in order:
            order.append(_key(m))
    return {m["id"]: m["story_n"] if isinstance(m.get("story_n"), int) else order.index(_key(m)) + 1
            for m in marks}


def numbers(spec: dict) -> list[int]:
    """The page numbers on the page now, smallest first (the [n] a reply may cite)."""
    return sorted(set(number_of(spec).values()))


def storylines(spec: dict) -> list[Optional[str]]:
    """Storyline keys (thread, or the mark id) by page number: item n-1 is storyline n, None for a number
    no longer on the page (numbers are stable, so there are gaps). Use ``numbers`` for the numbers shown."""
    nums = number_of(spec)
    out: list[Optional[str]] = [None] * max(nums.values(), default=0)
    for m in spec.get("marks", []):
        out[nums[m["id"]] - 1] = _key(m)
    return out


def page_numbers(spec: dict, mark_ids: Iterable[str]) -> list[int]:
    """The numbers the page shows for these marks: one per storyline."""
    nums = number_of(spec)
    out: list[int] = []
    for mid in mark_ids:
        n = nums.get(mid)
        if n is not None and n not in out:
            out.append(n)
    return out


def marks_of_number(spec: dict, number: int) -> list[dict]:
    """The marks behind the page number ``number`` (a storyline), or []."""
    nums = number_of(spec)
    return [m for m in spec.get("marks", []) if nums.get(m["id"]) == number]


def owner_of_number(spec: dict, number: int) -> Optional[str]:
    marks = marks_of_number(spec, number)
    return marks[0].get("agent") if marks else None


def panel(spec: dict, pid: str) -> Optional[dict]:
    return next((p for p in spec.get("panels", []) if p["id"] == pid), None)


def trend_panel(spec: dict) -> Optional[dict]:
    """The line panel with imports and exports (the baseline's ``trend``), where bands and events go."""
    p = panel(spec, "trend")
    if p and p["type"] == "line":
        return p
    return next((p for p in spec.get("panels", []) if p["type"] == "line"
                 and {"imports", "exports"} <= {q["key"] for q in p.get("queries", [])}), None)


def last_agent_line_panel(spec: dict, series: str) -> Optional[dict]:
    """The most recently drawn agent line chart with a ``series`` query (e.g. a partner-filtered
    "exports" line) — the chart on screen a trend/band/event mark should attach to when it is not
    the baseline imports+exports panel that ``trend_panel`` matches."""
    charts, _ = agent_charts(spec)
    candidates = [p for p in charts if p["type"] == "line" and series in {q["key"] for q in p.get("queries", [])}]
    return candidates[-1] if candidates else None


def agent_charts(spec: dict) -> tuple[list[dict], int]:
    """The charts the agents added (oldest first, as the backend counts them) and how many the page keeps."""
    try:
        limit = max(1, int(os.getenv("VIEW_MAX_AGENT_PANELS") or 3))
    except ValueError:
        limit = 3
    return [p for p in spec.get("panels", []) if p.get("agent", "baseline") not in ("baseline", "user")
            and p["id"] not in BASELINE_PANELS], limit


def next_replaced(spec: dict) -> Optional[dict]:
    """The agent chart a new one would take the place of: the oldest not kept, when the slots are full."""
    charts, limit = agent_charts(spec)
    if len(charts) < limit:
        return None
    kept = {m["target"] for m in spec.get("marks", []) if m.get("status") == "kept"}
    return next((p for p in charts if not p.get("pinned") and p["id"] not in kept), None)


def _label(m: dict) -> str:
    p = m.get("params") or {}
    bits = []
    for k in ("label", "at", "x", "in_code", "out_code", "product", "codes", "from", "to"):
        if p.get(k) not in (None, "", []):
            v = p[k]
            bits.append(f"{k}={','.join(v) if isinstance(v, list) else v}")
    return " ".join(bits)


def _params(q: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in (q.get("params") or {}).items())


def _reader(persona_id: str) -> str:
    """'journalist (Journalist: The story, one chart and a sentence you can quote)' for the context line."""
    try:
        from agents.kit.personas import profile
        p = profile(persona_id)
    except Exception:  # the kit is not on the path: the id alone
        p = None
    return f"{persona_id} ({p['label']}: {p.get('tagline', '')})" if p else f"{persona_id} (every reader)"


def summary(spec: dict, me: Optional[str] = None, max_note: int = 90) -> str:
    """The page in a few lines, for a prompt: title, context, panels, numbered marks."""
    ctx = spec.get("context") or {"period": "2025", "product": "TOTAL"}
    lines = [f"Page version {spec.get('version')}; title: {spec.get('title')!r}; "
             f"context: period {ctx.get('period')}, product {ctx.get('product')}"
             + (f", flow {ctx['flow']}" if ctx.get("flow") else "")
             + (f", from {ctx['start']} (the page's charts of one year sum {ctx['start']} to {ctx.get('period')})"
                if ctx.get("start") and str(ctx["start"]) < str(ctx.get("period")) else "")
             + (f"; read by: {_reader(ctx['persona'])}" if ctx.get("persona") else "")]
    lines.append("Panels (id, type, title, queries; who added it):")
    for p in spec.get("panels", []):
        who = p.get("agent", "baseline")
        qs = "; ".join(f"{q['key']}:{q['endpoint']}?{_params(q)}" for q in p.get("queries", []))
        if len(qs) > 160:
            qs = qs[:157] + "..."
        flags = (" PINNED by the user: never change or remove it (marks on it are fine)" if p.get("pinned") else "")
        own = p.get("context") or {}
        if own:  # contract 1.5: the chart's own year/goods win over the page context; set_context does not move it
            flags += (" OWN CONTEXT " + ", ".join(f"{k} {own[k]}" for k in ("start", "period", "product") if own.get(k))
                      + ": this chart shows that, not the page context")
        lines.append(f"- {p['id']} ({p['type']}) {p.get('title', '')!r} [{qs}] by {who}{flags}")
    gone = missing_panels(spec)
    if not spec.get("panels"):
        lines.append("- none: the page is EMPTY (the user removed every chart). Build the answer from nothing: the "
                     "first chart you add goes at the top. Never refer to the crossroads, the trend line, the headline "
                     "or the three figures; your tools draw a suitable chart when a recipe needs one.")
    elif gone:
        lines.append("The user removed these page panels: " + "; ".join(f"{pid} ({PANEL_WORDS.get(pid, pid)})"
                                                                      for pid in gone)
                     + ". Never refer to them; your tools draw a suitable chart when a recipe needs one.")
    charts, limit = agent_charts(spec)
    nxt = next_replaced(spec)
    lines.append(f"Agent charts: {len(charts)} of the {limit} the page keeps"
                 + (f"; a NEW chart would take the place of {nxt['id']} {nxt.get('title', '')!r} (re-draw your own "
                    "chart of the same kind instead, or say what it replaces)" if nxt else "; room for a new one"))
    nums = number_of(spec)
    marks = sorted(spec.get("marks", []), key=lambda m: m["n"])
    if not marks:
        lines.append("Marks: none yet.")
    else:
        lines.append("Marks (page number [n], mark id, agent, kind on target, params, status, note):")
        for m in marks:
            n = nums[m["id"]]
            note = (m.get("note") or "").replace("\n", " ")
            if len(note) > max_note:
                note = note[: max_note - 3] + "..."
            mine = " (yours)" if me and m.get("agent") == me else ""
            lines.append(f"- [{n}] {m['id']} {m.get('agent')}{mine}: {m['kind']} on {m['target']}"
                         f"{' series=' + m['series'] if m.get('series') else ''} {_label(m)}"
                         f"{' period=' + m['period'] if m.get('period') else ''}; {m.get('status')}; "
                         f"{note!r}")
    return "\n".join(lines)
