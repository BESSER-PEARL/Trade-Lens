# SPDX-License-Identifier: MIT
"""Role detection, role-driven config, default GUI and derived data slices (offline)."""
import json


from besser_sdmx.generators.insights import InsightsGenerator
from besser_sdmx.importer import ImportMapping, sdmx_to_buml
from besser_sdmx.importer.roles import detect_roles, find_total
from besser_sdmx.importer.structure import Codelist, Code, parse_hierarchy, parse_structure


def _dsd(structure_xml):
    dsd = parse_structure(structure_xml, "DF_TEST")
    dsd.constraints = {"freq": ["A", "M"], "flow": ["1", "2"], "rank": ["1"],
                       "TIME_PERIOD": ["2023", "2024", "2025", "2026-01"]}
    return dsd


def test_roles(structure_xml):
    roles = detect_roles(_dsd(structure_xml))
    assert {k: r.role for k, r in roles.items()} == {
        "freq": "frequency", "reporter": "geo", "partner": "geo", "product": "hierarchy",
        "flow": "unit", "rank": "unit"}  # rank has a single code in the data: a constant
    assert roles["partner"].total_code == "WORLD" and roles["partner"].default_code == "WORLD"
    assert roles["product"].total_code == "TOTAL" and roles["product"].hierarchy == "prefix"
    assert roles["flow"].default_code == "2"      # the headline: exports
    assert roles["freq"].default_code == "A"
    assert roles["rank"].default_code == "1"


def test_total_detection():
    cl = Codelist("CL", "X", "1", "CL", {c: Code(c, n) for c, n in
                                          [("_T", "Total"), ("F", "Female"), ("M", "Male")]})
    assert find_total(cl, ["F", "M", "_T"]) == "_T"
    cl = Codelist("CL", "X", "1", "CL", {c: Code(c, n) for c, n in [("W", "World"), ("DE", "Germany")]})
    assert find_total(cl, ["DE", "W"]) == "W"
    assert find_total(None, ["A", "B"], parents={"R": None, "A": "R", "B": "R"}) is None  # root not used
    assert find_total(None, ["R", "A", "B"], parents={"R": None, "A": "R", "B": "R"}) == "R"


def test_mapping_roles_roundtrip(structure_xml):
    model, mapping = sdmx_to_buml(_dsd(structure_xml), model_name="T", source="test")
    restored = ImportMapping.from_dict(json.loads(json.dumps(mapping.to_dict())))
    assert restored.dimension("partner").role == "geo" and restored.source == "test"
    # Older mapping files (no roles) still load.
    legacy = mapping.to_dict()
    for d in legacy["dimensions"]:
        for k in ("role", "label", "total_code", "default_code"):
            d.pop(k)
    legacy.pop("title"), legacy.pop("source")
    assert ImportMapping.from_dict(legacy).dimension("partner").role == "breakdown"
    # Roles are documented in the model itself.
    obs = next(t for t in model.types if getattr(t, "name", "") == "Observation")
    assert "partner (Partner): role=geo" in obs.metadata.description


def test_insights_config_from_roles(structure_xml, tmp_path):
    model, mapping = sdmx_to_buml(_dsd(structure_xml), model_name="T")
    cfg = InsightsGenerator(model, mapping, str(tmp_path), focus={"reporter": "LU"}).config()
    dims = {d["id"]: d for d in cfg["dimensions"]}
    assert cfg["defaults"]["partner"] == "WORLD" and cfg["defaults"]["product"] == "TOTAL"
    assert cfg["comparison"] == {"dimension": "reporter", "peers": "auto"}
    assert dims["partner"]["member_pattern"] and dims["product"]["parent_column"] == "parent_id"
    assert cfg["frequency_dimension"] == "freq"


def test_hierarchical_codelist():
    xml = b'''<m:Structure xmlns:m="m" xmlns:s="s"><s:Hierarchy id="H">
      <s:HierarchicalCode id="_T"><s:CodeID><Ref id="_T"/></s:CodeID>
        <s:HierarchicalCode id="EU"><s:CodeID><Ref id="EU"/></s:CodeID>
          <s:HierarchicalCode id="DE"><s:CodeID><Ref id="DE"/></s:CodeID></s:HierarchicalCode>
        </s:HierarchicalCode></s:HierarchicalCode></s:Hierarchy></m:Structure>'''
    assert parse_hierarchy(xml, "H") == {"_T": None, "EU": "_T", "DE": "EU"}
