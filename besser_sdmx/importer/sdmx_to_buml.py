# SPDX-License-Identifier: MIT
"""SDMX data structure definition -> BESSER B-UML domain model.

Mapping rules
-------------
* The dataflow becomes an ``Observation`` class holding the time period, the
  observed value and any SDMX attributes (e.g. OBS_FLAG).
* A coded dimension with a small, flat codelist becomes an ``Enumeration`` and an
  attribute of ``Observation``.
* A coded dimension with a large or hierarchical codelist becomes a class
  (``code`` as identifier, ``label``) linked to ``Observation`` by a 1..* association.
* A hierarchical codelist (SDMX parent references, or code prefixes such as
  HS2 -> HS4 -> HS6 -> CN8) gets a ``parent``/``children`` self-association and a
  ``level`` attribute, so the generated app can drill down.

The function also returns an ``ImportMapping`` that tells the loader how SDMX
columns and codes map onto the generated database.
"""
from __future__ import annotations

import keyword
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Iterable, Optional

from besser.BUML.metamodel.structural import (
    BinaryAssociation, Class, DomainModel, Enumeration, EnumerationLiteral, FloatType,
    IntegerType, Metadata, Multiplicity, Property, StringType,
)

from .structure import Codelist, Component, DataStructure

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _words(text: str) -> list[str]:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)  # split camelCase
    return [w for w in re.split(r"[^A-Za-z0-9]+", text) if w]


def pascal(text: str) -> str:
    name = "".join(w[:1].upper() + w[1:].lower() if w.isupper() or w.islower() else w[:1].upper() + w[1:]
                   for w in _words(text)) or "X"
    return name if name[0].isalpha() else f"X{name}"


def snake(text: str) -> str:
    name = "_".join(w.lower() for w in _words(text)) or "x"
    if not (name[0].isalpha() or name[0] == "_") or keyword.iskeyword(name):
        name = f"x_{name}"
    return name


def literal_name(code: str, label: str, taken: set[str]) -> str:
    """A valid, unique enum literal for an SDMX code: the code itself, else its label."""
    candidates = []
    if _IDENT.match(code) and not keyword.iskeyword(code):
        candidates.append(code)
    if label:
        candidates.append("_".join(w.upper() for w in _words(label))[:60])
    candidates.append("C_" + re.sub(r"[^A-Za-z0-9_]", "_", code))
    for cand in candidates:
        if cand and _IDENT.match(cand) and cand not in taken:
            return cand
    base, i = candidates[-1], 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def infer_prefix_parents(codes: Iterable[str]) -> dict[str, Optional[str]]:
    """Parent of each code = the longest other code that is a strict prefix of it.

    Works for nested product nomenclatures (HS2 '84' > HS4 '8471' > HS6 '847130' >
    CN8 '84713000'). Codes without a prefix parent are attached to 'TOTAL' when present.
    """
    codes = list(codes)
    present = set(codes)
    parents: dict[str, Optional[str]] = {}
    for c in codes:
        parent = None
        if c.isdigit():  # only numeric nomenclature codes nest by prefix
            for n in range(len(c) - 1, 0, -1):
                if c[:n] in present:
                    parent = c[:n]
                    break
        if parent is None and c.isdigit() and "TOTAL" in present:
            parent = "TOTAL"
        parents[c] = parent
    return parents


def _looks_prefix_hierarchical(cl: Codelist) -> bool:  # kept for compatibility; see roles.py
    digit_codes = [c for c in cl.codes if c.isdigit()]
    if len(digit_codes) < 50:
        return False
    # A run of plain integers (ranks 1..N) nests by prefix too, but is not a nomenclature.
    if not any(c.startswith("0") for c in digit_codes):
        ints = {int(c) for c in digit_codes}
        if len(ints) > 0.9 * (max(ints) - min(ints) + 1):
            return False
    parents = infer_prefix_parents(cl.codes)
    with_parent = sum(1 for c in digit_codes if parents.get(c) not in (None, "TOTAL"))
    return with_parent / len(digit_codes) > 0.5


@dataclass
class DimensionMapping:
    id: str                       # SDMX dimension id, e.g. "partner"
    kind: str                     # "enum" | "class" | "string"
    attr: str                     # Observation attribute or association end name
    type_name: Optional[str] = None  # Enumeration or Class name
    literals: dict[str, str] = field(default_factory=dict)  # code -> enum literal (enum only)
    hierarchy: Optional[str] = None  # "sdmx" | "prefix" | "hcl" (class only)
    role: str = "breakdown"       # "frequency" | "geo" | "hierarchy" | "unit" | "breakdown" (see roles.py)
    label: str = ""               # human-readable name of the dimension (SDMX concept)
    total_code: Optional[str] = None    # code holding the total of the others (WORLD, TOTAL, _T...)
    default_code: Optional[str] = None  # code used when the dimension is not analysed
    code_labels: dict[str, str] = field(default_factory=dict)  # code -> codelist name (enum only)


@dataclass
class ImportMapping:
    dataflow: str
    source_agency: str
    observation_class: str
    time_attr: Optional[str]
    value_attr: str
    dimensions: list[DimensionMapping]
    attributes: dict[str, str]    # SDMX attribute id -> Observation attribute
    title: str = ""               # dataflow name
    source: str = ""              # SOURCES key, e.g. "eurostat-comext"

    def to_dict(self) -> dict:
        return asdict(self)

    def dimension(self, dim_id: str) -> DimensionMapping:
        return next(d for d in self.dimensions if d.id == dim_id)

    @classmethod
    def from_dict(cls, d: dict) -> "ImportMapping":
        """Build from JSON; files written before roles existed load with default roles."""
        known = {f.name for f in fields(DimensionMapping)}
        d = {k: v for k, v in d.items() if k in {f.name for f in fields(cls)}}
        d["dimensions"] = [DimensionMapping(**{k: v for k, v in x.items() if k in known}) for x in d["dimensions"]]
        return cls(**d)


def sdmx_to_buml(
    dsd: DataStructure,
    model_name: Optional[str] = None,
    enum_max: int = 50,
    prefix_hierarchy: Optional[Iterable[str]] = None,
    observation_class: str = "Observation",
    class_names: Optional[dict[str, str]] = None,
    source: str = "",
) -> tuple[DomainModel, ImportMapping]:
    """Build a validated B-UML DomainModel from a parsed SDMX data structure.

    Args:
        dsd: parsed structure (see :func:`besser_sdmx.importer.structure.parse_structure`).
        model_name: name of the domain model; defaults to the dataflow id.
        enum_max: flat codelists with at most this many codes become enumerations.
        prefix_hierarchy: dimension ids whose codes nest by prefix (e.g. ``["product"]``).
            ``None`` auto-detects numeric nomenclatures.
        observation_class: name of the fact class.
        class_names: optional names for generated classes, keyed by dimension id or
            codelist id (e.g. ``{"CXT_FREE_ISO": "Country"}``). Dimensions sharing a
            codelist share one class.
    """
    from .roles import detect_roles

    class_names = class_names or {}
    roles = detect_roles(dsd, prefix_hierarchy)
    codelist_users: dict[str, int] = {}
    for d in dsd.dimensions:
        if d.codelist_ref:
            codelist_users[d.codelist_ref] = codelist_users.get(d.codelist_ref, 0) + 1
    shared_classes: dict[str, Class] = {}
    used_names: set[str] = {observation_class}
    obs_attrs: set[Property] = set()
    types: set = set()
    associations: set[BinaryAssociation] = set()
    dim_maps: list[DimensionMapping] = []

    def unique(name: str) -> str:
        base, i = name, 2
        while name in used_names:
            name, i = f"{base}{i}", i + 1
        used_names.add(name)
        return name

    obs_attr_names: set[str] = set()

    def attr_name(text: str) -> str:
        base = snake(text)
        name, i = base, 2
        while name in obs_attr_names or name in ("id",):
            name, i = f"{base}_{i}", i + 1
        obs_attr_names.add(name)
        return name

    time_attr = None
    if dsd.time_dimension is not None:
        time_attr = attr_name("time_period")
        obs_attrs.add(Property(name=time_attr, type=StringType))
    value_attr = attr_name("obs_value")
    obs_attrs.add(Property(name=value_attr, type=FloatType, is_optional=True))

    obs = Class(name=observation_class)
    for dim in dsd.dimensions:
        cl = dsd.codelist_for(dim)
        attr = attr_name(dim.id)
        r = roles[dim.id]
        role_fields = dict(role=r.role, label=r.label, total_code=r.total_code, default_code=r.default_code)
        if cl is None or not cl.codes:
            obs_attrs.add(Property(name=attr, type=StringType))
            dim_maps.append(DimensionMapping(id=dim.id, kind="string", attr=attr, **role_fields))
            continue

        hierarchy = r.hierarchy

        if hierarchy is None and len(cl.codes) <= enum_max:
            taken: set[str] = set()
            literals: dict[str, str] = {}
            for code in cl.codes.values():
                lit = literal_name(code.id, code.name, taken)
                taken.add(lit)
                literals[code.id] = lit
            enum = Enumeration(name=unique(pascal(dim.id)),
                               literals={EnumerationLiteral(name=lit) for lit in literals.values()})
            types.add(enum)
            obs_attrs.add(Property(name=attr, type=enum))
            dim_maps.append(DimensionMapping(id=dim.id, kind="enum", attr=attr, type_name=enum.name, literals=literals,
                                             code_labels={c.id: c.name for c in cl.codes.values() if c.name},
                                             **role_fields))
            continue

        shared = codelist_users.get(cl.id, 0) > 1
        dim_cls = shared_classes.get(cl.id)
        new_class = dim_cls is None
        if new_class:
            cls_attrs = {
                Property(name="code", type=StringType, is_id=True),
                Property(name="label", type=StringType),
            }
            if hierarchy:
                cls_attrs.add(Property(name="level", type=IntegerType))
            default = re.sub(r"^(CL|CXT)_", "", cl.id) if shared else dim.id
            name = class_names.get(dim.id) or class_names.get(cl.id) or pascal(default)
            dim_cls = Class(name=unique(name), attributes=cls_attrs, metadata=Metadata(
                description=_describe(dim, r, cl.id), uri=f"sdmx:codelist:{cl.agency}:{cl.id}({cl.version})"))
            shared_classes[cl.id] = dim_cls
            types.add(dim_cls)
        associations.add(BinaryAssociation(
            name=f"{snake(observation_class)}_{attr}",
            ends={
                Property(name=attr, type=dim_cls, multiplicity=Multiplicity(1, 1)),
                Property(name=f"observations_as_{attr}" if shared else "observations", type=obs,
                         multiplicity=Multiplicity(0, "*")),
            },
        ))
        if hierarchy and new_class:
            associations.add(BinaryAssociation(
                name=f"{snake(dim_cls.name)}_hierarchy",
                ends={
                    Property(name="parent", type=dim_cls, multiplicity=Multiplicity(0, 1)),
                    Property(name="children", type=dim_cls, multiplicity=Multiplicity(0, "*")),
                },
            ))
        dim_maps.append(DimensionMapping(id=dim.id, kind="class", attr=attr, type_name=dim_cls.name, hierarchy=hierarchy,
                                         **role_fields))

    # A class shared by several dimensions (reporter and partner -> Country) documents all of them.
    for cl_id, cls in shared_classes.items():
        users = [d for d in dsd.dimensions if d.codelist_ref == cl_id]
        if len(users) > 1 and cls.metadata is not None:
            cls.metadata.description = "; ".join(_describe(d, roles[d.id]) for d in users) + f"; codelist={cl_id}"

    attr_map: dict[str, str] = {}
    for a in dsd.attributes:
        name = attr_name(a.id)
        attr_map[a.id] = name
        obs_attrs.add(Property(name=name, type=StringType, is_optional=True))

    obs.attributes = obs_attrs
    obs.metadata = Metadata(
        description=(f"One observation of SDMX dataflow {dsd.agency}:{dsd.dataflow_id} ({dsd.dataflow_name}). "
                     "Dimension roles: " + "; ".join(_describe(d, roles[d.id]) for d in dsd.dimensions)),
        uri=f"sdmx:dataflow:{dsd.agency}:{dsd.dataflow_id}",
    )
    types.add(obs)

    model = DomainModel(
        name=model_name or pascal(dsd.dataflow_id),
        types=types,
        associations=associations,
    )
    model.validate(raise_exception=True)

    mapping = ImportMapping(
        dataflow=dsd.dataflow_id,
        source_agency=dsd.agency,
        observation_class=observation_class,
        time_attr=time_attr,
        value_attr=value_attr,
        dimensions=dim_maps,
        attributes=attr_map,
        title=dsd.dataflow_name,
        source=source,
    )
    return model, mapping


def _describe(dim, r, codelist: Optional[str] = None) -> str:
    """Role summary stored in the model's Metadata, so the B-UML model documents itself."""
    parts = [f"{dim.id} ({r.label}): role={r.role}"]
    if r.hierarchy:
        parts.append(f"hierarchy={r.hierarchy}")
    if r.total_code:
        parts.append(f"total={r.total_code}")
    if r.default_code:
        parts.append(f"default={r.default_code}")
    if codelist:
        parts.append(f"codelist={codelist}")
    return ", ".join(parts)


def code_rows(dsd: DataStructure, dim_map: DimensionMapping) -> list[dict]:
    """Rows (code, label, level, parent) for a class-mapped dimension."""
    dim = next(d for d in dsd.dimensions if d.id == dim_map.id)
    cl = dsd.codelist_for(dim)
    if dim_map.hierarchy == "prefix":
        parents = infer_prefix_parents(cl.codes)
    elif dim_map.hierarchy == "hcl":
        parents = dict(dsd.hierarchies.get(dim.id, {}))
    elif dim_map.hierarchy == "sdmx":
        parents = {c.id: c.parent for c in cl.codes.values()}
    else:
        parents = {}

    def level(code: str) -> int:
        depth, seen = 0, set()
        p = parents.get(code)
        while p and p not in seen:
            seen.add(p)
            depth += 1
            p = parents.get(p)
        return depth

    rows = []
    for c in cl.codes.values():
        row = {"code": c.id, "label": c.name}
        if dim_map.hierarchy:
            row["level"] = level(c.id)
            row["parent"] = parents.get(c.id)
        rows.append(row)
    return rows
