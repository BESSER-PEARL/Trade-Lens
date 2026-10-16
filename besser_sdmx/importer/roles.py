# SPDX-License-Identifier: MIT
"""Detect what each SDMX dimension *is*, so generators need no dataset-specific config.

Every dimension gets exactly one role:

* ``frequency``  the FREQ dimension (annual, monthly, ...). Never summed; one value at a time.
* ``unit``       anything that must never be summed across its codes: units of measure,
                 indicators/measures (value vs quantity, imports vs exports, GDP vs
                 consumption), and dimensions with a single code in the data (constants
                 such as ``_Z``). One value at a time: ``default_code``.
* ``geo``        countries and areas: the codelist is mostly ISO 3166 alpha-2 codes, or the
                 concept is an area/country/partner/reporter. Gets a map; aggregates
                 (World, EU27...) are kept out of rankings.
* ``hierarchy``  a nomenclature with levels: SDMX parent references, a .Stat Suite
                 ``HIER_CONTEXT`` hierarchical codelist, or numeric codes nesting by prefix
                 (HS2 > HS4 > HS6 > CN8). Gets drill-down and the significance ladder.
* ``breakdown``  every other categorical dimension (sex, age, sector...).

Each dimension also gets a ``total_code`` (the code that holds the sum of the others, by SDMX
conventions ``_T``, ``TOTAL``, ``WORLD``... or labels such as "Total", "World") and a
``default_code``: what a query uses when the dimension is not being analysed, so that values
are never double counted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .structure import Codelist, Component, DataStructure

FREQUENCY, GEO, HIERARCHY, UNIT, BREAKDOWN = "frequency", "geo", "hierarchy", "unit", "breakdown"
ROLES = (FREQUENCY, GEO, HIERARCHY, UNIT, BREAKDOWN)

_FREQ_IDS = {"FREQ", "FREQUENCY"}
# Concepts that are measures or units: summing across their codes is meaningless.
_UNIT_IDS = re.compile(
    r"^(UNIT|UNIT_MEASURE|UNIT_MEAS|UNIT_MULT|UNITS|CURRENCY|PRICES?|INDICATORS?|INDIC\w*|MEASURE|MEASURES|"
    r"NA_ITEM|STO|S_ADJ|ADJUSTMENT|FLOW|FLOWS|STK_FLOW|TRANSFORMATION|TRANSFORM|BASE_PER|BASE_PERIOD|DECIMALS)$")
_UNIT_WORDS = ("unit", "measure", "indicator", "currency", "adjustment", "flow", "base period", "price")
_GEO_IDS = re.compile(r"(^|_)(GEO|AREA|REF_AREA|COUNTRY|COUNTERPART_AREA|PARTNER|REPORTER|C_PART|C_IMP|C_EXP|REGION)($|_)")
_GEO_WORDS = ("country", "area", "partner", "reporter", "geopolitical", "region", "geo")
_TOTAL_CODES = ("TOTAL", "_T", "WORLD", "T", "TOT", "ALL", "W0", "W1", "_X")
_TOTAL_LABELS = re.compile(r"^(total|world|all countries( of the world)?|all|all items|all activities|"
                           r"total[ ,].*|world total)$", re.I)
_ISO2 = re.compile(r"^[A-Z]{2}$")
# Eurostat/Comext pseudo-countries (QP..QZ: not specified, confidential) and 2-letter aggregates.
_NOT_COUNTRY = re.compile(r"^(Q[P-Z]|EU|EA|EZ|XX|ZZ)$")
GEO_MEMBER_PATTERN = r"(?!Q[P-Z]|EU$|EA$|EZ$|XX$|ZZ$)[A-Z]{2}"
# Preferred default for unit-like dimensions, when no total exists: the headline value.
_HEADLINE = [(re.compile(p, re.I), w) for p, w in (
    (r"^(VALUE_IN_EUROS|EUR|MIO_EUR|CP_MEUR|CP_MNAC|NR|THS|THS_PER|PER)$", 8),  # levels in money or counts
    (r"MEUR|MIO_EUR|euro", 2), (r"value", 2), (r"gross domestic product", 4), (r"total", 2),
    (r"^EXP|export", 1),
    (r"^PC|_PC|^PCH|percent|percentage|^I\d|index|rate|_HAB|per capita|^RT", -6),  # ratios are not summable levels
)]


@dataclass
class DimensionRole:
    id: str
    role: str
    label: str
    total_code: Optional[str] = None
    default_code: Optional[str] = None
    hierarchy: Optional[str] = None  # "sdmx" | "prefix" | "hcl"
    used_codes: list[str] = field(default_factory=list)
    reason: str = ""


def _concept_key(dim: Component) -> str:
    return (dim.concept or dim.id or "").upper()


def _label_of(cl: Optional[Codelist], code: str) -> str:
    if cl and code in cl.codes:
        return cl.codes[code].name or code
    return code


def _iso_share(codes: Iterable[str]) -> float:
    codes = list(codes)
    if not codes:
        return 0.0
    return sum(1 for c in codes if _ISO2.match(c)) / len(codes)


def looks_prefix_hierarchical(codes: Iterable[str]) -> bool:
    """Numeric codes where most have a strictly shorter code as prefix (HS/CN, NACE digits...)."""
    from .sdmx_to_buml import infer_prefix_parents  # local import: importer depends on this module

    codes = list(codes)
    digit_codes = [c for c in codes if c.isdigit()]
    if len(digit_codes) < 50:
        return False
    # A run of plain integers (ranks 1..N) nests by prefix too, but is not a nomenclature.
    if not any(c.startswith("0") for c in digit_codes):
        ints = {int(c) for c in digit_codes}
        if len(ints) > 0.9 * (max(ints) - min(ints) + 1):
            return False
    parents = infer_prefix_parents(codes)
    with_parent = sum(1 for c in digit_codes if parents.get(c) not in (None, "TOTAL"))
    return with_parent / len(digit_codes) > 0.5


def find_total(cl: Optional[Codelist], used: list[str], parents: Optional[dict] = None) -> Optional[str]:
    """The code holding the total of a dimension, by SDMX conventions, labels, or the hierarchy root."""
    present = set(used)
    for code in _TOTAL_CODES:
        if code in present and code != "_Z":
            return code
    for code in used:
        if _TOTAL_LABELS.match(_label_of(cl, code).strip()):
            return code
    if parents:
        roots = [c for c in used if parents.get(c) is None]
        if len(roots) == 1 and len(used) > 1:
            return roots[0]
    return None


def _headline(cl: Optional[Codelist], used: list[str]) -> Optional[str]:
    if not used:
        return None
    best, best_score = used[0], 0
    for code in used:
        text = f"{code} {_label_of(cl, code)}"
        score = sum(w for rx, w in _HEADLINE if rx.search(code) or rx.search(text))
        if score > best_score:
            best, best_score = code, score
    return best


def detect_roles(dsd: DataStructure, prefix_hierarchy: Optional[Iterable[str]] = None) -> dict[str, DimensionRole]:
    """Assign a role, a total and a default code to every dimension of ``dsd`` (deterministic)."""
    forced_prefix = set(prefix_hierarchy) if prefix_hierarchy is not None else None
    out: dict[str, DimensionRole] = {}
    for dim in dsd.dimensions:
        cl = dsd.codelist_for(dim)
        used = dsd.used_codes(dim)
        label = dim.name or dim.id
        key = _concept_key(dim)
        words = f"{dim.id} {dim.concept or ''} {label}".lower()

        hierarchy, parents = None, None
        if dim.id in dsd.hierarchies:
            hierarchy, parents = "hcl", dsd.hierarchies[dim.id]
        elif cl is not None and cl.has_parents:
            hierarchy, parents = "sdmx", {c.id: c.parent for c in cl.codes.values()}
        elif cl is not None and (
                (forced_prefix is not None and dim.id in forced_prefix)
                or (forced_prefix is None and looks_prefix_hierarchical(cl.codes))):
            from .sdmx_to_buml import infer_prefix_parents
            hierarchy, parents = "prefix", infer_prefix_parents(cl.codes)

        total = find_total(cl, used, parents)

        if dim.id.upper() in _FREQ_IDS or key in _FREQ_IDS:
            role, reason = FREQUENCY, "frequency concept"
        elif len(used) == 1:
            role, reason = UNIT, "a single code in the data (constant)"
        elif _UNIT_IDS.match(dim.id.upper()) or _UNIT_IDS.match(key) or (
                any(w in label.lower() for w in _UNIT_WORDS) and not hierarchy and len(used) <= 60):
            role, reason = UNIT, "unit/measure concept: never summed"
        elif (_GEO_IDS.search(dim.id.upper()) or any(w in words for w in _GEO_WORDS)
              or (_iso_share(used) >= 0.6 and len(used) >= 10)):
            role, reason = GEO, f"area concept, {_iso_share(used):.0%} ISO alpha-2 codes"
        elif hierarchy:
            role, reason = HIERARCHY, f"{hierarchy} hierarchy"
        else:
            role, reason = BREAKDOWN, "categorical"

        if role == FREQUENCY:
            default = "A" if "A" in used else (used[0] if used else None)
        elif role == UNIT:
            default = used[0] if len(used) == 1 else (total or _headline(cl, used))
        elif total:
            default = total
        elif role == GEO:
            # No world total: fall back to the first aggregate (EU27, euro area...) if any.
            default = next((c for c in used if not _ISO2.match(c) or _NOT_COUNTRY.match(c)), None)
        else:
            default = None  # summing a breakdown without a total is the best we can do
        out[dim.id] = DimensionRole(dim.id, role, label, total, default, hierarchy, used, reason)
    return out


def member_pattern(role: DimensionRole) -> Optional[str]:
    """Regex for the "real" items of a dimension in rankings (countries, not aggregates)."""
    return GEO_MEMBER_PATTERN if role.role == GEO else None
