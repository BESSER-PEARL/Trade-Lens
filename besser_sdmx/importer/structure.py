# SPDX-License-Identifier: MIT
"""Parse an SDMX-ML 2.1 structure message into plain Python objects.

Only the parts needed to build a B-UML model are read: the dataflow, its data
structure definition (dimensions, time dimension, attributes, primary measure),
concepts (for human-readable names) and codelists (codes, names, parents).
Tags are matched by local name so the parser does not depend on the namespace
prefixes a given SDMX provider uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import re
import xml.etree.ElementTree as ET

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass
class Code:
    id: str
    name: str
    parent: Optional[str] = None


@dataclass
class Codelist:
    id: str
    agency: str
    version: str
    name: str
    codes: dict[str, Code] = field(default_factory=dict)

    @property
    def has_parents(self) -> bool:
        return any(c.parent for c in self.codes.values())


@dataclass
class Component:
    """A dimension, the time dimension, an attribute or the primary measure."""
    id: str
    kind: str  # "dimension" | "time" | "attribute" | "measure"
    position: int = 0
    concept: Optional[str] = None
    codelist_ref: Optional[str] = None  # codelist id
    name: Optional[str] = None


@dataclass
class DataStructure:
    id: str
    agency: str
    version: str
    name: str
    dataflow_id: str
    dataflow_name: str
    dimensions: list[Component]
    time_dimension: Optional[Component]
    attributes: list[Component]
    measure: Optional[Component]
    codelists: dict[str, Codelist]
    # Codes actually present in the data, per dimension (SDMX "Actual" content constraint);
    # falls back to "Allowed" constraints. Empty when the provider sends none.
    constraints: dict[str, list[str]] = field(default_factory=dict)
    # Annotations of the dataflow and the DSD: list of (type, title, text).
    annotations: list[tuple[str, str, str]] = field(default_factory=list)
    # Parent of each code per dimension, from a hierarchical codelist (e.g. LUSTAT HIER_CONTEXT).
    hierarchies: dict[str, dict[str, Optional[str]]] = field(default_factory=dict)

    def codelist_for(self, comp: Component) -> Optional[Codelist]:
        return self.codelists.get(comp.codelist_ref) if comp.codelist_ref else None

    def used_codes(self, comp: Component) -> list[str]:
        """Codes of a dimension that occur in the data (constraint), else every code of its codelist."""
        if comp.id in self.constraints:
            return list(self.constraints[comp.id])
        cl = self.codelist_for(comp)
        return list(cl.codes) if cl else []

    def hier_context(self) -> dict[str, tuple[str, str, str, str]]:
        """``HIER_CONTEXT`` annotations (.Stat Suite): dim -> (agency, hcl id, version, hierarchy id).

        Format of the annotation title: ``C_PART:LU1:HCL_E4103(1.0).H`` (several separated by ``,``).
        """
        out: dict[str, tuple[str, str, str, str]] = {}
        for typ, title, text in self.annotations:
            if typ != "HIER_CONTEXT":
                continue
            for part in (title or text or "").split(","):
                m = re.match(r"\s*([^:]+):([^:]+):([^(]+)\(([^)]+)\)\.(\w+)", part)
                if m:
                    out[m.group(1)] = (m.group(2), m.group(3), m.group(4), m.group(5))
        return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _child(el: ET.Element, name: str) -> Optional[ET.Element]:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _descendants(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el.iter() if _local(c.tag) == name]


def _name(el: ET.Element, lang: str = "en") -> str:
    """Return the element's Name in `lang`, else the first Name, else its id."""
    names = _children(el, "Name")
    for n in names:
        if n.get(XML_LANG, "").lower() == lang:
            return (n.text or "").strip()
    if names:
        return (names[0].text or "").strip()
    return el.get("id", "")


def _ref_id(el: Optional[ET.Element]) -> Optional[str]:
    """Id of the first <Ref> (or URN) below `el`."""
    if el is None:
        return None
    for r in el.iter():
        if _local(r.tag) == "Ref" and r.get("id"):
            return r.get("id")
        if _local(r.tag) == "URN" and r.text:
            # urn:...Codelist=ESTAT:CL_FREQ(1.0) -> CL_FREQ
            return r.text.split("=", 1)[-1].split(":", 1)[-1].split("(", 1)[0]
    return None


def _parse_codelists(root: ET.Element, lang: str) -> dict[str, Codelist]:
    out: dict[str, Codelist] = {}
    for cl in _descendants(root, "Codelist"):
        if not _children(cl, "Code"):
            continue  # a reference, not a definition
        codelist = Codelist(
            id=cl.get("id", ""),
            agency=cl.get("agencyID", ""),
            version=cl.get("version", ""),
            name=_name(cl, lang),
        )
        for c in _children(cl, "Code"):
            code = Code(id=c.get("id", ""), name=_name(c, lang), parent=_ref_id(_child(c, "Parent")))
            codelist.codes[code.id] = code
        out[codelist.id] = codelist
    return out


def _annotations(el: Optional[ET.Element]) -> list[tuple[str, str, str]]:
    out = []
    if el is None:
        return out
    anns = _child(el, "Annotations")
    if anns is None:
        return out
    for a in _children(anns, "Annotation"):
        typ = (_child(a, "AnnotationType").text or "").strip() if _child(a, "AnnotationType") is not None else ""
        title = (_child(a, "AnnotationTitle").text or "").strip() if _child(a, "AnnotationTitle") is not None else ""
        texts = _children(a, "AnnotationText")
        text = (texts[0].text or "").strip() if texts else ""
        out.append((typ, title, text))
    return out


def _parse_constraints(root: ET.Element, flow_id: Optional[str]) -> dict[str, list[str]]:
    """Codes per dimension from content constraints: "Actual" wins over "Allowed"."""
    by_type: dict[str, dict[str, list[str]]] = {"Actual": {}, "Allowed": {}}
    for cc in _descendants(root, "ContentConstraint"):
        ctype = cc.get("type", "Actual")
        if ctype not in by_type:
            continue
        attach = _child(cc, "ConstraintAttachment")
        if flow_id and attach is not None:
            refs = [r.get("id") for r in attach.iter() if _local(r.tag) == "Ref" and r.get("id")]
            if refs and not any(r.upper() == flow_id.upper() for r in refs) and not any(
                    r.startswith("DSD") for r in refs):
                continue
        for region in _children(cc, "CubeRegion"):
            if region.get("include", "true").lower() != "true":
                continue
            for kv in _children(region, "KeyValue"):
                values = [(v.text or "").strip() for v in _children(kv, "Value") if (v.text or "").strip()]
                if values:
                    bucket = by_type[ctype].setdefault(kv.get("id", ""), [])
                    bucket.extend(v for v in values if v not in bucket)
    merged = dict(by_type["Allowed"])
    merged.update(by_type["Actual"])
    return merged


def parse_constraint(xml: bytes | str, dataflow_id: Optional[str] = None) -> dict[str, list[str]]:
    """Codes per dimension from a standalone content-constraint message."""
    return _parse_constraints(ET.fromstring(xml), dataflow_id)


def parse_hierarchy(xml: bytes | str, hierarchy_id: Optional[str] = None) -> dict[str, Optional[str]]:
    """Parent of each code in an SDMX 2.1 HierarchicalCodelist (one hierarchy)."""
    root = ET.fromstring(xml)
    hierarchies = _descendants(root, "Hierarchy")
    if hierarchy_id:
        hierarchies = [h for h in hierarchies if h.get("id") == hierarchy_id] or hierarchies
    parents: dict[str, Optional[str]] = {}

    def code_of(hc: ET.Element) -> str:
        cid = _ref_id(_child(hc, "CodeID")) or _ref_id(_child(hc, "Code"))
        return cid or hc.get("id", "")

    def walk(hc: ET.Element, parent: Optional[str]) -> None:
        code = code_of(hc)
        parents.setdefault(code, parent)
        for child in _children(hc, "HierarchicalCode"):
            walk(child, code)

    if hierarchies:
        for top in _children(hierarchies[0], "HierarchicalCode"):
            walk(top, None)
    return parents


def _parse_concepts(root: ET.Element, lang: str) -> dict[str, str]:
    return {c.get("id", ""): _name(c, lang) for c in _descendants(root, "Concept")}


def _component(el: ET.Element, kind: str, concepts: dict[str, str]) -> Component:
    concept = _ref_id(_child(el, "ConceptIdentity"))
    rep = _child(el, "LocalRepresentation")
    enum = _child(rep, "Enumeration") if rep is not None else None
    cid = el.get("id") or concept or ""
    return Component(
        id=cid,
        kind=kind,
        position=int(el.get("position", "0") or 0),
        concept=concept,
        codelist_ref=_ref_id(enum),
        name=concepts.get(concept or cid) or concepts.get(cid) or cid,
    )


def parse_structure(xml: bytes | str, dataflow_id: Optional[str] = None, lang: str = "en") -> DataStructure:
    """Parse an SDMX-ML 2.1 structure message.

    `xml` should contain the data structure definition and, ideally, its codelists
    and concepts (request it with ``references=children`` or ``references=descendants``).
    """
    root = ET.fromstring(xml)
    concepts = _parse_concepts(root, lang)
    codelists = _parse_codelists(root, lang)

    dsds = _descendants(root, "DataStructure")
    dsds = [d for d in dsds if _child(d, "DataStructureComponents") is not None]
    if not dsds:
        raise ValueError("No DataStructure definition found in the structure message")

    flows = [f for f in _descendants(root, "Dataflow") if f.get("id")]
    flow = None
    if dataflow_id:
        flow = next((f for f in flows if f.get("id", "").upper() == dataflow_id.upper()), None)
    flow = flow or (flows[0] if flows else None)

    dsd = dsds[0]
    if flow is not None:
        target = _ref_id(_child(flow, "Structure"))
        dsd = next((d for d in dsds if d.get("id") == target), dsd)

    comps = _child(dsd, "DataStructureComponents")
    dims: list[Component] = []
    time_dim = None
    attributes: list[Component] = []
    measure = None
    # Only direct children of each list: attributes also nest <Dimension> references.
    dim_list = _child(comps, "DimensionList")
    dim_list = comps if dim_list is None else dim_list
    for el in _children(dim_list, "Dimension"):
        dims.append(_component(el, "dimension", concepts))
    for el in _children(dim_list, "TimeDimension"):
        time_dim = _component(el, "time", concepts)
    if _child(comps, "AttributeList") is not None:
        for el in _children(_child(comps, "AttributeList"), "Attribute"):
            attributes.append(_component(el, "attribute", concepts))
    if _child(comps, "MeasureList") is not None:
        for el in _children(_child(comps, "MeasureList"), "PrimaryMeasure"):
            measure = _component(el, "measure", concepts)
    dims.sort(key=lambda d: d.position)

    annotations = _annotations(flow) + _annotations(dsd)
    return DataStructure(
        id=dsd.get("id", ""),
        agency=dsd.get("agencyID", ""),
        version=dsd.get("version", ""),
        name=_name(dsd, lang),
        dataflow_id=flow.get("id") if flow is not None else (dataflow_id or dsd.get("id", "")),
        dataflow_name=_name(flow, lang) if flow is not None else _name(dsd, lang),
        dimensions=dims,
        time_dimension=time_dim,
        attributes=attributes,
        measure=measure,
        codelists=codelists,
        constraints=_parse_constraints(root, flow.get("id") if flow is not None else dataflow_id),
        annotations=annotations,
    )
