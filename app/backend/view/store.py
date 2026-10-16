# SPDX-License-Identifier: MIT
"""The view store: holds the spec, applies op batches, keeps history, persists to JSON.

One process holds one store. Every change (a batch of ops, undo, redo, reset) makes a new version,
is saved to ``VIEW_STATE_PATH`` (default ``data/view_state.json``) and is pushed to listeners (the
SSE streams of /view/events). Pre-flighting queries against /insights is the router's job; the
store only validates (validate.py) and applies.

State file::

    {"format": "besser-view-state-1", "spec": {...},
     "history": [{"version", "at", "agent", "action", "message", "run_id", "n_ops", "op_kinds"?, "spec"}, ...],
     "undo": [versions], "redo": [versions], "runs": {"<agent>:<run_id>": {...}}}

Undo and redo restore the content of an earlier version as a NEW version; mark numbers stay
monotonic (``next_mark_n`` never goes down), so a number is never given to two different marks.

A storyline's page number (contract 1.6: ``story_n`` on its marks, ``next_story_n`` on the spec) is given
when its first mark is added and kept while the storyline is on the page; it is never given to another
storyline. Undo and redo keep the counter's highest value; only a reset or "Clear the page" starts the
numbers again. A state from before 1.6 is numbered on load, in the order the page showed it.

Every spec the store holds has a ``context`` (contract 1.1): what the page is about, {period,
product, flow?, persona? (1.4)}. A spec without one (the baseline, a state file from before 1.1) gets the store's
default context, {latest complete year, TOTAL}; the set_context op merges into it. A panel may have its own
``context`` {period?, start?, product?} (1.5, set_panel_context) that wins over the page's for that panel.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .validate import (DEFAULT_CONTEXT, PANEL_CONTEXT_FIELDS, check_panel_context, clearable_page_panels, context_of,
                       is_agent_panel, iter_queries, load_baseline, max_agent_panels as _max_agent_panels,
                       number_storylines, panel_context, panels_to_replace, query_url, validate_ops, validate_spec)

logger = logging.getLogger(__name__)

STATE_FORMAT = "besser-view-state-1"
DEFAULT_STATE_PATH = "data/view_state.json"
HISTORY_LIMIT = 200      # versions kept (and so how far undo reaches)
RUNS_LIMIT = 1000        # run_ids remembered for idempotent retries
MAX_SOURCES = 30         # provenance.sources maxItems in the schema
CONTEXT_FIELDS = ("period", "product", "flow", "start", "persona")  # start, persona: contract 1.4


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ViewConflict(Exception):
    """409: the batch was made against an older version, or a run_id is reused for other ops."""

    def __init__(self, detail: str, spec: dict):
        super().__init__(detail)
        self.detail, self.spec = detail, spec


class ViewRejected(Exception):
    """422: the batch is invalid; nothing was changed."""

    def __init__(self, detail: str, errors: list[dict]):
        super().__init__(detail)
        self.detail, self.errors = detail, errors


def _ops_hash(envelope: dict) -> str:
    return hashlib.sha256(json.dumps(envelope.get("ops"), sort_keys=True).encode("utf-8")).hexdigest()


def op_kinds(ops: list) -> list[str]:
    """What a batch did, op by op, for GET /view/history: 'upsert_panel', 'add_mark event', 'set_context'
    (the 'What changed' drawer lists them). Repeats are kept once, in order."""
    out: list[str] = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "")
        if kind == "add_mark" and isinstance(op.get("mark"), dict) and op["mark"].get("kind"):
            kind = f"add_mark {op['mark']['kind']}"
        if kind and kind not in out:
            out.append(kind)
    return out[:12]


def _add_sources(provenance: dict, obj: Any, context: Optional[dict] = None) -> list[str]:
    """Append the URL of every query in ``obj`` to provenance.sources (once each); warnings if cut.

    Placeholders ($period, $product...) are filled from ``context``: the source is what was asked."""
    sources = provenance.setdefault("sources", [])
    seen = {s["url"] for s in sources}
    body = {k: v for k, v in obj.items() if k != "provenance"} if isinstance(obj, dict) else obj
    for _, q in iter_queries(body):
        url = query_url(q, context)
        if url not in seen:
            seen.add(url)
            sources.append({"url": url})
    if len(sources) > MAX_SOURCES:
        del sources[MAX_SOURCES:]
        return [f"provenance kept the first {MAX_SOURCES} sources"]
    return []


_PANEL_CONTENT = ("type", "title", "subtitle", "text", "facts", "value", "queries", "options")
_PLACEHOLDER_VALUES = ("$period", "$previous", "$start", "$product")


def _pins_a_period(panel: dict, base: dict) -> bool:
    """True when ``panel`` holds a literal value (2022) where the baseline panel follows the page
    context with a placeholder ($period): what the old Year and range controls wrote."""
    base_q = {q.get("key"): q.get("params") or {} for q in base.get("queries") or []}
    for q in panel.get("queries") or []:
        want = base_q.get(q.get("key")) or {}
        for name, value in want.items():
            have = (q.get("params") or {}).get(name)
            if value in _PLACEHOLDER_VALUES and have is not None and have not in _PLACEHOLDER_VALUES:
                return True
    return False


def repair_page_panels(spec: dict, baseline: dict) -> Optional[dict]:
    """A persisted spec whose page panels no longer match the baseline, repaired; None when nothing to do.

    - a panel still owned by ``baseline`` that differs from today's baseline.json (a state saved before
      the baseline changed: the trend from 2015 when the data now starts in 1999) gets today's version;
    - a baseline panel owned by ``user`` (the page's own controls) that pins a literal year where the
      baseline follows the context (period 2022 for $period) goes back to the baseline panel, so the
      page context moves every panel again.
    Agents' panels and marks are left alone (a mark keeps its target: the id does not change).
    """
    base = {p["id"]: p for p in baseline.get("panels") or []}
    out = copy.deepcopy(spec)
    fixed = []
    for i, panel in enumerate(out.get("panels") or []):
        b = base.get(panel.get("id"))
        if not b:
            continue
        owner = panel.get("agent")
        stale = owner == "baseline" and any(panel.get(k) != b.get(k) for k in _PANEL_CONTENT)
        pinned = owner == "user" and _pins_a_period(panel, b)
        if stale or pinned:
            out["panels"][i] = {**copy.deepcopy(b), "pinned": bool(panel.get("pinned"))}
            # the panel's own year or goods (1.5) stays while the baseline panel still follows them
            own = panel.get("context")
            if own and not check_panel_context(own, b, ""):
                out["panels"][i]["context"] = copy.deepcopy(own)
            fixed.append(panel["id"])
    if not fixed:
        return None
    out["_repaired"] = fixed
    return out


class ViewStore:
    """The current spec, its history and the applied run_ids, persisted to one JSON file."""

    def __init__(self, path: Optional[str | os.PathLike] = None, baseline: Optional[dict] = None,
                 default_context: Optional[dict] = None, max_agent_panels: Optional[int] = None):
        self.path = Path(path or os.getenv("VIEW_STATE_PATH") or DEFAULT_STATE_PATH)
        self.baseline = copy.deepcopy(baseline) if baseline is not None else load_baseline()
        # The agents' slots: a new agent panel beyond them replaces the oldest one not kept (validate.py).
        self.max_agent_panels = max(1, int(max_agent_panels)) if max_agent_panels else _max_agent_panels()
        # What a spec without a context is about: the latest complete year and all goods.
        self.default_context = {**DEFAULT_CONTEXT, **(default_context or {})}
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []
        self._load()

    # ------------------------------------------------------------------ persistence
    def _with_context(self, spec: dict) -> dict:
        """``spec`` with a context: its own, or the store's default when it has none."""
        if not isinstance(spec.get("context"), dict):
            spec["context"] = dict(self.default_context)
        return spec

    def _fresh(self) -> None:
        spec = self._with_context(copy.deepcopy(self.baseline))
        spec["version"] = 0
        self.spec = spec
        self.history = [{"version": 0, "at": spec.get("updated_at") or now_iso(), "agent": "baseline",
                         "action": "baseline", "message": "The baseline page", "run_id": None, "n_ops": 0,
                         "spec": spec}]
        self.undo: list[int] = []
        self.redo: list[int] = []
        self.runs: dict[str, dict] = {}

    def _load(self) -> None:
        if not self.path.exists():
            self._fresh()
            return
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state.get("format") != STATE_FORMAT or not isinstance(state.get("spec"), dict):
                raise ValueError(f"not a {STATE_FORMAT} file")
            self.spec = number_storylines(self._with_context(state["spec"]))   # 1.6: marks saved without numbers
            self.history = state.get("history") or []
            if self.history and self.history[-1].get("version") == self.spec["version"]:
                self.history[-1]["spec"] = self.spec
            self.undo = list(state.get("undo") or [])
            self.redo = list(state.get("redo") or [])
            self.runs = dict(state.get("runs") or {})
            if not self.history or self.history[-1]["version"] != self.spec["version"]:
                self.history.append({"version": self.spec["version"], "at": now_iso(), "agent": "baseline",
                                     "action": "loaded", "message": None, "run_id": None, "n_ops": 0,
                                     "spec": self.spec})
            repaired = repair_page_panels(self.spec, self.baseline)
            if repaired:
                new = repaired
                new["version"] = self.spec["version"] + 1
                new["updated_at"] = now_iso()
                new["updated_by"] = "baseline"
                message = ("The page's own panels brought back to the current baseline, following the page "
                           "context: " + ", ".join(repaired.pop("_repaired")))
                new["message"] = message
                self.spec = new
                self.history.append({"version": new["version"], "at": new["updated_at"], "agent": "baseline",
                                     "action": "repair", "message": message, "run_id": None, "n_ops": 0,
                                     "spec": new})
                del self.history[:-HISTORY_LIMIT]
                self.undo.append(new["version"] - 1)
                self.redo = []
                logger.info("View state %s: %s", self.path, message)
                self._save()
        except Exception as exc:  # a corrupt or foreign file: keep it aside, start from the baseline
            aside = self.path.with_name(f"{self.path.name}.corrupt-{datetime.now():%Y%m%d%H%M%S}")
            logger.warning("View state %s unreadable (%s); moved to %s, starting from the baseline",
                           self.path, exc, aside)
            try:
                self.path.replace(aside)
            except OSError:
                pass
            self._fresh()

    def _save(self) -> None:
        state = {"format": STATE_FORMAT, "spec": self.spec, "history": self.history, "undo": self.undo,
                 "redo": self.redo, "runs": self.runs}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ reading
    def current(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.spec)

    @property
    def version(self) -> int:
        return self.spec["version"]

    def snapshot(self, version: int) -> Optional[dict]:
        with self._lock:
            for entry in self.history:
                if entry["version"] == version:
                    return copy.deepcopy(entry["spec"])
        return None

    def history_entries(self) -> dict:
        with self._lock:
            # persona (1.4): which reader each version was drawn for, so the audit trail says it
            entries = [{**{k: v for k, v in e.items() if k != "spec"},
                        **({"persona": p} if (p := ((e.get("spec") or {}).get("context") or {}).get("persona")) else {})}
                       for e in reversed(self.history)]
            return {"version": self.version, "can_undo": bool(self._live(self.undo)),
                    "can_redo": bool(self._live(self.redo)), "entries": entries}

    def _live(self, stack: list[int]) -> list[int]:
        have = {e["version"] for e in self.history}
        return [v for v in stack if v in have]

    # ------------------------------------------------------------------ listeners
    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _notify(self) -> None:
        spec = copy.deepcopy(self.spec)
        for fn in list(self._listeners):
            try:
                fn(spec)
            except Exception:  # a closed stream must not stop the others
                logger.exception("view listener failed")

    # ------------------------------------------------------------------ commits
    def _commit(self, new: dict, *, action: str, agent: str, message: Optional[str], run_id: Optional[str] = None,
                n_ops: int = 0, push_undo: bool = True, op_kinds: Optional[list] = None) -> dict:
        prev = self.spec["version"]
        self._with_context(new)
        new["version"] = prev + 1
        new["next_mark_n"] = max(new.get("next_mark_n", 1), self.spec.get("next_mark_n", 1))
        if action in ("undo", "redo"):
            # 1.6: an earlier version comes back with its numbers; numbers given since stay used
            new["next_story_n"] = max(new.get("next_story_n") or 1, self.spec.get("next_story_n") or 1)
        number_storylines(new)
        new["updated_at"] = now_iso()
        new["updated_by"] = agent
        if message:
            new["message"] = message
        else:
            new.pop("message", None)
        if push_undo:
            self.undo.append(prev)
            self.redo.clear()
        self.spec = new
        self.history.append({"version": new["version"], "at": new["updated_at"], "agent": agent, "action": action,
                             "message": message, "run_id": run_id, "n_ops": n_ops,
                             **({"op_kinds": op_kinds} if op_kinds else {}), "spec": new})
        del self.history[:-HISTORY_LIMIT]
        self.undo = self._live(self.undo)[-HISTORY_LIMIT:]
        self.redo = self._live(self.redo)
        self._save()
        self._notify()
        return copy.deepcopy(new)

    # ------------------------------------------------------------------ ops
    def retry_of(self, envelope: dict) -> Optional[dict]:
        """The earlier result when this batch's run_id was already applied (a retry); None otherwise.

        Raises ViewConflict when the run_id was used for a different list of ops."""
        run_id = (envelope.get("agent") or {}).get("run_id")
        if not run_id:
            return None
        with self._lock:
            rec = self.runs.get(f"{envelope['agent']['name']}:{run_id}")
            if rec is None:
                return None
            if rec["hash"] != _ops_hash(envelope):
                raise ViewConflict(f"run_id {run_id!r} was already used for a different batch (version "
                                   f"{rec['version']}); use a new run_id", self.current())
            return {"version": rec["version"], "assigned": rec["assigned"], "marks_added": rec.get("marks_added", []),
                    "replaced": rec.get("replaced", []),
                    "warnings": [f"retry: run_id {run_id!r} was applied as version {rec['version']}; "
                                 "nothing changed"], "retry": True, "spec": self.current()}

    def _page_ids(self) -> set:
        return {p["id"] for p in self.baseline.get("panels") or []}

    def check(self, envelope: dict, meta: Optional[dict] = None) -> None:
        """Raise ViewConflict (stale base_version) or ViewRejected (invalid) without changing anything."""
        with self._lock:
            base = envelope.get("base_version") if isinstance(envelope, dict) else None
            if isinstance(base, int) and not isinstance(base, bool) and base != self.version:
                raise ViewConflict(f"The spec is at version {self.version}, not {base}; read it again and "
                                   "decide again", self.current())
            errors = validate_ops(envelope, self.spec, meta, page_ids=self._page_ids(), limit=self.max_agent_panels)
        if errors:
            raise ViewRejected("The batch was refused; nothing was changed.", errors)

    def apply(self, envelope: dict, meta: Optional[dict] = None) -> dict:
        """Validate and apply a batch atomically. Returns {version, assigned, marks_added, warnings, replaced, spec};
        ``replaced``: the agent panels a new one pushed out, [{id, title, type, agent, by}] (see validate.py)."""
        with self._lock:
            retry = self.retry_of(envelope)
            if retry:
                return retry
            self.check(envelope, meta)
            agent = envelope["agent"]["name"]
            given_run_id = envelope["agent"].get("run_id")
            run_id = given_run_id or f"{agent}-{uuid.uuid4().hex[:12]}"
            new, assigned, added, warnings, replaced = self._apply_ops(self.spec, envelope, run_id)
            errors = validate_spec(new, meta)
            if errors:
                raise ViewRejected("The batch would make an invalid spec; nothing was changed.", errors)
            if given_run_id:  # recorded before the commit, which saves the state
                self.runs[f"{agent}:{given_run_id}"] = {"version": self.version + 1, "assigned": assigned,
                                                        "marks_added": added, "replaced": replaced,
                                                        "hash": _ops_hash(envelope)}
                while len(self.runs) > RUNS_LIMIT:
                    self.runs.pop(next(iter(self.runs)))
            spec = self._commit(new, action="ops", agent=agent, message=envelope.get("message"), run_id=run_id,
                                n_ops=len(envelope["ops"]), op_kinds=op_kinds(envelope["ops"]))
            return {"version": spec["version"], "assigned": assigned, "marks_added": added, "warnings": warnings,
                    "replaced": replaced, "spec": spec}

    def _apply_ops(self, current: dict, envelope: dict, run_id: str) -> tuple[dict, dict, list, list]:
        spec = copy.deepcopy(current)
        agent = envelope["agent"]["name"]
        at = now_iso()
        panels: list[dict] = spec["panels"]
        rows: list[dict] = spec["layout"]["rows"]
        marks: list[dict] = spec["marks"]
        tmp: dict[str, str] = {}
        assigned: dict[str, dict] = {}
        added: list[dict] = []
        warnings: list[str] = []
        replaced: list[dict] = []
        page_ids = self._page_ids()
        limit = self.max_agent_panels

        def panel_index(pid: str) -> Optional[int]:
            return next((i for i, p in enumerate(panels) if p["id"] == pid), None)

        def mark_of(ref: str) -> Optional[dict]:
            mid = tmp.get(ref, ref)
            return next((m for m in marks if m["id"] == mid), None)

        def drop_panel(pid: str) -> None:
            panels[:] = [p for p in panels if p["id"] != pid]
            for row in rows:
                if pid in row["panels"]:
                    row["panels"].remove(pid)
            rows[:] = [r for r in rows if r["panels"]]

        def provenance(given: Optional[dict], obj: dict, pid: Optional[str] = None) -> dict:
            prov = {"agent": agent, "run_id": run_id, "created_at": at,
                    "sources": copy.deepcopy((given or {}).get("sources") or [])}
            warnings.extend(_add_sources(prov, obj, context_on(pid)))
            return prov

        # Sources are recorded as the page will ask them: with the context after the whole batch, and the
        # own context (1.5) the panel they are on has after it.
        final_context = context_of(self._with_context(spec))
        final_own = {p["id"]: p.get("context") for p in panels}
        for op in envelope["ops"]:
            if op["op"] == "set_context":
                final_context = {**final_context, **{k: op[k] for k in CONTEXT_FIELDS if k in op}}
            elif op["op"] == "upsert_panel":
                final_own[op["panel"]["id"]] = op["panel"].get("context")
            elif op["op"] == "set_panel_context":
                final_own[op["id"]] = {k: op[k] for k in PANEL_CONTEXT_FIELDS if op.get(k) is not None} or None

        def context_on(pid: Optional[str]) -> dict:
            return panel_context(final_context, {"context": final_own.get(pid)})

        for i, op in enumerate(envelope["ops"]):
            kind = op["op"]
            if kind == "upsert_panel":
                given = op["panel"]
                panel = copy.deepcopy(given)
                idx = panel_index(panel["id"])
                old = panels[idx] if idx is not None else None
                panel["agent"] = agent
                if agent == "user" and "pinned" in given:
                    panel["pinned"] = bool(given["pinned"])
                else:
                    panel["pinned"] = bool(old.get("pinned")) if old else False
                if panel["type"] == "text" or "provenance" in given:
                    panel["provenance"] = provenance(given.get("provenance"), given, panel["id"])
                if old is None:
                    # the agents' area is full: the new panel takes the place of the oldest one not kept
                    gone = panels_to_replace(panels, marks, page_ids, limit) if is_agent_panel(panel, page_ids) else []
                    slot = next((row for row in rows if gone and gone[0] in row["panels"]), None)
                    for pid in gone:
                        was = panels[panel_index(pid)]
                        marks[:] = [m for m in marks if m["target"] != pid]
                        if slot is not None and pid == gone[0]:
                            slot["panels"][slot["panels"].index(pid)] = panel["id"]
                            panels[:] = [p for p in panels if p["id"] != pid]
                        else:
                            drop_panel(pid)
                        replaced.append({"id": pid, "title": was.get("title"), "type": was.get("type"),
                                         "agent": was.get("agent"), "by": panel["id"]})
                        warnings.append(f"ops/{i}: {panel['id']!r} replaced {pid!r} ({was.get('title')!r}): the "
                                        f"agents keep at most {limit} charts on the page; the user pins one to keep it")
                    if not gone and is_agent_panel(panel, page_ids) and                             sum(1 for p in panels if is_agent_panel(p, page_ids)) >= limit:
                        warnings.append(f"ops/{i}: every agent chart on the page is kept (pinned or with a kept "
                                        f"mark), so {panel['id']!r} is added below them")
                    panels.append(panel)
                    if slot is None:
                        after = op.get("after")
                        at_row = next((r for r, row in enumerate(rows) if after in row["panels"]), None) if after else None
                        rows.insert(len(rows) if at_row is None else at_row + 1, {"panels": [panel["id"]]})
                    elif op.get("after"):
                        warnings.append(f"ops/{i}: {panel['id']!r} took the place of {gone[0]!r}; after is ignored")
                elif is_agent_panel(panel, page_ids):
                    del panels[idx]          # re-pointed: the newest agent panel now (replaced last)
                    panels.append(panel)
                else:
                    panels[idx] = panel
                if old is not None and op.get("after"):
                    warnings.append(f"ops/{i}: {panel['id']!r} already exists; it keeps its place (after "
                                    "is for new panels)")
            elif kind == "remove_panel":
                pid = op["id"]
                marks[:] = [m for m in marks if m["target"] != pid]
                drop_panel(pid)
            elif kind == "set_layout":
                spec["layout"]["rows"] = rows = copy.deepcopy(op["rows"])
            elif kind == "add_mark":
                body = copy.deepcopy(op["mark"])
                n = spec["next_mark_n"]
                spec["next_mark_n"] = n + 1
                given_prov = body.pop("provenance", None)
                body.pop("agent", None)
                mark = {"id": f"m{n}", "n": n, "kind": body.pop("kind"), "agent": agent, "target": body.pop("target")}
                mark.update(body)
                mark["status"] = "proposed"
                mark["provenance"] = provenance(given_prov, op["mark"], mark["target"])
                marks.append(mark)
                entry = {"op": i, "id": mark["id"], "n": n}
                if op.get("tmp_id"):
                    tmp[op["tmp_id"]] = mark["id"]
                    assigned[op["tmp_id"]] = {"id": mark["id"], "n": n}
                    entry["tmp_id"] = op["tmp_id"]
                added.append(entry)
            elif kind == "update_mark":
                mark = mark_of(op["id"])
                changes = copy.deepcopy(op["set"])
                mark.update(changes)
                prov = mark.setdefault("provenance", {"agent": mark["agent"], "created_at": at, "sources": []})
                warnings.extend(_add_sources(prov, changes, context_on(mark.get("target"))))
            elif kind == "keep_mark":
                mark_of(op["id"])["status"] = "kept"
            elif kind == "remove_mark":
                mark = mark_of(op["id"])
                marks[:] = [m for m in marks if m is not mark]
            elif kind == "pin_panel":
                panels[panel_index(op["id"])]["pinned"] = bool(op["pinned"])
            elif kind == "clear" and op["scope"] == "page":
                gone = clearable_page_panels(panels, marks)
                for pid in gone:
                    marks[:] = [m for m in marks if m["target"] != pid]
                    drop_panel(pid)
                marks[:] = [m for m in marks if m.get("status") == "kept"]
                # 1.6: the numbers start again after the kept storylines (none: at 1)
                spec["next_story_n"] = max((m.get("story_n") or 0 for m in marks), default=0) + 1
                spec["title"] = self.baseline.get("title", spec.get("title"))   # an agent's title went with its work
                if gone:
                    warnings.append(f"ops/{i}: the page is cleared ({len(gone)} panels removed); "
                                    "POST /view/reset brings the starting page back")
            elif kind == "clear":
                who = op.get("agent")
                if op["scope"] in ("agent_panels", "all"):
                    original = {p["id"]: p for p in self.baseline["panels"]}
                    for panel in list(panels):
                        owner = panel.get("agent", "baseline")
                        kept = any(m["target"] == panel["id"] and m.get("status") == "kept" for m in marks)
                        # the page's own edits (agent "user", e.g. the year control) are not agent panels
                        if owner not in ("baseline", "user") and not panel.get("pinned") and not kept \
                                and who in (None, owner):
                            marks[:] =[m for m in marks if m["target"] != panel["id"]]
                            if panel["id"] in original:
                                # an agent rewrote a baseline panel (the headline): put the original back
                                panels[panel_index(panel["id"])] = copy.deepcopy(original[panel["id"]])
                                warnings.append(f"ops/{i}: {panel['id']!r} is back to its baseline version")
                            else:
                                drop_panel(panel["id"])
                if op["scope"] in ("marks", "all"):
                    marks[:] = [m for m in marks if m.get("status") == "kept" or who not in (None, m.get("agent"))]
            elif kind == "set_title":
                spec["title"] = op["title"]
            elif kind == "set_context":
                spec["context"] = {**context_of(spec), **{k: op[k] for k in CONTEXT_FIELDS if k in op}}
            elif kind == "set_panel_context":
                # 1.5: the fields given are the panel's whole own context; none: it follows the page again
                panel = panels[panel_index(op["id"])]
                own = {k: op[k] for k in PANEL_CONTEXT_FIELDS if op.get(k) is not None}
                if own:
                    panel["context"] = own
                else:
                    panel.pop("context", None)
        # 1.6: a new storyline gets the next number; the others keep theirs
        number_storylines(spec)
        # a panel added and replaced within the same batch was never on the page
        before = {p["id"] for p in current["panels"]}
        replaced = [r for r in replaced if r["id"] in before]
        return spec, assigned, added, warnings, replaced

    # ------------------------------------------------------------------ undo, redo, reset
    def undo_last(self, agent: str = "user") -> dict:
        with self._lock:
            live = self._live(self.undo)
            if not live:
                raise ViewConflict("Nothing to undo", self.current())
            target = live[-1]
            self.undo = live[:-1]
            self.redo.append(self.version)
            spec = copy.deepcopy(self.snapshot(target))
            return self._commit(spec, action="undo", agent=agent, message=f"Undo: back to version {target}",
                                push_undo=False)

    def redo_last(self, agent: str = "user") -> dict:
        with self._lock:
            live = self._live(self.redo)
            if not live:
                raise ViewConflict("Nothing to redo", self.current())
            target = live[-1]
            self.redo = live[:-1]
            self.undo.append(self.version)
            spec = copy.deepcopy(self.snapshot(target))
            return self._commit(spec, action="redo", agent=agent, message=f"Redo: version {target} again",
                                push_undo=False)

    def reset(self, agent: str = "user") -> dict:
        with self._lock:
            spec = copy.deepcopy(self.baseline)
            return self._commit(spec, action="reset", agent=agent, message="Back to the baseline page")
