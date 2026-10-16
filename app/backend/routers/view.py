# SPDX-License-Identifier: MIT
"""The view spec over HTTP, and the registry of agents (written by hand, not generated).

Agents never touch the page: they POST typed ops to /view/ops. A batch is validated
(view/validate.py), every new query in it is pre-flighted against /insights inside this process
(a 4xx or ``loaded: false`` rejects the batch), then the store applies it as a new version, saves
it and pushes the full spec to every browser over SSE (GET /view/events). See docs/agents.md.

    GET  /view                 the current spec; ?since=N answers 204 while the version is <= N
    GET  /view/events          SSE: event "spec" with the full spec on connect and on every change
    POST /view/ops             a batch: 200 {version, assigned, warnings, replaced, spec} | 422 | 409
    POST /view/undo|redo|reset each makes a new version
    GET  /view/history         the last 200 versions (without their specs); /view/history/{version}
    POST /agents/register      an agent says it is alive (re-register every 30 s; TTL 60 s)
    GET  /agents               the agents seen in the last 60 s
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from view.store import CONTEXT_FIELDS, ViewConflict, ViewRejected, ViewStore
from view.validate import PERIOD_RE, batch_query_contexts, context_of, iter_queries, query_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["View"])

HEARTBEAT_SECONDS = 15.0
STREAM_MAX_SECONDS = 600.0      # a stream ends after this; EventSource reconnects (retry below)
STREAM_RETRY_MS = 2000
PREFLIGHT_TIMEOUT = float(os.getenv("VIEW_PREFLIGHT_TIMEOUT", "90"))
PREFLIGHT_CONCURRENCY = 4
PREFLIGHT_CACHE_SECONDS = 600   # a query that answered well is not asked again for 10 minutes
AGENT_TTL_SECONDS = 60

_STORE: Optional[ViewStore] = None
_STORE_LOCK = threading.Lock()
_PREFLIGHT_OK: dict[str, float] = {}
_META: list[dict] = []


def default_context() -> dict:
    """The context of a page nobody has moved yet: the latest complete year (12 months in the
    database, from /insights/meta ``complete_until``) and all goods. 2025 if the data says nothing."""
    ctx = {"period": "2025", "product": "TOTAL"}
    try:
        from database import SessionLocal
        from routers.insights import meta

        db = SessionLocal()
        try:
            complete = meta(db).get("complete_until")
        finally:
            db.close()
        if complete:
            ctx["period"] = complete[:4]
    except Exception:  # no database yet: the contract's default
        logger.exception("could not read the latest complete year for the view context")
    return ctx


def get_store() -> ViewStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ViewStore(default_context=default_context())
        return _STORE


def configure(path: Optional[str] = None, baseline: Optional[dict] = None) -> ViewStore:
    """Use another state file (tests); returns the new store."""
    global _STORE
    with _STORE_LOCK:
        _STORE = ViewStore(path, baseline, default_context=default_context())
        _PREFLIGHT_OK.clear()
        return _STORE


def _who(body: Optional[dict]) -> str:
    name = ((body or {}).get("agent") or {}).get("name") if isinstance(body, dict) else None
    return name if isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,39}", name) else "user"


def _conflict(exc: ViewConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.detail, "version": exc.spec["version"],
                                                  "spec": exc.spec})


def _rejected(detail: str, errors: list[dict]) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": detail, "errors": errors})


async def _json_body(request: Request) -> Optional[dict]:
    raw = await request.body()
    if not raw.strip():
        return None
    return json.loads(raw)


# --------------------------------------------------------------------------- pre-flight
def _client(request: Request) -> httpx.AsyncClient:
    """Calls into this same app, in process (no network, no port)."""
    transport = httpx.ASGITransport(app=request.app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://view.preflight", timeout=PREFLIGHT_TIMEOUT)


async def _meta(client: httpx.AsyncClient) -> Optional[dict]:
    """/insights/meta, for the validator's dimension ids and enum values (cached once it answers)."""
    if not _META:
        try:
            r = await client.get("/insights/meta")
            if r.status_code == 200:
                _META.append(r.json())
        except Exception:  # validation falls back to the default dimensions
            logger.exception("could not read /insights/meta for the view validator")
    return _META[0] if _META else None


def context_after(envelope: dict, spec: dict) -> dict:
    """The page context once the batch is applied: the spec's, with every set_context of the batch."""
    ctx = context_of(spec)
    for op in envelope.get("ops") or []:
        if isinstance(op, dict) and op.get("op") == "set_context":
            ctx = {**ctx, **{k: op[k] for k in CONTEXT_FIELDS if k in op}}
    return ctx


def _queries_of(envelope: dict, context: Optional[dict] = None, spec: Optional[dict] = None) -> list[tuple[str, str]]:
    """(JSON pointer, relative URL) of every query a batch adds or changes, with the placeholders
    ($period, $previous, $product) filled from ``context``: what the page will ask. With ``spec`` (the
    current one), each query is filled as the page will fill it after the batch: from the page context
    and the own context (1.5) of the panel it is on."""
    if spec is not None:
        return [(ptr, query_url(q, ctx)) for ptr, q, ctx in batch_query_contexts(envelope, spec)]
    out = []
    for i, op in enumerate(envelope.get("ops") or []):
        for field in ("panel", "mark", "set"):
            if isinstance(op, dict) and isinstance(op.get(field), dict):
                body = {k: v for k, v in op[field].items() if k != "provenance"}
                out += [(ptr, query_url(q, context)) for ptr, q in iter_queries(body, f"/ops/{i}/{field}")]
    return out


def _check_context(envelope: dict, meta: Optional[dict]) -> list[dict]:
    """A set_context (or a panel's own context, 1.5) must name a period the database has (a year of the annual data, a month of the
    monthly data) and a product code that exists: cheap checks, no data query. Errors as pre-flight."""
    errors = []
    periods = (meta or {}).get("periods") or {}
    for i, op in enumerate(envelope.get("ops") or []):
        if not isinstance(op, dict):
            continue
        # set_context, set_panel_context (1.5), and the own context of an upserted panel (1.5)
        if op.get("op") in ("set_context", "set_panel_context"):
            fields, ptr = op, f"/ops/{i}"
        elif op.get("op") == "upsert_panel" and isinstance(op.get("panel"), dict)                 and isinstance(op["panel"].get("context"), dict):
            fields, ptr = op["panel"]["context"], f"/ops/{i}/panel/context"
        else:
            continue
        period = fields.get("period")
        if isinstance(period, str) and PERIOD_RE.match(period) and periods:
            known = periods.get("M" if len(period) == 7 else "A") or []
            if known and period not in known:
                span = f"{known[0]} to {known[-1]}"
                errors.append({"path": f"{ptr}/period", "code": "preflight", "status": None,
                               "message": f"There is no data for {period}; the data runs from {span}"})
        start = fields.get("start")  # 1.4: the first year of a range
        known_years = periods.get("A") or []
        if isinstance(start, str) and known_years and start not in known_years:
            errors.append({"path": f"{ptr}/start", "code": "preflight", "status": None,
                           "message": f"There is no data for {start}; the data runs from "
                                      f"{known_years[0]} to {known_years[-1]}"})
        product = fields.get("product")
        if isinstance(product, str) and not _known_product(product):
            errors.append({"path": f"{ptr}/product", "code": "preflight", "status": None,
                           "message": f"Unknown product code {product!r}: TOTAL or an HS code (85, 8517, 85171300)"})
    return errors


def _known_product(code: str) -> bool:
    try:
        from database import SessionLocal
        from routers.insights import DIMS, _known_codes

        db = SessionLocal()
        try:
            return code in _known_codes(db, DIMS["product"])
        finally:
            db.close()
    except Exception:  # cannot tell: let the page find out
        logger.exception("could not check the product code %r", code)
        return True


async def _preflight(client: httpx.AsyncClient, envelope: dict, context: Optional[dict] = None,
                     spec: Optional[dict] = None) -> list[dict]:
    """Ask /insights every new query (placeholders filled from ``context``, or with ``spec`` as the page
    will fill them after the batch, panel contexts included); errors (code 'preflight') for those that fail."""
    targets = _queries_of(envelope, context, spec)
    now = time.time()
    urls = sorted({u for _, u in targets if now - _PREFLIGHT_OK.get(u, 0) > PREFLIGHT_CACHE_SECONDS})
    gate = asyncio.Semaphore(PREFLIGHT_CONCURRENCY)

    async def ask(url: str) -> Optional[dict]:
        async with gate:
            try:
                r = await client.get(url)
            except Exception as exc:
                return {"status": None, "message": f"GET {url} failed: {exc}"}
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code >= 400:
            detail = body.get("detail", body.get("message")) if isinstance(body, dict) else r.text[:300]
            return {"status": r.status_code, "message": f"GET {url} answered {r.status_code}: {detail}"}
        if isinstance(body, dict) and body.get("loaded") is False:
            why = body.get("error") or "the data is not loaded and the source could not be reached"
            return {"status": r.status_code, "message": f"GET {url} has no data (loaded: false): {why}"}
        _PREFLIGHT_OK[url] = time.time()
        return None

    failures = dict(zip(urls, await asyncio.gather(*(ask(u) for u in urls))))
    errors = []
    for ptr, url in targets:
        fail = failures.get(url)
        if fail:
            errors.append({"path": ptr, "code": "preflight", "message": fail["message"], "url": url,
                           "status": fail["status"]})
    return errors


# --------------------------------------------------------------------------- the spec
@router.get("/view")
def get_view(since: Optional[int] = None):
    """The current spec. With ``since=N``: 204 (no body) while the version is N or less."""
    spec = get_store().current()
    if since is not None and spec["version"] <= since:
        return Response(status_code=204)
    return spec


def _sse(spec: dict) -> str:
    return f"id: {spec['version']}\nevent: spec\ndata: {json.dumps(spec, ensure_ascii=False)}\n\n"


@router.get("/view/events")
async def view_events(request: Request, since: Optional[int] = None, max_events: Optional[int] = None,
                      heartbeat: float = HEARTBEAT_SECONDS):
    """Server-sent events: ``event: spec`` with the full spec, at once (unless ``since`` is the
    current version or later) and after every change; a comment line every 15 s as heartbeat.
    ``max_events`` closes the stream after that many specs (for scripts and tests)."""
    store = get_store()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def listener(spec: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, spec)

    store.add_listener(listener)
    heartbeat = min(max(heartbeat, 0.05), 60.0)

    async def stream():
        sent, last = 0, since if since is not None else -1
        ends = time.monotonic() + STREAM_MAX_SECONDS
        try:
            yield f"retry: {STREAM_RETRY_MS}\n\n"
            spec = store.current()
            if spec["version"] > last:
                yield _sse(spec)
                sent, last = sent + 1, spec["version"]
            while not (max_events and sent >= max_events) and time.monotonic() < ends:
                try:
                    spec = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except asyncio.TimeoutError:
                    try:
                        if await request.is_disconnected():
                            return
                    except Exception:
                        pass
                    yield f": heartbeat {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}\n\n"
                    continue
                while not queue.empty():  # only the newest spec matters
                    spec = queue.get_nowait()
                if spec["version"] > last:
                    yield _sse(spec)
                    sent, last = sent + 1, spec["version"]
        finally:
            store.remove_listener(listener)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/view/ops")
async def post_ops(request: Request):
    """Apply a batch of ops. 200 {version, assigned, marks_added, warnings, replaced, spec}; 422 {detail, errors}
    when the batch is invalid or a query fails its pre-flight; 409 {detail, version, spec} when
    ``base_version`` is not the current version. Nothing changes unless the whole batch is accepted."""
    try:
        envelope = await _json_body(request)
    except ValueError as exc:
        return _rejected("The body is not JSON.", [{"path": "", "code": "schema", "message": str(exc)}])
    if not isinstance(envelope, dict):
        return _rejected("The body must be a JSON object.", [{"path": "", "code": "schema",
                                                               "message": "expected {agent, ops, ...}"}])
    store = get_store()
    async with _client(request) as client:
        meta = await _meta(client)
        try:
            retry = store.retry_of(envelope) if isinstance(envelope.get("agent"), dict) else None
            if retry:
                return retry
            store.check(envelope, meta)          # fast answers before any query runs
        except ViewConflict as exc:
            return _conflict(exc)
        except ViewRejected as exc:
            return _rejected(exc.detail, exc.errors)
        errors = await asyncio.to_thread(_check_context, envelope, meta)
        if errors:
            return _rejected("The batch asks for a context the data does not have; nothing was changed.", errors)
        current = store.current()
        errors = await _preflight(client, envelope, context_after(envelope, current), current)
    if errors:
        return _rejected("A query of the batch failed against /insights; nothing was changed.", errors)
    try:
        result = await asyncio.to_thread(store.apply, envelope, meta)
    except ViewConflict as exc:
        return _conflict(exc)
    except ViewRejected as exc:
        return _rejected(exc.detail, exc.errors)
    _seen(envelope["agent"]["name"])
    return result


async def _history_op(request: Request, action: str):
    try:
        body = await _json_body(request)
    except ValueError:
        body = None
    store = get_store()
    try:
        fn = {"undo": store.undo_last, "redo": store.redo_last, "reset": store.reset}[action]
        spec = await asyncio.to_thread(fn, _who(body))
    except ViewConflict as exc:
        return _conflict(exc)
    return {"version": spec["version"], "spec": spec}


@router.post("/view/undo")
async def undo(request: Request):
    """Undo the last change (a batch, a redo or a reset) as a new version. 409 when there is nothing to undo."""
    return await _history_op(request, "undo")


@router.post("/view/redo")
async def redo(request: Request):
    """Redo the last undone change as a new version. 409 when there is nothing to redo."""
    return await _history_op(request, "redo")


@router.post("/view/reset")
async def reset(request: Request):
    """Back to the baseline page, as a new version (undo brings the page back)."""
    return await _history_op(request, "reset")


@router.get("/view/history")
def history():
    """The last 200 versions, newest first: {version, can_undo, can_redo, entries: [{version, at,
    agent, action (ops|undo|redo|reset|baseline), message, run_id, n_ops, persona?}]}; persona (1.4)
    is the reader the version was drawn for, when the page had one."""
    return get_store().history_entries()


@router.get("/view/history/{version}")
def history_version(version: int):
    """The spec as it was at one version (within the last 200)."""
    spec = get_store().snapshot(version)
    if spec is None:
        return JSONResponse(status_code=404, content={"detail": f"Version {version} is not in the history"})
    return spec


# --------------------------------------------------------------------------- agents
class Capability(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    produces: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default="", max_length=300)
    inputs: list[str] = Field(default_factory=list, max_length=20)


class Registration(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    title: str = Field(min_length=1, max_length=80)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    ink: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    capabilities: list[Capability] = Field(default_factory=list, max_length=20)
    examples: list[str] = Field(default_factory=list, max_length=20)


_AGENTS: dict[str, dict] = {}
_AGENTS_LOCK = threading.Lock()


def _seen(name: str) -> None:
    with _AGENTS_LOCK:
        if name in _AGENTS:
            _AGENTS[name]["last_seen"] = time.time()


def _public(entry: dict, now: float) -> dict:
    out = {k: v for k, v in entry.items() if k not in ("last_seen", "registered_at")}
    out["last_seen"] = datetime.fromtimestamp(entry["last_seen"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["registered_at"] = datetime.fromtimestamp(entry["registered_at"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["online"] = now - entry["last_seen"] <= AGENT_TTL_SECONDS
    return out


@router.post("/agents/register", tags=["Agents"])
def register_agent(registration: Registration):
    """An agent says who it is and what it can do. Registering again is the heartbeat (every 30 s);
    an agent not seen for 60 s drops out of GET /agents. 'baseline' and 'user' are reserved."""
    if registration.name in ("baseline", "user"):
        return JSONResponse(status_code=422, content={"detail": f"{registration.name!r} is reserved",
                                                      "errors": [{"path": "/name", "code": "reserved_agent",
                                                                  "message": f"{registration.name!r} is reserved"}]})
    now = time.time()
    with _AGENTS_LOCK:
        old = _AGENTS.pop(registration.name, None)
        entry = {**registration.model_dump(), "registered_at": old["registered_at"] if old else now, "last_seen": now}
        _AGENTS[registration.name] = entry
        return {**_public(entry, now), "ttl": AGENT_TTL_SECONDS}


@router.get("/agents", tags=["Agents"])
def list_agents(include_offline: bool = False):
    """The agents seen in the last 60 s, in the order they first registered (with
    ``include_offline=true``, every agent seen since the backend started, with ``online``)."""
    now = time.time()
    with _AGENTS_LOCK:
        rows = sorted(_AGENTS.values(), key=lambda e: e["registered_at"])
        return [_public(e, now) for e in rows if include_offline or now - e["last_seen"] <= AGENT_TTL_SECONDS]
