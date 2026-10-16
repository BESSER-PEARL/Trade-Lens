# SPDX-License-Identifier: MIT
"""All the project's BESSER models in one BESSER Project file, to import into the BESSER web editor.

    python model/build_project.py            # -> model/lux_trade_project.py, checked by the editor's importer

The project holds:
  - the class diagram imported from Eurostat's SDMX metadata (lux_trade_model.py),
  - the GUI model of the first page (lux_trade_gui.py),
  - the two neural networks of the `nn` forecast (forecast_nn.py: mlp and gru),
  - the six readers as user models (personas_user_model.py), shown by the editor as user diagrams.

The file is written by BESSER's own project code builder (project_to_code), then read back with the
editor's own importer (project_to_json, behind "Import project" in the editor) to check that every
model comes through. BESSER_model.md explains each model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from besser.BUML.metamodel.project import Project  # noqa: E402
from besser.BUML.metamodel.structural import Metadata  # noqa: E402
from besser.utilities.buml_code_builder.project_builder import project_to_code  # noqa: E402

OUT = HERE / "lux_trade_project.py"


def first_and_last_year() -> tuple[str, str]:
    """The annual span of the data recipe, as generate.py reads it."""
    recipe = json.loads((HERE / "lux_trade_slices.json").read_text(encoding="utf-8"))["slices"]
    annual = [s for s in recipe if s["key"].get("freq") == "A"]
    return min(s["start"] for s in annual), max(s["end"] for s in annual)


def readers(personas: list[dict], build):
    """The six readers in ONE user model (one user diagram in the editor).

    BESSER's project code builder writes at most one user object model per project (with more it keeps
    only the reference model), so the readers share one model; each object's name starts with its reader's
    id, so the six stay apart in the diagram."""
    from besser.BUML.metamodel.object import ObjectModel

    objects = set()
    for p in personas:
        model = build(p)  # the strict user model: it validates against the reference model
        for obj in model.objects:
            if not obj.name.startswith(f"{p['id']}_"):
                obj.name = f"{p['id']}_{obj.name}"
        objects |= set(model.objects)
    merged = ObjectModel(name="readers", objects=objects)
    result = merged.validate(raise_exception=False)
    if isinstance(result, dict) and not result.get("success", True):
        raise SystemExit(f"the readers' user model does not validate: {result}")
    return merged


def build_project() -> Project:
    from forecast_nn import gru_model, mlp_model
    from lux_trade_gui import build_gui_model
    from lux_trade_model import domain_model
    from personas_user_model import PERSONAS, build

    first, last = first_and_last_year()
    models = [domain_model, build_gui_model(domain_model, period=last, first=first), mlp_model(), gru_model()]
    models.append(readers(PERSONAS, build))
    return Project(
        name="LuxTradeExplorer",
        models=models,
        owner="Team BESSER",
        metadata=Metadata(description="Luxembourg Trade Explorer (STATEC Hackathon 2026): the class diagram "
                                      "imported from Eurostat's SDMX metadata, the GUI model of the first page, "
                                      "the neural networks of the forecaster and the six readers as user models."),
    )


OUT_JSON = HERE / "lux_trade_project.json"


def editor_project_json(project: Project) -> dict:
    """The project in the editor's own JSON format, with the readers as a real user diagram.

    BESSER 8.1.0's B-UML importer (the .py route) has no user-diagram type: it shows the user reference
    model as a second class diagram and drops the readers. The editor's JSON format has one
    ("UserDiagram": an object diagram over the user reference model), so this file carries every model."""
    import uuid
    from datetime import datetime, timezone

    from besser.utilities.web_modeling_editor.backend.services.converters.buml_to_json.project_converter import (
        project_to_json,
    )

    data = project_to_json(OUT.read_text(encoding="utf-8"))
    diagrams = data["diagrams"]
    # The user reference model came in as a class diagram: the user diagram brings it in itself.
    classes = diagrams.get("ClassDiagram", [])
    is_reference = [any((e or {}).get("name") == "Personal_Information"
                        for e in ((d.get("model") or {}).get("elements") or {}).values()) for d in classes]
    reference_json = next((d["model"] for d, ref in zip(classes, is_reference) if ref), None)
    diagrams["ClassDiagram"] = [d for d, ref in zip(classes, is_reference) if not ref]
    if diagrams["ClassDiagram"]:
        diagrams["ClassDiagram"][0]["title"] = "Trade data (from Eurostat SDMX)"

    readers_model = next(m for m in project.models if getattr(m, "name", "") == "readers")
    user_json = user_diagram_json(readers_model, reference_json or {})
    diagrams["UserDiagram"] = [{"id": str(uuid.uuid4()), "title": "Readers (six personas)", "model": user_json,
                                "lastUpdate": datetime.now(timezone.utc).isoformat()}]
    data["name"] = project.name
    data["description"] = project.metadata.description if project.metadata else ""
    data["owner"] = project.owner
    return data


def user_diagram_json(model, reference_json: dict) -> dict:
    """The readers as the editor's user diagram, built from the model: every object with its attribute
    values, every link with its association, one column per reader.

    BESSER 8.1.0's B-UML-to-JSON object converter is not used: it only finds objects written
    ``Class("x").attributes(...).build()`` (objects without attributes, such as each reader's User,
    Competence and Preference, are dropped) and links written ``a.role = b`` (none of ours)."""
    import uuid
    from personas_user_model import PERSONAS

    ref_elements = reference_json.get("elements") or {}
    class_ids, class_attrs = {}, {}
    for cid, e in ref_elements.items():
        if e.get("type") in ("Class", "AbstractClass"):
            class_ids[e["name"]] = cid
            attrs = {}
            for aid in e.get("attributes", []):
                a = ref_elements.get(aid) or {}
                name = (a.get("name") or "").split(":")[0].strip().lstrip("+-#~")
                attrs[name] = {"id": aid, "type": a.get("attributeType") or "str"}
            class_attrs[e["name"]] = attrs
    assoc_ids = {r.get("name"): i for i, r in (reference_json.get("relationships") or {}).items() if r.get("name")}

    def attrs_of(cls):  # the class's attributes and its parents' (Generalization)
        out = {}
        for parent in getattr(cls, "all_parents", lambda: set())() or set():
            out.update(class_attrs.get(parent.name, {}))
        out.update(class_attrs.get(cls.name, {}))
        return out

    def reader_of(name):
        return next((i for i, p in enumerate(PERSONAS) if name.startswith(p["id"] + "_")), len(PERSONAS))

    elements, relationships, ids = {}, {}, {}
    rows = {}
    for obj in sorted(model.objects, key=lambda o: (reader_of(o.name), o.name.count("_"), o.name)):
        col = reader_of(obj.name)
        y = rows.get(col, 0)
        oid = str(uuid.uuid4())
        ids[obj.name] = oid
        known = attrs_of(obj.classifier)
        attr_ids = []
        for k, slot in enumerate(sorted(obj.slots, key=lambda s: s.attribute.name)):
            value = getattr(slot.value, "value", slot.value)
            value = getattr(value, "name", value)  # an enumeration literal shows its name
            aid = str(uuid.uuid4())
            info = known.get(slot.attribute.name, {})
            elements[aid] = {"id": aid, "name": f"{slot.attribute.name} = {value}", "type": "ObjectAttribute",
                             "owner": oid, "bounds": {"x": col * 320 + 0.5, "y": y + 30 + k * 30 - 0.5,
                                                      "width": 259, "height": 30},
                             "attributeId": info.get("id"), "attributeType": info.get("type", "str")}
            attr_ids.append(aid)
        height = max(70, 40 + len(attr_ids) * 30)
        elements[oid] = {"id": oid, "name": obj.name, "type": "ObjectName", "owner": None,
                         "bounds": {"x": col * 320, "y": y, "width": 260, "height": height},
                         "attributes": attr_ids, "methods": [], "classId": class_ids.get(obj.classifier.name)}
        rows[col] = y + height + 40

    for link in model.links:
        ends = [c for c in link.connections if c.object.name in ids]
        if len(ends) != 2:
            continue
        rid = str(uuid.uuid4())
        name = link.association.name if link.association else ""
        relationships[rid] = {
            "id": rid, "name": name, "type": "ObjectLink", "owner": None,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}, "path": [],
            "source": {"direction": "Down", "element": ids[ends[0].object.name]},
            "target": {"direction": "Up", "element": ids[ends[1].object.name]},
            "isManuallyLayouted": False, "associationId": assoc_ids.get(name),
        }
    return {"version": "3.0.0", "type": "UserDiagram", "size": {"width": 320 * len(PERSONAS) + 200, "height":
            max(rows.values() or [800]) + 200}, "interactive": {"elements": {}, "relationships": {}},
            "elements": elements, "relationships": relationships, "assessments": {},
            "referenceDiagramData": reference_json}


def check_json(data: dict) -> dict[str, str]:
    """Read the JSON back with the editor's own JSON-to-B-UML converter (what it does on export or
    generation): every model must come out, the readers as one user object model."""
    from besser.utilities.web_modeling_editor.backend.models.project import ProjectInput
    from besser.utilities.web_modeling_editor.backend.services.converters.json_to_buml.project_converter import (
        json_to_buml_project,
    )
    project = json_to_buml_project(ProjectInput(**data))
    out = {}
    for m in project.models:
        kind = type(m).__name__
        size = len(getattr(m, "objects", None) or getattr(m, "types", None) or getattr(m, "modules", None)
                   or getattr(m, "layers", None) or [])
        out[f"{kind} {getattr(m, 'name', '')}"] = size
    return out


def check_import(path: Path) -> dict[str, int]:
    """Read the file back with the editor's importer; the number of diagrams per type."""
    from besser.utilities.web_modeling_editor.backend.services.converters.buml_to_json.project_converter import (
        project_to_json,
    )
    project = project_to_json(path.read_text(encoding="utf-8"))
    counts = {}
    for kind, diagrams in (project.get("diagrams") or {}).items():
        n = len(diagrams) if isinstance(diagrams, list) else (1 if diagrams else 0)
        if n:
            counts[kind] = n
    return counts


def main() -> int:
    project = build_project()
    project_to_code(project, str(OUT))
    print(f"wrote {OUT.relative_to(HERE.parent)} ({len(project.models)} models)")
    try:
        counts = check_import(OUT)
    except ImportError as exc:  # the editor's backend needs extras (see BESSER's backend requirements)
        print(f"not checked with the editor's importer ({exc})")
        return 0
    print("the editor's importer reads:", ", ".join(f"{n} {k}" for k, n in counts.items()))
    data = editor_project_json(project)
    OUT_JSON.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT_JSON.relative_to(HERE.parent)}")
    for model, size in check_json(data).items():
        print(f"  the editor rebuilds {model} ({size} elements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
