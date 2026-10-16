# SPDX-License-Identifier: MIT
"""model/build_project.py: every BESSER model of the project in one project file the web editor imports,
and the round trip through the editor's own converters keeps every model (the readers included)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("besser")


@pytest.fixture(scope="module")
def built():
    sys.path.insert(0, str(ROOT / "model"))
    try:
        try:
            import build_project as B
            from besser.utilities.web_modeling_editor.backend.models.project import ProjectInput  # noqa: F401
            project = B.build_project()   # imports the editor's converters (deep_translator, ...)
        except ImportError as e:  # the editor's backend extras are not installed (CI)
            pytest.skip(f"BESSER web-editor backend dependencies not installed: {e}")
        return B, project
    finally:
        sys.path.remove(str(ROOT / "model"))


def test_the_project_holds_every_model(built):
    _, project = built
    kinds = sorted(type(m).__name__ for m in project.models)
    assert kinds == ["DomainModel", "GUIModel", "NN", "NN", "ObjectModel"]


def test_the_editor_rebuilds_every_model_from_the_json(built, tmp_path, monkeypatch):
    B, project = built
    monkeypatch.setattr(B, "OUT", tmp_path / "project.py")
    from besser.utilities.buml_code_builder.project_builder import project_to_code
    project_to_code(project, str(B.OUT))
    data = B.editor_project_json(project)
    assert {"ClassDiagram", "GUINoCodeDiagram", "NNDiagram", "UserDiagram"} <= set(data["diagrams"])

    from besser.utilities.web_modeling_editor.backend.models.project import ProjectInput
    from besser.utilities.web_modeling_editor.backend.services.converters.json_to_buml.project_converter import (
        json_to_buml_project,
    )
    back = json_to_buml_project(ProjectInput(**json.loads(json.dumps(data))))
    readers = next(m for m in project.models if type(m).__name__ == "ObjectModel")
    again = next(m for m in back.models if type(m).__name__ == "ObjectModel")
    assert len(again.objects) == len(readers.objects) and len(again.links) == len(readers.links)
    assert sum(len(o.slots) for o in again.objects) == sum(len(o.slots) for o in readers.objects)
    assert again.validate(raise_exception=False)["success"]
    assert sorted(type(m).__name__ for m in back.models) == ["DomainModel", "GUIModel", "NN", "NN", "ObjectModel"]
