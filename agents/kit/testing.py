# SPDX-License-Identifier: MIT
"""An in-memory /view store, for tests and offline use (no backend needed).

It uses the backend's own validator (app/backend/view/validate.py), so a batch the stub accepts is
one the backend accepts too, except for the pre-flight against /insights, which the stub only does
when it is given an InsightsClient. It applies ops with the store rules of docs/agents.md: mark ids
``m<n>`` from ``next_mark_n`` (never reused), status ``proposed``, agent and provenance filled in,
new panels in a new layout row after the row of ``after``, and each storyline's stable page number
(``story_n``, contract 1.6).

    store = LocalView()                         # starts from the baseline (version 0)
    view = StubViewClient(store, "trend_finder")
    view.send([...])                            # same return value and errors as ViewClient.send
"""
from __future__ import annotations

import copy
import importlib.util
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .insights import InsightsClient, InsightsError
from .view import ViewClient, ViewError

REPO = Path(__file__).resolve().parents[2]
BACKEND_VIEW = REPO / "app" / "backend" / "view"
_MODULE = "besser_view_contract"


def contract():
    """The backend's view package (validate_ops, validate_spec, iter_queries, ...), imported by path.

    app/backend is not a package on sys.path (its modules would shadow others), so load it by file.
    """
    if _MODULE in sys.modules:
        return sys.modules[_MODULE]
    spec = importlib.util.spec_from_file_location(_MODULE, BACKEND_VIEW / "__init__.py",
                                                  submodule_search_locations=[str(BACKEND_VIEW)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = module
    spec.loader.exec_module(module)
    return module


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LocalView:
    """The spec, its versions, and POST /view/ops semantics, in memory. Thread-safe."""

    def __init__(self, spec: Optional[dict] = None, insights: Optional[InsightsClient] = None):
        self.spec = copy.deepcopy(spec) if spec is not None else contract().load_baseline()
        if self.spec.get("marks"):
            contract().validate.number_storylines(self.spec)   # a spec from before 1.6: numbered in order
        self.insights = insights          # when set, new queries are pre-flighted like the backend does
        self.agents: dict[str, dict] = {}  # the registry: name -> registration
        self._runs: dict[str, dict] = {}   # run_id -> response, so a resent batch is a no-op retry
        self._lock = threading.Lock()

    def post_ops(self, envelope: dict) -> dict:
        """POST /view/ops. Returns {version, assigned, warnings, spec}; raises ViewError (409/422)."""
        with self._lock:
            run_id = envelope.get("agent", {}).get("run_id")
            if run_id and run_id in self._runs:
                return copy.deepcopy(self._runs[run_id])
            base = envelope.get("base_version")
            if base is not None and base != self.spec["version"]:
                raise ViewError(409, {"detail": f"base_version {base} is stale; the page is at "
                                                f"{self.spec['version']}",
                                      "version": self.spec["version"], "spec": copy.deepcopy(self.spec)})
            errors = contract().validate_ops(envelope, self.spec)
            if errors:
                raise ViewError(422, {"detail": "the batch was not applied", "errors": errors})
            sources = self._preflight(envelope)
            spec, assigned, replaced = self._apply(envelope, sources)
            errors = contract().validate_spec(spec)
            if errors:  # a store bug, not the agent's: refuse rather than save a broken page
                raise ViewError(422, {"detail": "the batch would leave an invalid page", "errors": errors})
            self.spec = spec
            result = {"version": spec["version"], "assigned": assigned, "warnings": [], "replaced": replaced,
                      "spec": copy.deepcopy(spec)}
            if run_id:
                self._runs[run_id] = copy.deepcopy(result)
            return result

    def _preflight(self, envelope: dict) -> list[dict]:
        """Call every query of the batch once; a 4xx or loaded:false rejects it (code ``preflight``)."""
        if self.insights is None:
            return []
        c, seen, errors, sources = contract(), set(), [], []
        # each query as the page fills it after the batch: the page context (with every set_context of the
        # batch) and the own context (1.5) of the panel it is on
        for ptr, q, context in c.validate.batch_query_contexts(envelope, self.spec):
            url = c.query_url(q, context)
            if url in seen:
                continue
            seen.add(url)
            try:
                self.insights.get(q["endpoint"], **c.validate.fill_params(q["params"], context))
                sources.append({"url": url})
            except InsightsError as exc:
                errors.append({"path": ptr, "code": "preflight", "message": f"{url}: {exc}"})
        if errors:
            raise ViewError(422, {"detail": "a query failed its pre-flight", "errors": errors})
        return sources

    def _apply(self, envelope: dict, preflight_sources: list[dict]) -> tuple[dict, dict, list]:
        spec = copy.deepcopy(self.spec)
        before = {p["id"] for p in spec["panels"]}
        replaced: list[dict] = []
        agent, run_id = envelope["agent"]["name"], envelope["agent"].get("run_id")
        now = _now()
        assigned: dict[str, dict] = {}
        panels = {p["id"]: p for p in spec["panels"]}
        rows: list[dict] = spec["layout"]["rows"]

        def provenance(given: Optional[dict]) -> dict:
            out = {"agent": agent, "created_at": now, "sources": list((given or {}).get("sources", []))}
            if run_id:
                out["run_id"] = run_id
            known = {s["url"] for s in out["sources"]}
            out["sources"] += [s for s in preflight_sources if s["url"] not in known]
            return out

        def resolve(mid: str) -> str:
            return assigned[mid]["id"] if mid in assigned else mid

        def drop_panel(pid: str) -> None:
            spec["panels"][:] = [p for p in spec["panels"] if p["id"] != pid]
            panels.pop(pid, None)
            for row in rows:
                if pid in row["panels"]:
                    row["panels"].remove(pid)
            rows[:] = [r for r in rows if r["panels"]]

        for op in envelope["ops"]:
            kind = op["op"]
            if kind == "upsert_panel":
                panel = copy.deepcopy(op["panel"])
                old = panels.get(panel["id"])
                panel["agent"] = agent
                panel["pinned"] = bool(old and old.get("pinned"))
                if panel["type"] == "text" or "provenance" in panel:
                    panel["provenance"] = provenance(panel.get("provenance"))
                v = contract().validate
                page_ids = v.baseline_panel_ids()
                if old is None:
                    # the agents' area is full: the new panel takes the slot of the oldest one not kept
                    gone = (v.panels_to_replace(spec["panels"], spec["marks"], page_ids)
                            if v.is_agent_panel(panel, page_ids) else [])
                    slot = next((r for r in rows if gone and gone[0] in r["panels"]), None)
                    for pid in gone:
                        was = panels[pid]
                        if pid in before:
                            replaced.append({"id": pid, "title": was.get("title"), "type": was.get("type"),
                                             "agent": was.get("agent"), "by": panel["id"]})
                        spec["marks"] = [m for m in spec["marks"] if m["target"] != pid]
                        if slot is not None and pid == gone[0]:
                            slot["panels"][slot["panels"].index(pid)] = panel["id"]
                            spec["panels"][:] = [p for p in spec["panels"] if p["id"] != pid]
                            panels.pop(pid, None)
                        else:
                            drop_panel(pid)
                    spec["panels"].append(panel)
                    if slot is None:
                        at = next((i + 1 for i, r in enumerate(rows) if op.get("after") in r["panels"]), len(rows))
                        rows.insert(at, {"panels": [panel["id"]]})
                elif v.is_agent_panel(panel, page_ids):   # re-pointed: the newest agent panel now
                    spec["panels"].remove(old)
                    spec["panels"].append(panel)
                else:
                    spec["panels"][spec["panels"].index(old)] = panel
                panels[panel["id"]] = panel
            elif kind == "remove_panel":
                spec["marks"] = [m for m in spec["marks"] if m["target"] != op["id"]]
                drop_panel(op["id"])
            elif kind == "set_layout":
                rows[:] = copy.deepcopy(op["rows"])
            elif kind == "add_mark":
                n = spec["next_mark_n"]
                spec["next_mark_n"] = n + 1
                mark = copy.deepcopy(op["mark"])
                mark.update({"id": f"m{n}", "n": n, "status": "proposed", "agent": agent,
                             "provenance": provenance(mark.get("provenance"))})
                spec["marks"].append(mark)
                if op.get("tmp_id"):
                    assigned[op["tmp_id"]] = {"id": mark["id"], "n": n}
            elif kind == "update_mark":
                mid = resolve(op["id"])
                for m in spec["marks"]:
                    if m["id"] == mid:
                        m.update(copy.deepcopy(op["set"]))
            elif kind == "keep_mark":
                mid = resolve(op["id"])
                for m in spec["marks"]:
                    if m["id"] == mid:
                        m["status"] = "kept"
            elif kind == "remove_mark":
                mid = resolve(op["id"])
                spec["marks"] = [m for m in spec["marks"] if m["id"] != mid]
            elif kind == "pin_panel":
                panels[op["id"]]["pinned"] = op["pinned"]
            elif kind == "clear" and op["scope"] == "page":   # the user's "Clear the page"
                gone = contract().validate.clearable_page_panels(spec["panels"], spec["marks"])
                for pid in gone:
                    drop_panel(pid)
                spec["marks"] = [m for m in spec["marks"] if m.get("status") == "kept"]
                spec["next_story_n"] = max((m.get("story_n") or 0 for m in spec["marks"]), default=0) + 1
            elif kind == "clear":
                who = op.get("agent")
                if op["scope"] in ("agent_panels", "all"):
                    for pid, panel in list(panels.items()):
                        owner = panel.get("agent", "baseline")
                        kept = any(m["target"] == pid and m["status"] == "kept" for m in spec["marks"])
                        if owner not in ("baseline", "user") and not panel.get("pinned") and not kept \
                                and who in (None, owner):
                            spec["marks"] = [m for m in spec["marks"] if m["target"] != pid]
                            drop_panel(pid)
                if op["scope"] in ("marks", "all"):
                    spec["marks"] = [m for m in spec["marks"]
                                     if m["status"] == "kept" or who not in (None, m.get("agent"))]
            elif kind == "set_title":
                spec["title"] = op["title"]
            elif kind == "set_context":  # contract 1.1: merges into the page context
                spec["context"] = {**contract().validate.context_of(spec),
                                   **{k: op[k] for k in ("period", "product", "flow", "start", "persona") if k in op}}
            elif kind == "set_panel_context":  # contract 1.5: the panel's whole own context; none: follow the page
                own = {k: op[k] for k in ("period", "start", "product") if op.get(k) is not None}
                if own:
                    panels[op["id"]]["context"] = own
                else:
                    panels[op["id"]].pop("context", None)

        contract().validate.number_storylines(spec)   # 1.6: a new storyline gets the next number
        spec["version"] += 1
        spec["updated_at"], spec["updated_by"] = now, agent
        if envelope.get("message"):
            spec["message"] = envelope["message"]
        else:
            spec.pop("message", None)
        return spec, assigned, replaced


class StubViewClient(ViewClient):
    """A ViewClient whose send/spec/register go to a LocalView instead of the backend."""

    def __init__(self, store: LocalView, agent_name: str):  # no httpx client: nothing goes over HTTP
        self.base_url, self.agent_name, self.store = "stub:", agent_name, store
        self._registration: Optional[dict] = None
        self._validator = None

    def close(self) -> None:
        pass

    def send(self, ops: list, message: Optional[str] = None, base_version: Optional[int] = None,
             run_id: Optional[str] = None) -> dict:
        env = self.envelope(ops, message, base_version, run_id)
        problems = self.validate(env)
        if problems:
            raise ValueError("invalid ops:\n" + "\n".join(problems))
        return self.store.post_ops(env)

    def spec(self, since: Optional[int] = None) -> dict:
        with self.store._lock:
            if since is not None and since >= self.store.spec["version"]:
                return {}
            return copy.deepcopy(self.store.spec)

    def heartbeat(self) -> dict:
        if self._registration is None:
            raise RuntimeError("call register() first")
        self.store.agents[self.agent_name] = dict(self._registration)
        return {"ok": True}

    def keep_alive(self, interval: float = 30.0):
        stop = threading.Event()
        stop.set()  # nothing expires in the stub
        return stop


def stub_views(store: LocalView, names) -> dict[str, Any]:
    return {name: StubViewClient(store, name) for name in names}
