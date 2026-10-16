# SPDX-License-Identifier: MIT
"""A thin client for the /insights API. Every call returns ``(data, source_url)``.

``source_url`` is the call relative to the backend (``/insights/ranking?by=partner&...``): put it
in a mark's or panel's provenance so readers can see where a number comes from. Agents read numbers
here to decide what to say, but never type them into the page: they go in as {fact} placeholders.
"""
from __future__ import annotations

import copy
import os
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"

# A process-wide cache of successful GET responses of the read endpoints (/insights/*, /trends/*): the data does
# not move while the agent runs, and a warm-up makes the first answers fast (the drill-down reads
# /insights/movers four times in a row, about a second each). Never /view (the page changes all the time), never
# an error. Keyed on the backend, the path and the sorted parameters; entries live CACHE_SECONDS.
CACHE_SECONDS = float(os.environ.get("LUX_INSIGHTS_CACHE_SECONDS", 30 * 60))
CACHED_PREFIXES = ("/insights/", "/trends/")
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cache_key(base_url: str, endpoint: str, params: Optional[dict]) -> tuple:
    """The cache key of a GET: the backend, the path and the parameters sorted (their order never matters)."""
    clean = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in (params or {}).items() if v is not None}
    return (base_url.rstrip("/"), endpoint, tuple(sorted(clean.items())))


def cacheable(endpoint: str) -> bool:
    """Only the read endpoints of the data: /insights/* and /trends/*, never /view."""
    return CACHE_SECONDS > 0 and endpoint.startswith(CACHED_PREFIXES)


def clear_cache() -> None:
    """Forget every cached response (the tests, or after the data was reloaded)."""
    with _cache_lock:
        _cache.clear()


class InsightsError(RuntimeError):
    """The API refused the call (4xx/5xx), e.g. an unknown code."""

    def __init__(self, status: int, detail: Any, url: str):
        super().__init__(f"{status} on {url}: {detail}")
        self.status, self.detail, self.url = status, detail, url


class NotLoadedError(InsightsError):
    """The data is not in the database and the live source could not be reached (loaded: false)."""


def source_url(endpoint: str, params: Optional[dict] = None) -> str:
    """The relative URL of a call, the same way the backend writes it in provenance."""
    clean = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in (params or {}).items() if v is not None}
    return endpoint + (f"?{urlencode(clean, safe=',')}" if clean else "")


class InsightsClient:
    """``InsightsClient("http://localhost:8000").ranking("partner", "2025", flow="IMPORT")``.

    Dimension filters are keyword arguments (flow="EXPORT", partner="FR", product="85", freq="M");
    several values are comma-separated. Unfiltered dimensions fall back to the totals.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "InsightsClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, endpoint: str, **params) -> tuple[Any, str]:
        """Any /insights or /trends endpoint: ``get("/insights/series", flow="EXPORT", freq="M")``, or the product
        codelist (``/product/search/``)."""
        if not endpoint.startswith(("/insights", "/trends", "/product/")):
            endpoint = "/insights/" + endpoint.lstrip("/")
        url = source_url(endpoint, params)
        key = cache_key(self.base_url, endpoint, params) if cacheable(endpoint) else None
        if key is not None:
            with _cache_lock:
                hit = _cache.get(key)
            if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
                return copy.deepcopy(hit[1]), url   # a copy: a caller that edits it never edits the cache
        r = self._http.get(url)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise InsightsError(r.status_code, detail, url)
        data = r.json()
        if isinstance(data, dict) and data.get("loaded") is False:
            raise NotLoadedError(200, data.get("error", "not loaded"), url)
        if key is not None:
            with _cache_lock:
                _cache[key] = (time.monotonic(), copy.deepcopy(data))
        return data, url

    def absolute(self, url: str) -> str:
        return self.base_url + url

    # existing endpoints
    def product_codes(self, label: str, level: Optional[int] = None):
        """The product codelist (every HS2, HS4, HS6 and CN8 code, traded or not) whose name holds ``label``:
        ``product_codes("nuclear", level=2)`` -> [{code, label, level, parent_id}] (8401, even with no trade)."""
        return self.get("/product/search/", label=label, level=level)

    def meta(self):
        return self.get("/insights/meta")

    def ranking(self, by: str, period: str, **params):
        return self.get("/insights/ranking", by=by, period=period, **params)

    def ranking_records(self, by: str, period: str, **params):
        return self.get("/insights/ranking/records", by=by, period=period, **params)

    def ladder(self, by: str, period: str, **params):
        return self.get("/insights/ladder", by=by, period=period, **params)

    def series(self, **params):
        """``series(flow="EXPORT", freq="M", adjust=True)``; adjusted values only when no start/end."""
        return self.get("/insights/series", **params)

    def movers(self, by: str, period: str, previous: str, **params):
        return self.get("/insights/movers", by=by, period=period, previous=previous, **params)

    def compare(self, by: str, period: str, **params):
        return self.get("/insights/compare", by=by, period=period, **params)

    def compare_records(self, by: str, period: str, **params):
        return self.get("/insights/compare/records", by=by, period=period, **params)

    def specialisation(self, by: str, period: str, **params):
        return self.get("/insights/specialisation", by=by, period=period, **params)

    def specialisation_records(self, by: str, period: str, **params):
        return self.get("/insights/specialisation/records", by=by, period=period, **params)

    # endpoints added for the agents (see docs/agents.md for their responses)
    def trend(self, **params):
        return self.get("/insights/trend", **params)

    def trends(self, by: str, **params):
        return self.get("/insights/trends", by=by, **params)

    def forecast(self, **params):
        return self.get("/insights/forecast", **params)

    def events(self, **params):
        return self.get("/insights/events", **params)

    def flows(self, period: str, **params):
        return self.get("/insights/flows", period=period, **params)

    # contract 1.1
    def matrix(self, by: str, start: str, end: str, **params):
        """Codes x periods: ``matrix("partner", "2015", "2025", flow="EXPORT", measure="rank")``.
        -> {by, measure, periods: [...], rows: [{code, name, values: [...]}]}"""
        return self.get("/insights/matrix", by=by, start=start, end=end, **params)

    def tree(self, period: str, by: str = "product", **params):
        """The product tree: ``tree("2025", flow="EXPORT", depth=2)``.
        -> {by, period, total, children: [{code, name, value, share, children: [...]}]}"""
        return self.get("/insights/tree", by=by, period=period, **params)

    def analyze(self, **params):
        """Ivan's trend analysis (STL, change points): ``analyze(flow="EXPORT", freq="M", start="1999-01")``."""
        return self.get("/trends/analyze", **params)

    def seasonal_heatmap(self, **params):
        """Ivan's seasonal profile of the top HS2 chapters: ``seasonal_heatmap(flow="EXPORT", freq="M", top=12)``."""
        return self.get("/trends/seasonal-heatmap", **params)


_SEGMENT_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)=([^\].]+)\]|(#length|#position)|(-?\d+)|([A-Za-z_][A-Za-z0-9_]*)")


def get_path(data: Any, path: str) -> Any:
    """Read a value with a fact path, exactly as the page will: ``get_path(r, "items.[code=FR].value")``.

    Segments: a key, an index (-1 = last), [field=value] (first matching item), #length, #position
    (1-based position of the item just selected). Raises KeyError when the value is not there.
    """
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
