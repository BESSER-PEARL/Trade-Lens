# SPDX-License-Identifier: MIT
"""Tests for the SDMX importer, loader and generator glue (offline: no network)."""
import sqlite3
import sys

import pandas as pd
import pytest
from besser.generators.sql_alchemy import SQLAlchemyGenerator

from besser_sdmx.importer import infer_prefix_parents, literal_name, sdmx_to_buml, snake, pascal
from besser_sdmx.loader import Loader, Slice, build_key, import_module
from besser_sdmx.patches import fix_self_relationships
from besser_sdmx.importer.structure import parse_structure


def test_parse_reads_only_real_dimensions(structure_xml):
    dsd = parse_structure(structure_xml, "DF_TEST")
    assert [d.id for d in dsd.dimensions] == ["freq", "reporter", "partner", "product", "flow", "rank"]
    assert dsd.time_dimension.id == "TIME_PERIOD"
    assert [a.id for a in dsd.attributes] == ["OBS_FLAG"]
    assert dsd.measure.id == "OBS_VALUE"
    assert dsd.dataflow_name == "Test trade"
    assert dsd.codelist_for(dsd.dimensions[2]).codes["DE"].name == "Germany"


def test_mapping_rules(structure_xml):
    dsd = parse_structure(structure_xml, "DF_TEST")
    model, mapping = sdmx_to_buml(dsd, model_name="TestTrade", class_names={"CL_AREA": "Country"})
    kinds = {d.id: (d.kind, d.type_name, d.hierarchy) for d in mapping.dimensions}
    assert kinds["freq"] == ("enum", "Freq", None)
    assert kinds["flow"][0] == "enum"
    assert kinds["reporter"] == ("class", "Country", None)
    assert kinds["partner"] == ("class", "Country", None)       # shared codelist -> one class
    assert kinds["product"] == ("class", "Product", "prefix")   # HS-like nomenclature
    assert kinds["rank"] == ("class", "Rank", None)             # 1..60 is a ranking, not a hierarchy
    flow = next(d for d in mapping.dimensions if d.id == "flow")
    assert flow.literals == {"1": "IMPORT", "2": "EXPORT"}       # numeric codes -> label literals

    classes = {t.name: t for t in model.types if type(t).__name__ == "Class"}
    assert set(classes) == {"Observation", "Country", "Product", "Rank"}
    obs_attrs = {a.name: a for a in classes["Observation"].attributes}
    assert obs_attrs["obs_value"].is_optional and obs_attrs["obs_flag"].is_optional
    assert {a.name for a in classes["Product"].attributes} == {"code", "label", "level"}
    ends = {(e.name, e.type.name) for a in model.associations for e in a.ends}
    assert ("parent", "Product") in ends and ("children", "Product") in ends
    assert ("observations_as_reporter", "Observation") in ends and ("observations_as_partner", "Observation") in ends


def test_prefix_hierarchy():
    parents = infer_prefix_parents(["TOTAL", "TO", "84", "8471", "847130", "84713000", "00", "0090"])
    assert parents == {"TOTAL": None, "TO": None, "84": "TOTAL", "8471": "84", "847130": "8471",
                       "84713000": "847130", "00": "TOTAL", "0090": "00"}


def test_names():
    assert pascal("REF_AREA") == "RefArea" and pascal("c_part") == "CPart"
    assert snake("LuxTrade") == "lux_trade" and snake("TIME_PERIOD") == "time_period" and snake("class") == "x_class"
    assert literal_name("1", "IMPORT", set()) == "IMPORT"
    assert literal_name("A", "Annual", set()) == "A"
    assert literal_name("1", "IMPORT", {"IMPORT"}) == "C_1"


def test_self_relationship_patch():
    src = ('class Product(Base):\n    __tablename__ = "product"\n'
           '    code: Mapped_[str] = mapped_column(String_(100), primary_key=True)\n'
           '    parent_id: Mapped_[str] = mapped_column(ForeignKey_("product.code"), nullable=True)\n\n'
           'Product.parent: Mapped_["Product"] = relationship("Product", back_populates="children", uselist=False, '
           'foreign_keys=[Product.parent_id])\n')
    out = fix_self_relationships(src)
    assert "remote_side=[Product.code]" in out
    assert fix_self_relationships(out) == out  # idempotent


def test_build_key(structure_xml):
    dsd = parse_structure(structure_xml, "DF_TEST")
    assert build_key(dsd, {"freq": "A", "reporter": "LU", "flow": ["1", "2"]}) == "A.LU...1+2."
    assert build_key(dsd, "all") == "all"


class FakeClient:
    """Answers data queries with a fixed SDMX-CSV frame."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def data(self, flow, key, start=None, end=None):
        return self.frame


def test_load_into_besser_generated_schema(structure_xml, tmp_path):
    dsd = parse_structure(structure_xml, "DF_TEST")
    model, mapping = sdmx_to_buml(dsd, model_name="TestTrade", class_names={"CL_AREA": "Country"})
    SQLAlchemyGenerator(model=model, output_dir=str(tmp_path / "orm")).generate(dbms="sqlite")
    from besser_sdmx.patches import patch_generated_backend
    patch_generated_backend(tmp_path / "orm")
    orm = import_module(tmp_path / "orm" / "sql_alchemy.py", name="test_orm")

    frame = pd.DataFrame([
        {"DATAFLOW": "TEST:DF_TEST", "freq": "A", "reporter": "LU", "partner": "DE", "product": "8401",
         "flow": "2", "rank": "1", "TIME_PERIOD": "2025", "OBS_VALUE": "100.5", "OBS_FLAG": ""},
        {"DATAFLOW": "TEST:DF_TEST", "freq": "A", "reporter": "LU", "partner": "ZZ", "product": "8401",
         "flow": "1", "rank": "2", "TIME_PERIOD": "2025", "OBS_VALUE": "", "OBS_FLAG": "c"},
    ])
    db = tmp_path / "t.db"
    loader = Loader(orm, f"sqlite:///{db.as_posix()}", dsd, mapping)
    loader.reset()
    loader.load_codelists()
    # The same rows twice (overlapping slices) are stored once.
    n = loader.load_slices(FakeClient(frame), [Slice("all"), Slice("all")])
    loader.index()
    assert n == 2

    con = sqlite3.connect(db)
    rows = con.execute("SELECT partner_id, product_id, flow, obs_value, obs_flag FROM observation ORDER BY id").fetchall()
    assert rows == [("DE", "8401", "EXPORT", 100.5, None), ("ZZ", "8401", "IMPORT", None, "c")]
    # Unknown code ZZ was added to the codelist table; the product hierarchy is navigable.
    assert con.execute("SELECT label FROM country WHERE code='ZZ'").fetchone() == ("ZZ",)
    assert con.execute("SELECT parent_id, level FROM product WHERE code='840101'").fetchone() == ("8401", 3)
    sys.modules.pop("test_orm", None)


def test_append_replaces_rows_already_there_never_doubles_them(structure_xml, tmp_path):
    """load --append into a database that already holds some of the rows (an earlier load, or a slice the
    analytics API fetched live): those rows are replaced, so no value is counted twice."""
    dsd = parse_structure(structure_xml, "DF_TEST")
    model, mapping = sdmx_to_buml(dsd, model_name="TestTrade", class_names={"CL_AREA": "Country"})
    SQLAlchemyGenerator(model=model, output_dir=str(tmp_path / "orm")).generate(dbms="sqlite")
    from besser_sdmx.patches import patch_generated_backend
    patch_generated_backend(tmp_path / "orm")
    orm = import_module(tmp_path / "orm" / "sql_alchemy.py", name="test_orm_append")

    def row(partner, value):
        return {"DATAFLOW": "TEST:DF_TEST", "freq": "A", "reporter": "LU", "partner": partner, "product": "8401",
                "flow": "2", "rank": "1", "TIME_PERIOD": "2025", "OBS_VALUE": str(value), "OBS_FLAG": ""}

    db = tmp_path / "a.db"
    first = Loader(orm, f"sqlite:///{db.as_posix()}", dsd, mapping)
    first.reset()
    first.load_codelists()
    first.load_slices(FakeClient(pd.DataFrame([row("DE", 100)])), [Slice("all")])
    first.index()
    again = Loader(orm, f"sqlite:///{db.as_posix()}", dsd, mapping)   # a new run: --append
    again.load_known_codes()
    n = again.load_slices(FakeClient(pd.DataFrame([row("DE", 101), row("FR", 50)])), [Slice("all")], replace=True)
    assert n == 2
    con = sqlite3.connect(db)
    assert con.execute("SELECT partner_id, obs_value FROM observation ORDER BY partner_id").fetchall() == \
        [("DE", 101.0), ("FR", 50.0)]
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'ix_observation_slice'").fetchone() == (1,)
    con.close()
    sys.modules.pop("test_orm_append", None)
