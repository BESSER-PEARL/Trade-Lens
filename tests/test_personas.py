# SPDX-License-Identifier: MIT
"""Personas (contract 1.4): the profiles, their page variants, the copies the page reads, and the
same readers as BESSER user models (model/personas_user_model.py)."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
PERSONAS = REPO / "contract" / "personas"
FRONTEND = REPO / "app" / "frontend" / "src" / "view" / "personas"
VIEW_DIR = REPO / "app" / "backend" / "view"


def _view():
    """app/backend/view without putting app/backend on sys.path (as tests/test_view_contract.py)."""
    if "view" in sys.modules and getattr(sys.modules["view"], "__file__", "").startswith(str(VIEW_DIR)):
        return sys.modules["view"]
    spec = importlib.util.spec_from_file_location("view", VIEW_DIR / "__init__.py", submodule_search_locations=[str(VIEW_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["view"] = module
    spec.loader.exec_module(module)
    return module


def load(name: str) -> dict:
    return json.loads((PERSONAS / name).read_text(encoding="utf-8"))


PROFILES = load("persona_profiles.json")["profiles"]
VARIANTS = load("persona_variants.json")["variants"]
IDS = [p["id"] for p in PROFILES]


@pytest.mark.parametrize("name", ["persona_profiles.json", "persona_variants.json"])
def test_the_page_reads_an_identical_copy(name):
    assert (FRONTEND / name).read_bytes() == (PERSONAS / name).read_bytes(), \
        f"app/frontend/src/view/personas/{name} differs from contract/personas/{name}: copy it again"


@pytest.mark.parametrize("profile", PROFILES, ids=IDS)
def test_profiles_are_valid(profile):
    schema = json.loads((PERSONAS / "persona_profile.schema.json").read_text(encoding="utf-8"))
    errors = [f"{list(e.path)}: {e.message}" for e in Draft202012Validator(schema).iter_errors(profile)]
    assert errors == []
    assert profile["default_view"].get("variant_ops", profile["id"]) in VARIANTS


def test_six_readers_with_distinct_ids():
    assert IDS == ["statec_analyst", "policy_adviser", "journalist", "exporter", "researcher", "citizen"]


@pytest.mark.parametrize("profile", PROFILES, ids=IDS)
def test_a_persona_switch_is_one_valid_batch(profile):
    """What the page sends when the reader picks a persona: set_context {persona, ...context}, the previous
    persona's panels removed, then this persona's panels; valid on the baseline, as agent 'user'."""
    view = _view()
    baseline = view.load_baseline()
    variant = VARIANTS[profile["default_view"].get("variant_ops", profile["id"])]
    ops = [{"op": "set_context", "persona": profile["id"], **profile["default_view"]["context"]}, *variant["ops"]]
    envelope = {"agent": {"name": "user", "run_id": f"persona-{profile['id']}"}, "message": variant["message"], "ops": ops}
    assert view.validate_ops(envelope, copy.deepcopy(baseline)) == []
    added = [op["panel"]["id"] for op in variant["ops"] if op["op"] == "upsert_panel"]
    assert sorted(added) == sorted(variant["remove_on_leave"])
    shown = set(profile["default_view"]["show_panels"]) | set(profile["default_view"].get("hide_panels", []))
    assert shown == {p["id"] for p in baseline["panels"]}, "every baseline panel is shown or folded, never dropped"


def test_variants_carry_no_figures():
    """A persona changes panels and words, never a number: no typed figures in its panels."""
    view = _view()
    for pid, variant in VARIANTS.items():
        for op in variant["ops"]:
            assert view.validate.FIGURE_RE.search(json.dumps(op.get("panel", {}).get("text", ""))) is None, pid


def test_a_persona_switch_never_takes_a_mark_off_the_page():
    """personaSwitchOps (the page's "Reading as" batch) removes only the leaving persona's panels that carry no
    mark, and never with force (force would take the marks with them)."""
    source = (REPO / "app" / "frontend" / "src" / "view" / "personas.ts").read_text(encoding="utf-8")
    body = source[source.index("export function personaSwitchOps"):source.index("export function switchMessage")]
    assert "force" not in body
    assert "!marked.has(p.id)" in body and "spec.marks" in body


# ------------------------------------------------------------------ the BESSER user models
besser = pytest.importorskip("besser")


@pytest.fixture(scope="module")
def user_models():
    sys.path.insert(0, str(REPO / "model"))
    try:
        try:
            import personas_user_model as pum
        except ImportError as e:  # BESSER's user model lives in its web-editor backend, whose extras CI does not install
            pytest.skip(f"BESSER web-editor backend dependencies not installed: {e}")
        return pum, pum.run()
    finally:
        sys.path.remove(str(REPO / "model"))


def test_the_readers_are_valid_besser_user_models(user_models):
    _, rows = user_models
    assert [r["persona"] for r in rows] == IDS
    for r in rows:
        assert r["strict_valid"], (r["persona"], r["strict_errors"])
        assert r["extension_valid"], (r["persona"], r["extension_errors"])
        assert r["ocl_errors"] == [], r["persona"]


def test_the_user_models_give_the_profiles_literacy_and_valid_profiles(user_models):
    """The adapter's profiles are schema-valid, and what a user model decides (statistical literacy) matches
    the hand-written profiles for all six; BESSER's own rule gives the citizen large text."""
    _, rows = user_models
    schema = json.loads((PERSONAS / "persona_profile.schema.json").read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in PROFILES}
    for r in rows:
        prof = r["profile"]
        assert list(Draft202012Validator(schema).iter_errors(prof)) == [], r["persona"]
        assert prof["literacy"] == by_id[r["persona"]]["literacy"], r["persona"]
    citizen = next(r for r in rows if r["persona"] == "citizen")
    assert "older_adults_readability" in citizen["besser_rules"]
    assert citizen["profile"]["presentation"]["interfaceStyle"]["size"] >= 20
