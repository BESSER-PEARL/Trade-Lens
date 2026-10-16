# SPDX-License-Identifier: MIT
"""Contract 1.6: a storyline keeps its page number.

The number a reply cites ("[3]") and the circle on the chart come from ``story_n``, which the store gives
when the storyline's first mark is added. Replacing an agent chart, removing a storyline, undo and redo
never renumber the others, and a number is never given to another storyline until the page is reset or
cleared. Runs the store directly (no database, no pre-flight).
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "app" / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents.kit.testing import LocalView, StubViewClient, contract  # noqa: E402
from agents.kit.view import story_numbers  # noqa: E402

store_mod = importlib.import_module(f"{contract().__name__}.store")
validate = contract().validate


def bar(pid: str) -> dict:
    return {"id": pid, "type": "bar", "title": f"Chart {pid}",
            "queries": [{"key": "main", "endpoint": "/insights/ranking",
                         "params": {"by": "partner", "period": "2025", "flow": "EXPORT"}}]}


def highlight(target: str, code: str = "FR", thread: str | None = None) -> dict:
    mark = {"kind": "highlight", "target": target, "params": {"codes": [code]}, "note": f"{code} stands out."}
    if thread:
        mark["thread"] = thread
    return {"op": "add_mark", "mark": mark}


def event(at: str, thread: str | None = None) -> dict:
    mark = {"kind": "event", "target": "trend", "params": {"at": at, "label": f"Event {at}"}, "note": "Something happened."}
    if thread:
        mark["thread"] = thread
    return {"op": "add_mark", "mark": mark}


def send(store, agent: str, *ops) -> dict:
    return store.apply({"agent": {"name": agent}, "ops": list(ops)})


def chart(store, agent: str, pid: str) -> dict:
    """An agent chart with one mark on it: one new storyline."""
    return send(store, agent, {"op": "upsert_panel", "panel": bar(pid)}, highlight(pid))


def numbers(spec: dict) -> dict:
    """{target or thread: storyline number} of the marks on the page."""
    return {m.get("thread") or m["target"]: m["story_n"] for m in spec["marks"]}


@pytest.fixture
def store(tmp_path):
    return store_mod.ViewStore(tmp_path / "view_state.json", max_agent_panels=3)


def test_a_new_storyline_gets_the_next_number_and_a_thread_shares_it(store):
    send(store, "cause_finder", event("2020-03", thread="events"))
    chart(store, "visualizer", "a1")
    spec = send(store, "cause_finder", event("2021-01", thread="events"))["spec"]
    assert numbers(spec) == {"events": 1, "a1": 2}
    assert [m["story_n"] for m in spec["marks"] if m.get("thread") == "events"] == [1, 1]
    assert spec["next_story_n"] == 3 and validate.validate_spec(spec) == []


def test_replacing_a_chart_keeps_the_other_numbers(store):
    """Four questions with three slots: the fourth chart replaces the first; [2] and [3] stay."""
    chart(store, "trend_finder", "a1")
    chart(store, "cause_finder", "a2")
    chart(store, "forecaster", "a3")
    out = chart(store, "visualizer", "a4")
    assert [r["id"] for r in out["replaced"]] == ["a1"]
    assert numbers(out["spec"]) == {"a2": 2, "a3": 3, "a4": 4}   # not renumbered 1, 2, 3
    assert story_numbers(out["spec"]) == {"m2": 2, "m3": 3, "m4": 4}


def test_a_removed_storyline_loses_its_number_for_good(store):
    send(store, "cause_finder", event("2020-03", thread="events"))
    send(store, "forecaster", event("2022-01"))
    send(store, "user", {"op": "remove_mark", "id": "m1"})
    spec = send(store, "cause_finder", event("2023-01", thread="events"))["spec"]
    # the thread comes back as a new storyline: a new number, never 1 again, and 2 is unchanged
    assert {m["id"]: m["story_n"] for m in spec["marks"]} == {"m2": 2, "m3": 3}


def test_undo_and_redo_restore_the_numbers_and_never_reuse_one(store):
    send(store, "cause_finder", event("2020-03"))
    send(store, "forecaster", event("2022-01"))
    send(store, "user", {"op": "remove_mark", "id": "m1"})
    undone = store.undo_last()
    assert {m["id"]: m["story_n"] for m in undone["marks"]} == {"m1": 1, "m2": 2}
    redone = store.redo_last()
    assert {m["id"]: m["story_n"] for m in redone["marks"]} == {"m2": 2}
    store.undo_last()
    store.undo_last()                                   # back to one storyline, counter stays at 3
    spec = send(store, "trend_finder", event("2024-01"))["spec"]
    assert {m["id"]: m["story_n"] for m in spec["marks"]} == {"m1": 1, "m3": 3}


def test_reset_starts_the_numbers_again(store):
    send(store, "cause_finder", event("2020-03"))
    send(store, "forecaster", event("2022-01"))
    store.reset()
    spec = send(store, "trend_finder", event("2024-01"))["spec"]
    assert {m["id"]: m["story_n"] for m in spec["marks"]} == {"m3": 1}


def test_clear_the_page_starts_after_the_kept_storylines(store):
    send(store, "cause_finder", event("2020-03"))
    send(store, "forecaster", event("2022-01"))
    send(store, "trend_finder", event("2023-01"))
    send(store, "user", {"op": "keep_mark", "id": "m2"})
    cleared = send(store, "user", {"op": "clear", "scope": "page"})["spec"]
    assert {m["id"]: m["story_n"] for m in cleared["marks"]} == {"m2": 2} and cleared["next_story_n"] == 3
    spec = send(store, "visualizer", event("2024-01"))["spec"]
    assert {m["id"]: m["story_n"] for m in spec["marks"]} == {"m2": 2, "m4": 3}
    send(store, "user", {"op": "remove_mark", "id": "m2"}, {"op": "remove_mark", "id": "m4"})
    send(store, "user", {"op": "clear", "scope": "page"})
    store.reset()
    assert send(store, "visualizer", event("2025-01"))["spec"]["marks"][0]["story_n"] == 1


def test_a_mark_moved_to_another_thread_takes_that_storylines_number(store):
    send(store, "cause_finder", event("2020-03", thread="a"), event("2021-03", thread="a"))
    send(store, "cause_finder", event("2022-03", thread="b"))
    spec = send(store, "cause_finder", {"op": "update_mark", "id": "m3", "set": {"thread": "a"}})["spec"]
    assert {m["id"]: m["story_n"] for m in spec["marks"]} == {"m1": 1, "m2": 1, "m3": 1}
    # a mark split off its storyline is a new storyline: a new number, the old one keeps its own
    spec = send(store, "cause_finder", {"op": "update_mark", "id": "m2", "set": {"thread": "c"}})["spec"]
    assert {m["id"]: m["story_n"] for m in spec["marks"]} == {"m1": 1, "m2": 3, "m3": 1}


def test_a_state_saved_without_numbers_is_numbered_on_load(store, tmp_path):
    send(store, "cause_finder", event("2020-03", thread="events"))
    send(store, "forecaster", event("2022-01"))
    send(store, "cause_finder", event("2021-01", thread="events"))
    send(store, "user", {"op": "remove_mark", "id": "m2"})
    state = json.loads(store.path.read_text(encoding="utf-8"))
    for entry in [state, *state["history"]]:
        entry["spec"].pop("next_story_n", None)
        for m in entry["spec"]["marks"]:
            m.pop("story_n", None)
    old = tmp_path / "old_state.json"
    old.write_text(json.dumps(state), encoding="utf-8")
    loaded = store_mod.ViewStore(old, max_agent_panels=3).current()
    assert {m["id"]: m["story_n"] for m in loaded["marks"]} == {"m1": 1, "m3": 1}
    assert loaded["next_story_n"] == 2 and validate.validate_spec(loaded) == []


def test_the_validator_checks_the_numbers(store):
    send(store, "cause_finder", event("2020-03", thread="events"), event("2021-01", thread="events"))
    spec = send(store, "forecaster", event("2022-01"))["spec"]
    bad = json.loads(json.dumps(spec))
    bad["marks"][1]["story_n"] = 2                       # one storyline, two numbers; 2 used twice
    codes = [e["code"] for e in validate.validate_spec(bad)]
    assert codes and set(codes) == {"bad_story_number"}
    bad = json.loads(json.dumps(spec))
    bad["next_story_n"] = 2                              # a number not below the counter
    assert [e["code"] for e in validate.validate_spec(bad)] == ["bad_story_number"]
    # an agent cannot choose a number: the backend gives it
    op = event("2023-01")
    op["mark"]["story_n"] = 7
    errors = validate.validate_ops({"agent": {"name": "forecaster"}, "ops": [op]}, spec)
    assert errors and "story_n" in json.dumps(errors)


def test_page_py_reads_the_stable_numbers(store):
    from lux_insights import page
    chart(store, "trend_finder", "a1")
    chart(store, "cause_finder", "a2")
    chart(store, "trend_finder", "a3")
    send(store, "forecaster", event("2022-01", thread="band"), event("2023-01", thread="band"))
    spec = chart(store, "visualizer", "a4")["spec"]          # replaces a1: storyline 1 is gone
    assert page.numbers(spec) == [2, 3, 4, 5]
    assert page.page_numbers(spec, ["m6", "m5", "m4"]) == [5, 4]
    assert [m["id"] for m in page.marks_of_number(spec, 4)] == ["m4", "m5"]
    assert page.marks_of_number(spec, 1) == [] and page.owner_of_number(spec, 5) == "visualizer"
    # by number, with a gap for 1: every number on the page is in range(1, len + 1)
    assert page.storylines(spec) == [None, "m2", "m3", "band", "m6"]
    text = page.summary(spec)
    assert "- [4] m4 forecaster" in text and "- [5] m6 visualizer" in text and "- [1]" not in text
    # a spec from before 1.6: the place in order of first n
    for m in spec["marks"]:
        m.pop("story_n")
    assert page.numbers(spec) == [1, 2, 3, 4]


def test_local_view_numbers_like_the_backend():
    store = LocalView()
    trend = StubViewClient(store, "cause_finder")
    trend.send([event("2020-03", thread="events")])
    trend.send([event("2022-01")])
    StubViewClient(store, "user").send([{"op": "remove_mark", "id": "m1"}])
    spec = trend.send([event("2023-01")])["spec"]
    assert {m["id"]: m["story_n"] for m in spec["marks"]} == {"m2": 2, "m3": 3}
    assert contract().validate_spec(spec) == []
