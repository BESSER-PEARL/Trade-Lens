# SPDX-License-Identifier: MIT
"""Even our readers are BESSER models: the six personas of the Trade Explorer as B-UML user models.

Each persona is an ObjectModel that instantiates BESSER's User Modeling Language, the reference
domain model the BESSER web editor ships for user diagrams
(besser/utilities/web_modeling_editor/backend/constants/user_buml_model.py). For each one this file:

  1. builds the object model and validates it (ObjectModel.validate: links, multiplicities, enum
     literals), and checks the four OCL invariants of the reference model;
  2. renders it with BESSER's JSONObjectGenerator and folds it into BESSER's normalised user-profile
     document (normalize_user_model_output), the document BESSER's personalisation reads;
  3. runs BESSER's rule-based recommender (build_manual_mapping_recommendation) on it;
  4. derives a persona profile for the page and the agents (to_persona_profile): what a user model
     can decide (statistical literacy, codes, depth, text size, style) comes from the model; what it
     cannot (the default panels, the suggested questions) comes from the hand-written role template
     in contract/personas/persona_profiles.json.

    python model/personas_user_model.py              # validate and print one line per persona
    python model/personas_user_model.py --out DIR    # also write the documents (default build/personas)

The reference model's Goal has no attributes (the full User Modeling Language gives it a name, a
description and a deadline), so the goals live in an extension: a copy of the reference model whose
Goal has name and description, validated as well. The strict models (no goals) validate against the
reference model as shipped.

What the models show (docs/agents.md, "Personas"): BESSER's generic rules key on age, languages and
disabilities, not on statistical literacy, so the domain decisions are the adapter's; the derived
literacy matches the hand-written profiles for all six readers.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path

from besser.BUML.metamodel.object import ObjectModel
from besser.BUML.metamodel.object.builder import ObjectBuilder
from besser.BUML.metamodel.structural import Property, StringType
from besser.generators.json.json_object_generator import JSONObjectGenerator
from besser.utilities.web_modeling_editor.backend.constants.user_buml_model import domain_model as USER_MODEL
from besser.utilities.web_modeling_editor.backend.services.utils.agent_config_manual_mapping_utils import (
    build_manual_mapping_recommendation,
)
from besser.utilities.web_modeling_editor.backend.services.utils.user_profile_utils import normalize_user_model_output

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "contract" / "personas" / "persona_profiles.json"

# The personas as user-model facts only: who they are, what they can do, how they prefer to read.
# What the page does for them (panels, questions, wording) is adaptation, not user model.
PERSONAS = [
    {
        "id": "statec_analyst", "first": "Claire", "age": 41,
        "education": ("MSc Economics", "Masters_degree", "Economics and statistics", "Universite du Luxembourg"),
        "role": ("Trade statistician (expert analyst)", "STATEC"),
        "skills": {"statistical_literacy": "expert", "trade_nomenclature_hs_cn": "expert",
                   "time_series_methods": "expert", "data_tools_api": "high"},
        "languages": {"fra": "C2", "deu": "C2", "ltz": "C2", "eng": "C1"},
        "preferred": ("en", "formal"), "text_size": 14, "outputs": ["text", "image"], "inputs": ["text", "click"],
        "goals": [("Check every figure", "Trace each number on the page to its query, code and revision status"),
                  ("Find what drives a change", "Drill a movement from HS2 down to the CN8 line and partner")],
    },
    {
        "id": "policy_adviser", "first": "Marc", "age": 48,
        "education": ("Master in Public Policy", "Masters_degree", "Public policy", "Sciences Po"),
        "role": ("Policy adviser, foreign trade (policymaker)", "Ministry of the Economy"),
        "skills": {"statistical_literacy": "intermediate", "trade_nomenclature_hs_cn": "basic",
                   "time_series_methods": "basic", "data_tools_api": "none"},
        "languages": {"fra": "C2", "ltz": "C2", "deu": "C1", "eng": "C1"},
        "preferred": ("en", "formal"), "text_size": 16, "outputs": ["text", "image"], "inputs": ["text", "click"],
        "goals": [("Brief the minister", "Three facts on exposure, partners and outlook, ready for a note"),
                  ("Benchmark Luxembourg", "Compare Luxembourg with Belgium, Germany and the EU")],
    },
    {
        "id": "journalist", "first": "Sophie", "age": 33,
        "education": ("BA Journalism", "Bachelors_degree", "Journalism", "Universite de Strasbourg"),
        "role": ("Economy journalist (information forager)", "Luxembourg news media"),
        "skills": {"statistical_literacy": "intermediate", "trade_nomenclature_hs_cn": "none",
                   "time_series_methods": "none", "data_tools_api": "none"},
        "languages": {"fra": "C2", "eng": "C1", "deu": "B2"},
        "preferred": ("en", "informal"), "text_size": 16, "outputs": ["image", "text"], "inputs": ["text", "voice"],
        "goals": [("Find the story", "One headline figure and why it moved, quotable with its source"),
                  ("Illustrate it", "One chart or map she can screenshot or embed")],
    },
    {
        "id": "exporter", "first": "Paul", "age": 52,
        "education": ("Bachelor in Business", "Bachelors_degree", "Business administration", "HEC Liege"),
        "role": ("Export manager, manufacturing SME (business user)", "Luxembourg SME"),
        "skills": {"statistical_literacy": "intermediate", "trade_nomenclature_hs_cn": "high",
                   "time_series_methods": "none", "data_tools_api": "basic"},
        "languages": {"fra": "C2", "deu": "C2", "ltz": "C2", "eng": "B2"},
        "preferred": ("fr", "formal"), "text_size": 16, "outputs": ["text", "image"], "inputs": ["text", "click"],
        "goals": [("Find markets", "Which partners buy his product line, and which are growing"),
                  ("Spot niches", "New and fast-growing CN8 lines in the long tail")],
    },
    {
        "id": "researcher", "first": "Lea", "age": 27,
        "education": ("MSc Data Science", "Masters_degree", "Economics", "Universite du Luxembourg"),
        "role": ("PhD student, international economics (technical user)", "University of Luxembourg"),
        "skills": {"statistical_literacy": "expert", "trade_nomenclature_hs_cn": "high",
                   "time_series_methods": "high", "data_tools_api": "expert"},
        "languages": {"eng": "C2", "fra": "B2", "deu": "B1"},
        "preferred": ("en", "informal"), "text_size": 14, "outputs": ["text", "image"], "inputs": ["text"],
        "goals": [("Reproduce", "Get the query, the method and the data behind every chart"),
                  ("Compare", "Specialisation (RCA), seasonality and breaks against EU peers")],
    },
    {
        "id": "citizen", "first": "Jean", "age": 67,
        "education": ("Secondary school", "High_school_diploma", "General", "Lycee"),
        "role": ("Retired, curious about the economy (inquiring citizen)", "None"),
        "skills": {"statistical_literacy": "basic", "trade_nomenclature_hs_cn": "none",
                   "time_series_methods": "none", "data_tools_api": "none"},
        "languages": {"ltz": "C2", "fra": "C1", "deu": "C1", "eng": "B1"},
        "preferred": ("fr", "formal"), "text_size": 20, "outputs": ["image", "text"], "inputs": ["click", "text"],
        "goals": [("Understand the headlines", "What Luxembourg buys and sells, and whether it is going well"),
                  ("Check a claim", "Verify a figure heard in the news")],
    },
]


def _class(model, name):
    return next(c for c in model.get_classes() if c.name == name)


def _literal(model, enum_name, literal):
    enum = next(e for e in model.get_enumerations() if e.name == enum_name)
    return next(lit for lit in enum.literals if lit.name == literal)


def extended_model():
    """A copy of the reference user model whose Goal has a name and a description (as in the full ULM)."""
    ext = copy.deepcopy(USER_MODEL)
    ext.name = "StatecUserModelExtension"
    _class(ext, "Goal").attributes = {Property(name="name", type=StringType),
                                      Property(name="description", type=StringType)}
    return ext


def build(p: dict, model=USER_MODEL, with_goals: bool = False) -> ObjectModel:
    """The persona ``p`` as an object model conforming to ``model``. Each child links to its parent end."""
    objs = []
    pid = p["id"]

    def new(cname, oname, **attrs):
        b = ObjectBuilder(_class(model, cname)).name(oname)
        return b.attributes(**attrs) if attrs else b

    user = new("User", f"{pid}_user").build()
    objs.append(user)
    objs.append(new("Personal_Information", f"{pid}_info", firstName=p["first"], lastName="Persona",
                    age=p["age"], nationality_iso3166="LU").link(user, "User_end").build())

    comp = new("Competence", f"{pid}_competence").link(user, "User_end").build()
    objs.append(comp)
    degree, dtype, field, by = p["education"]
    objs.append(new("Education", f"{pid}_education", degreeName=degree, providedBy=by, fieldOfDegree=field,
                    degreeType=_literal(model, "DegreeEnum", dtype)).link(comp, "Competence_end").build())
    role, org = p["role"]
    objs.append(new("Experience", f"{pid}_role", roleName=role, companyName=org).link(comp, "Competence_end").build())
    for k, (skill, score) in enumerate(p["skills"].items()):
        objs.append(new("Skill", f"{pid}_skill{k}", name=skill, score=score).link(comp, "Competence_end").build())
    for iso, level in p["languages"].items():
        objs.append(new("Language", f"{pid}_lang_{iso}", iso693_3=iso,
                        level=_literal(model, "CEFR", level)).link(comp, "Competence_end").build())

    pref = new("Preference", f"{pid}_preference").link(user, "User_end").build()
    content = new("Content", f"{pid}_content").link(pref, "Preference_end").build()
    objs += [pref, content]
    lang, style = p["preferred"]
    objs.append(new("PreferredLanguage", f"{pid}_preferred_language", iso639_1=lang,
                    style=_literal(model, "StylesEnum", style)).link(content, "Content_end").build())
    objs.append(new("Design", f"{pid}_design", textSize=p["text_size"],
                    contentContrast=100 if p["text_size"] >= 20 else 70).link(pref, "Preference_end").build())
    # An Interaction_Modality pairs exactly one input with one output (1..1 on both ends).
    for k in range(max(len(p["inputs"]), len(p["outputs"]))):
        im = new("Interaction_Modality", f"{pid}_modality{k}").link(pref, "Preference_end").build()
        objs.append(im)
        inp = p["inputs"][min(k, len(p["inputs"]) - 1)]
        out = p["outputs"][min(k, len(p["outputs"]) - 1)]
        objs.append(new("Input_Modality", f"{pid}_modality{k}_in",
                        value=_literal(model, "InputEnum", inp)).link(im, "Interaction_Modality_end").build())
        objs.append(new("Output_Modality", f"{pid}_modality{k}_out",
                        value=_literal(model, "OutputEnum", out)).link(im, "Interaction_Modality_end").build())

    if with_goals:
        for k, (name, desc) in enumerate(p["goals"]):
            objs.append(new("Goal", f"{pid}_goal{k}", name=name, description=desc).link(user, "User_end").build())
    return ObjectModel(name=f"{pid}_profile", objects=set(objs))


def check_ocl(model: ObjectModel) -> list[str]:
    """The four OCL invariants of the reference model, evaluated in Python."""
    errors = []
    for obj in model.objects:
        cname = obj.classifier.name
        get = lambda a: next((s.value.value for s in obj.slots if s.attribute.name == a), None)  # noqa: E731
        if cname == "Personal_Information" and get("age") is not None and not 0 <= get("age") <= 120:
            errors.append(f"{obj.name_}: pi_age_range")
        if cname == "Skill" and not get("name"):
            errors.append(f"{obj.name_}: skill_name_not_empty")
        if cname == "Education" and not (get("degreeName") and get("providedBy")):
            errors.append(f"{obj.name_}: education_required_fields")
        if cname == "Disability" and not get("description"):
            errors.append(f"{obj.name_}: disability_description_not_empty")
    return errors


def profile_document(model: ObjectModel) -> dict:
    """BESSER's normalised user-profile document: JSONObjectGenerator, then normalize_user_model_output."""
    with tempfile.TemporaryDirectory() as tmp:
        JSONObjectGenerator(model, output_dir=tmp).generate()
        normalize_user_model_output(model, tmp)
        return json.loads((Path(tmp) / f"{model.name}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the adapter: user model -> profile
ROLE_KEYWORDS = [  # (keyword in Experience.roleName, persona id): the first match wins
    ("statistician", "statec_analyst"), ("policy", "policy_adviser"), ("journalist", "journalist"),
    ("export", "exporter"), ("phd", "researcher"), ("student", "researcher"),
    ("citizen", "citizen"), ("retired", "citizen"),
]
LEVEL = {"none": 0, "basic": 1, "intermediate": 2, "high": 3, "expert": 4}


def _walk(node, cls_name):
    """Every node of a class under ``node`` in the normalised document (its keys are class names)."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            for item in v if isinstance(v, list) else [v]:
                if isinstance(item, dict):
                    if k == cls_name:
                        out.append(item)
                    out.extend(_walk(item, cls_name))
    return out


def to_persona_profile(doc: dict, besser_config: dict, templates: dict) -> dict:
    """A persona profile from BESSER's profile document and recommendation, on the role's template."""
    m = doc["model"]
    skills = {s.get("name"): s.get("score") for s in _walk(m, "Skill")}
    role = " ".join(e.get("roleName", "") for e in _walk(m, "Experience")).lower()
    pid = next((p for kw, p in ROLE_KEYWORDS if kw in role), "citizen")
    lit = LEVEL.get(skills.get("statistical_literacy", "basic"), 1)
    hs = LEVEL.get(skills.get("trade_nomenclature_hs_cn", "none"), 0)
    api = LEVEL.get(skills.get("data_tools_api", "none"), 0)
    pref = (_walk(m, "PreferredLanguage") or [{}])[0]
    design = (_walk(m, "Design") or [{}])[0]
    outputs = [x.get("value") for x in _walk(m, "Output_Modality")]
    pres = besser_config.get("presentation", {})
    matched = besser_config.get("_matched", [])

    # The user model decides only what it can (literacy, codes, depth, text size, language, style);
    # the role template keeps the rest (panels, questions, wording, lead agent).
    readability = any(r in matched for r in
                      ("older_adults_readability", "low_literacy_accessibility", "children_short_attention"))
    size = int(design.get("textSize") or 14)
    besser_size = int((pres.get("interfaceStyle") or {}).get("size") or 0)
    answer = {
        "depth": "full" if lit >= 4 else "brief" if lit >= 2 else "headline",
        "show_codes": hs >= 3,
        "show_method": lit >= 4 or api >= 4,
        "uncertainty": "backtest" if lit >= 4 else "interval" if lit >= 2 else "plain_range",
    }
    if api >= 3 or lit >= 4:
        answer["show_sources"] = "inline"
    if lit <= 1 and "image" in outputs:
        answer["lead_with"] = "chart"
    profile = copy.deepcopy(templates.get(pid, {}))
    profile["literacy"] = "expert" if lit >= 4 else "intermediate" if lit >= 2 else "general"
    profile["answer"] = {**profile.get("answer", {}), **answer}
    # BESSER's own vocabulary, so the block can be fed to BAFGenerator(config=...).
    profile["presentation"] = {
        "agentLanguage": {"fr": "french", "de": "german", "lb": "luxembourgish"}.get(pref.get("iso639_1"), "english"),
        "agentStyle": "technical" if lit >= 4 else ("informal" if pref.get("style") == "informal" else "formal"),
        "languageComplexity": "complex" if lit >= 4 else "medium" if lit >= 2 else "simple",
        "sentenceLength": "verbose" if lit >= 4 else "concise",
        "useAbbreviations": hs >= 3,
        "interfaceStyle": {"size": max(size, besser_size) if readability else size,
                           "contrast": (pres.get("interfaceStyle") or {}).get("contrast", "medium")
                           if readability else "medium"},
    }
    if lit <= 1 and "image" in outputs:
        profile["charts"] = {**profile.get("charts", {}), "charts_first": True}
    profile["id"] = pid
    profile["derived_from"] = {"user_model": doc.get("name"), "besser_rules": matched}
    return profile


def run(out: Path | None = None) -> list[dict]:
    """Validate every persona, run BESSER's pipeline and the adapter; one report row per persona."""
    templates = {p["id"]: p for p in json.loads(PROFILES.read_text(encoding="utf-8"))["profiles"]}
    ext = extended_model()
    rows = []
    for p in PERSONAS:
        strict = build(p)
        r_strict = strict.validate(raise_exception=False)
        extended = build(p, ext, with_goals=True)
        r_ext = extended.validate(raise_exception=False)
        doc = profile_document(extended)
        rec = build_manual_mapping_recommendation(doc, user_profile_name=p["id"])
        config = {**rec["config"], "_matched": [r["id"] for r in rec["matchedRules"]]}
        profile = to_persona_profile(doc, config, templates)
        rows.append({"persona": p["id"], "objects": len(extended.objects), "links": len(extended.links),
                     "strict_valid": r_strict["success"], "strict_errors": r_strict["errors"],
                     "ocl_errors": check_ocl(strict), "extension_valid": r_ext["success"],
                     "extension_errors": r_ext["errors"], "besser_rules": config["_matched"],
                     "profile": profile})
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{p['id']}.user_profile.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
            (out / f"{p['id']}.besser_agent_config.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    if out:
        (out / "generated_persona_profiles.json").write_text(
            json.dumps({"generated_by": "model/personas_user_model.py", "profiles": [r["profile"] for r in rows]},
                       indent=2, ensure_ascii=False), encoding="utf-8")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, nargs="?", const=ROOT / "build" / "personas", default=None,
                    help="write the profile documents, BESSER's recommendations and the derived profiles here")
    args = ap.parse_args(argv)
    rows = run(args.out)
    for r in rows:
        ok = r["strict_valid"] and r["extension_valid"] and not r["ocl_errors"]
        print(f"{r['persona']:15} {'valid' if ok else 'INVALID'}  objects={r['objects']:2} links={r['links']:2}  "
              f"BESSER rules: {', '.join(r['besser_rules']) or '-'}")
        for e in r["strict_errors"] + r["extension_errors"] + r["ocl_errors"]:
            print("   ", e)
    if args.out:
        print(f"written to {args.out}")
    return 0 if all(r["strict_valid"] and r["extension_valid"] and not r["ocl_errors"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
