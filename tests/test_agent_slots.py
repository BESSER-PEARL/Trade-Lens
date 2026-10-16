# SPDX-License-Identifier: MIT
"""The agents' slots: a new agent chart replaces the oldest one the user has not kept (docs/agents.md,
"The agents' area"), pinned panels are never changed by an agent, and marks may still go on them."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from agents.kit.testing import LocalView, contract  # noqa: E402

contract()
store_mod = importlib.import_module("besser_view_contract.store")
validate = contract().validate


def bar(pid: str, title: str = "") -> dict:
    return {"id": pid, "type": "bar", "title": title or f"Chart {pid}",
            "queries": [{"key": "main", "endpoint": "/insights/ranking",
                         "params": {"by": "partner", "period": "2025", "flow": "EXPORT"}}]}


def highlight(target: str, code: str = "FR") -> dict:
    return {"op": "add_mark", "mark": {"kind": "highlight", "target": target, "params": {"codes": [code]},
                                       "note": "This partner."}}


@pytest.fixture
def store(tmp_path):
    return store_mod.ViewStore(tmp_path / "view_state.json", max_agent_panels=3)


def send(store, agent: str, *ops, **kw) -> dict:
    return store.apply({"agent": {"name": agent}, "ops": list(ops), **kw})


def refused(store, agent: str, *ops) -> list[str]:
    with pytest.raises(store_mod.ViewRejected) as exc:
        send(store, agent, *ops)
    return [e["code"] for e in exc.value.errors]


def agent_ids(spec: dict) -> list[str]:
    page = validate.baseline_panel_ids()
    return [p["id"] for p in spec["panels"] if validate.is_agent_panel(p, page)]


def rows(spec: dict) -> list[list[str]]:
    return [r["panels"] for r in spec["layout"]["rows"]]


def fill(store, *ids, agent="visualizer"):
    for pid in ids:
        send(store, agent, {"op": "upsert_panel", "panel": bar(pid)})


def test_a_fourth_chart_replaces_the_oldest_in_its_slot(store):
    fill(store, "a", "b")
    send(store, "visualizer", {"op": "upsert_panel", "panel": bar("c")}, highlight("a"))
    before = store.current()
    slot = next(i for i, r in enumerate(rows(before)) if "a" in r)
    out = send(store, "trend_finder", {"op": "upsert_panel", "panel": bar("d", "The new one"), "after": "trend"},
               highlight("d"))
    spec = out["spec"]
    assert agent_ids(spec) == ["b", "c", "d"]
    assert rows(spec)[slot] == ["d"] and len(rows(spec)) == len(rows(before))   # the page does not jump
    assert [m["target"] for m in spec["marks"]] == ["d"]                       # a's proposed mark went with it
    assert out["replaced"] == [{"id": "a", "title": "Chart a", "type": "bar", "agent": "visualizer", "by": "d"}]
    assert any("replaced 'a'" in w for w in out["warnings"])
    assert not validate_spec_errors(spec)


def validate_spec_errors(spec):
    return validate.validate_spec(spec)


def test_room_left_means_nothing_is_replaced(store):
    fill(store, "a", "b")
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("c")})
    assert out["replaced"] == [] and agent_ids(out["spec"]) == ["a", "b", "c"]


def test_re_pointing_a_chart_keeps_its_place_and_makes_it_the_newest(store):
    fill(store, "a", "b", "c")
    before = rows(store.current())
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("a", "Re-pointed")})
    assert out["replaced"] == [] and rows(out["spec"]) == before
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("d")})
    assert [r["id"] for r in out["replaced"]] == ["b"]            # a was drawn again, so b is the oldest
    assert agent_ids(out["spec"]) == ["c", "a", "d"]


def test_a_pinned_chart_is_never_replaced_changed_or_removed_by_an_agent(store):
    fill(store, "a", "b", "c")
    send(store, "user", {"op": "pin_panel", "id": "a", "pinned": True})
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("d")})
    assert [r["id"] for r in out["replaced"]] == ["b"]
    assert agent_ids(out["spec"]) == ["a", "c", "d"]              # a stays, b gave its place to d
    assert refused(store, "visualizer", {"op": "upsert_panel", "panel": bar("a", "Changed")}) == ["pinned"]
    assert refused(store, "visualizer", {"op": "remove_panel", "id": "a", "force": True}) == ["pinned"]
    assert refused(store, "visualizer", {"op": "pin_panel", "id": "a", "pinned": False}) == ["pinned"]
    spec = send(store, "visualizer", {"op": "clear", "scope": "all"})["spec"]
    assert agent_ids(spec) == ["a"]
    # marks annotate, they do not change the chart: an agent may still mark a pinned panel
    spec = send(store, "trend_finder", highlight("a", "DE"))["spec"]
    assert [m["target"] for m in spec["marks"]] == ["a"]
    # the user unpins, and only then may the agents replace it
    send(store, "user", {"op": "pin_panel", "id": "a", "pinned": False})
    assert send(store, "visualizer", {"op": "upsert_panel", "panel": bar("a", "Changed")})["spec"]


def test_a_pinned_chart_stays_while_the_others_rotate(store):
    fill(store, "a", "b", "c")
    send(store, "user", {"op": "pin_panel", "id": "a", "pinned": True})
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("d")})
    assert [r["id"] for r in out["replaced"]] == ["b"]
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("e")})
    assert [r["id"] for r in out["replaced"]] == ["c"]
    assert agent_ids(out["spec"]) == ["a", "d", "e"]


def test_when_every_chart_is_kept_the_page_grows(store):
    fill(store, "a", "b", "c")
    send(store, "user", *[{"op": "pin_panel", "id": pid, "pinned": True} for pid in "abc"])
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("d")})
    assert out["replaced"] == [] and agent_ids(out["spec"]) == ["a", "b", "c", "d"]
    assert any("every agent chart on the page is kept" in w for w in out["warnings"])


def test_a_chart_with_a_kept_mark_counts_as_pinned(store):
    fill(store, "a")
    mid = send(store, "visualizer", highlight("a"))["marks_added"][0]["id"]
    send(store, "user", {"op": "keep_mark", "id": mid})
    fill(store, "b", "c")
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("d")})
    assert [r["id"] for r in out["replaced"]] == ["b"]
    assert "a" in agent_ids(out["spec"]) and any(m["id"] == mid for m in out["spec"]["marks"])


def test_the_pages_own_panels_never_count(store):
    send(store, "user", {"op": "upsert_panel", "panel": bar("for_the_reader")})       # a persona's panel
    send(store, "visualizer", {"op": "upsert_panel", "panel": {**store.baseline["panels"][0], "title": "Rewritten"}})
    fill(store, "a", "b", "c", "d")
    spec = store.current()
    ids = [p["id"] for p in spec["panels"]]
    assert "for_the_reader" in ids and store.baseline["panels"][0]["id"] in ids
    assert agent_ids(spec) == ["b", "c", "d"]


def test_a_crowded_page_shrinks_back_to_the_slots(store):
    store.max_agent_panels = 50
    fill(store, "a", "b", "c", "d", "e")
    store.max_agent_panels = 3
    out = send(store, "visualizer", {"op": "upsert_panel", "panel": bar("f")})
    assert [r["id"] for r in out["replaced"]] == ["a", "b", "c"]
    assert agent_ids(out["spec"]) == ["d", "e", "f"]
    assert not validate_spec_errors(out["spec"])


def test_a_batch_cannot_mark_the_chart_it_replaced(store):
    fill(store, "a", "b", "c")
    codes = refused(store, "visualizer", {"op": "upsert_panel", "panel": bar("d")}, highlight("a"))
    assert codes == ["unknown_target"]
    assert agent_ids(store.current()) == ["a", "b", "c"]          # nothing changed


def test_the_validator_alone_simulates_the_replacement():
    spec = validate.load_baseline()
    env = {"agent": {"name": "visualizer"},
           "ops": [{"op": "upsert_panel", "panel": bar(pid)} for pid in "abcd"] + [highlight("b")]}
    assert validate.validate_ops(env, spec, limit=3) == []
    env["ops"][-1] = highlight("a")
    assert [e["code"] for e in validate.validate_ops(env, spec, limit=3)] == ["unknown_target"]


def test_the_local_view_replaces_like_the_backend(monkeypatch):
    monkeypatch.setenv("VIEW_MAX_AGENT_PANELS", "3")
    local = LocalView()
    for pid in "abcd":
        local.post_ops({"agent": {"name": "visualizer"}, "ops": [{"op": "upsert_panel", "panel": bar(pid)}]})
    assert agent_ids(local.spec) == ["b", "c", "d"]
    assert not validate.validate_spec(local.spec)
