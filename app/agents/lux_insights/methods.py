# SPDX-License-Identifier: MIT
"""The method catalogue, as the agent uses it: the plain name of the fixed method behind a query.

The catalogue is served by the backend (GET /insights/methods, app/backend/routers/insights.py METHODS), the
same text the page's "How this was made" card uses (app/frontend/src/view/methods.ts). The agent reads it once
and names each calculation by it, in its decision record and in its replies ("change-point analysis of the
monthly series", never "PELT" or "/trends/analyze"). When the backend cannot be read, NAMES below keeps the
names (tests/test_lux_insights_agent.py checks they are the backend's).

    m = method_of("/insights/movers?by=product&period=2025&previous=2024&flow=EXPORT", catalogue)
    m["name"] == "Movers"
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit

log = logging.getLogger("lux_insights.methods")

# The catalogue's names, for when the backend cannot be read (the same as METHODS in routers/insights.py).
NAMES = {
    "/insights/meta": "Data catalogue",
    "/insights/ranking": "Ranking by share",
    "/insights/ranking/records": "Ranking by share",
    "/insights/ladder": "Totals with the significance ladder",
    "/insights/series": "Totals over time",
    "/insights/movers": "Movers",
    "/insights/compare": "Values side by side",
    "/insights/compare/records": "Growth among EU countries",
    "/insights/specialisation": "Revealed comparative advantage",
    "/insights/specialisation/records": "Revealed comparative advantage",
    "/insights/trend": "Trend line",
    "/insights/trends": "Fastest growing",
    "/insights/forecast": "Forecast with its test",
    "/insights/events": "Dated events",
    "/insights/flows": "Ranking of partners, both ways",
    "/insights/matrix": "Values over the years",
    "/insights/tree": "Product tree",
    "/trends/analyze": "Seasonal adjustment and lasting shifts",
    "/trends/seasonal-heatmap": "Seasonal profile by product",
    "/product/search/": "Product code lookup",
}
VARIANT_NAMES = [  # (endpoint, param, value, name): a parameter that changes the method
    ("/insights/series", "adjust", "true", "Seasonal adjustment"),
    ("/insights/forecast", "backtest", "true", "Forecast test of every method"),
    ("/insights/matrix", "measure", "rank", "Ranks over the years"),
    ("/insights/matrix", "measure", "growth", "Growth over the years"),
    ("/insights/matrix", "measure", "share", "Shares over the years"),
]

# How the reply names a method in a sentence ("I ran a change-point analysis of the monthly series").
IN_A_SENTENCE = {
    "/trends/analyze": "change-point analysis of the monthly series",
    "/insights/forecast": "the tested forecast and its backtest",
    "/insights/events": "the curated list of dated events",
    "/insights/movers": "the movers between the two years",
    "/insights/ranking": "the ranking by share",
    "/insights/trend": "a fitted trend line",
    "/insights/trends": "the ranking of the fastest growing",
}

# Technical names a reply must not use, in plain words.
TECH_WORDS = [
    (re.compile(r"\bthe\s+PELT(?:\s+(?:algorithm|method|change[- ]point\s+(?:detection|analysis)))?\b"),
     "the change-point analysis"),
    (re.compile(r"\bPELT(?:\s+(?:algorithm|method|change[- ]point\s+(?:detection|analysis)))?\b"),
     "change-point analysis"),
    (re.compile(r"\b(?:an?\s+)?STL\s+decomposition\b"), "a seasonal adjustment"),
    (re.compile(r"\bSTL\b"), "seasonal adjustment"),
    (re.compile(r"\bCAGR\b"), "average growth per year"),
    (re.compile(r"\bOLS(?:[_ ]log)?\b"), "a fitted trend line"),
]

_lock = threading.Lock()
_cache: dict[str, Any] = {"entries": None, "tried": 0.0}
RETRY_SECONDS = 60


def fetch(api: Any) -> Optional[dict[str, dict]]:
    """{endpoint: entry} from GET /insights/methods, read once per process (again a minute after a failure);
    None when the backend cannot be read."""
    with _lock:
        if _cache["entries"] is not None:
            return _cache["entries"]
        if time.monotonic() - _cache["tried"] < RETRY_SECONDS and _cache["tried"]:
            return None
        _cache["tried"] = time.monotonic()
    try:
        data, _ = api.get("/insights/methods")
        entries = {m["endpoint"]: m for m in (data or {}).get("methods", []) if m.get("endpoint")}
    except Exception as exc:  # the backend is older or down: the names below
        log.info("no method catalogue from the backend (%s); using the built-in names", exc)
        return None
    with _lock:
        _cache["entries"] = entries or None
    return _cache["entries"]


def reset() -> None:
    """Forget the catalogue (tests)."""
    with _lock:
        _cache["entries"], _cache["tried"] = None, 0.0


def split_url(url: str) -> tuple[str, dict]:
    """'/insights/movers?by=product&period=2025' -> ('/insights/movers', {'by': 'product', 'period': '2025'})."""
    parts = urlsplit(str(url or ""))
    return parts.path or str(url or ""), dict(parse_qsl(parts.query, keep_blank_values=True))


def _truthy(v: Any) -> str:
    return str(v).lower()


def method_of(url_or_endpoint: str, catalogue: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    """{name, plain, endpoint, params}: the catalogue's entry for a query, its variant applied."""
    endpoint, from_url = split_url(url_or_endpoint)
    p = {**from_url, **(params or {})}
    entry = (catalogue or {}).get(endpoint) or (catalogue or {}).get(endpoint.rstrip("/") + "/")
    name = (entry or {}).get("name") or NAMES.get(endpoint) or NAMES.get(endpoint.rstrip("/") + "/") \
        or "A fixed calculation"
    plain = (entry or {}).get("plain") or ""
    if entry:
        for v in entry.get("variants") or []:
            if all(_truthy(p.get(k)) == _truthy(val) for k, val in (v.get("when") or {}).items()):
                name, plain = v.get("name", name), v.get("plain", plain)
                break
    else:
        for e, k, val, n in VARIANT_NAMES:
            if e == endpoint and _truthy(p.get(k)) == val:
                name = n
                break
    out = {"name": name, "plain": plain, "endpoint": endpoint, "params": p}
    if (entry or {}).get("no_calculation"):
        out["no_calculation"] = True
    return out


def prompt_lines(catalogue: Optional[dict] = None) -> str:
    """The methods, one line each, for a specialist's prompt: how to name them in the reply."""
    names = {}
    for endpoint in NAMES:
        if endpoint == "/product/search/":
            continue
        name = method_of(endpoint, catalogue)["name"]
        names.setdefault(name, IN_A_SENTENCE.get(endpoint))
    return "; ".join(f"{n}" + (f" (\"{s}\")" if s else "") for n, s in names.items())


def plain_method_words(text: str) -> str:
    """A reply without the methods' technical names: 'PELT' -> 'change-point analysis', 'STL' -> 'seasonal
    adjustment'."""
    for rx, words in TECH_WORDS:
        text = rx.sub(words, text)
    return text
