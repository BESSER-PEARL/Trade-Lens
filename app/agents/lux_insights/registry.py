# SPDX-License-Identifier: MIT
"""What each specialist tells POST /agents/register: title, pen, capabilities, two example questions.

Titles, pens and the one-line "can" come from agents.kit.AGENTS, so the page, the docs and the agent
agree. The page shows ``capabilities[0].description`` on each agent card.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import httpx

from .tools import AGENTS, ROLES, ViewClient

log = logging.getLogger("lux_insights.registry")

CAPABILITIES: dict[str, list[dict]] = {
    "trend_finder": [
        {"kind": "find_movers", "produces": ["thread", "highlight", "trend_break", "text", "waterfall", "heatmap",
                                             "bump"],
         "description": AGENTS["trend_finder"]["can"], "inputs": ["period", "previous", "flow", "product"]},
        {"kind": "find_breaks", "produces": ["trend_break", "heatmap"],
         "description": "Finds lasting shifts in a series since 1999 (change points)", "inputs": ["flow", "start"]},
    ],
    "cause_finder": [
        {"kind": "match_events", "produces": ["event", "waterfall", "annotation"],
         "description": AGENTS["cause_finder"]["can"], "inputs": ["start", "category"]},
    ],
    "forecaster": [
        {"kind": "forecast", "produces": ["forecast", "text", "heatmap"],
         "description": AGENTS["forecaster"]["can"], "inputs": ["flow", "partner", "product", "horizon"]},
    ],
    "visualizer": [
        {"kind": "show", "produces": ["line", "bar", "choropleth", "flowmap", "heatmap", "treemap", "butterfly",
                                      "bump", "scatter", "annotation", "highlight", "thread"],
         "description": AGENTS["visualizer"]["can"], "inputs": ["partner", "product", "period", "flow"]},
        {"kind": "context", "produces": ["set_context"], "description": "Moves the page to another year or goods",
         "inputs": ["period", "product"]},
        {"kind": "clear", "produces": ["clear"],
         "description": "Clears what the agents drew; kept marks and pinned panels stay", "inputs": []},
    ],
}

EXAMPLES: dict[str, list[str]] = {
    "trend_finder": ["Why did exports jump in 2025?", "When did exports shift for good since 1999?"],
    "cause_finder": ["Which events line up with the changes since 1999?", "What did COVID-19 do to trade?"],
    "forecaster": ["What do we expect for exports next year?", "Forecast imports for 2026"],
    "visualizer": ["Show me where the goods go on a map", "Compare Luxembourg with Belgium"],
}


def registration(name: str) -> dict:
    """The body of POST /agents/register for one specialist (without the name, which ViewClient adds)."""
    a = AGENTS[name]
    return {"title": a["title"], "color": a["color"], "capabilities": CAPABILITIES[name], "examples": EXAMPLES[name]}


def register_all(api_url: str, heartbeat: bool = True) -> tuple[list[threading.Event], list[str]]:
    """Register the four specialists (and keep them alive every 30 s). Returns (stop events, warnings)."""
    stops, warnings = [], []
    for name in ROLES:
        view = ViewClient(api_url, name)
        try:
            view.register(**registration(name))
        except (httpx.HTTPError, RuntimeError) as exc:
            warnings.append(f"{name}: could not register yet ({exc}); retrying every 30 s")
        if heartbeat:
            stops.append(view.keep_alive())
    return stops, warnings


def help_text(extra: Optional[str] = None) -> str:
    lines = [extra or "Here is what each agent can do:", ""]
    for name in ROLES:
        a = AGENTS[name]
        ex = "; ".join(f"“{q}”" for q in EXAMPLES[name])
        lines.append(f"- **{a['title']}**: {a['can']}. Try {ex}")
    return "\n".join(lines)
