# SPDX-License-Identifier: MIT
"""SDMX importer: an SDMX dataflow's structure becomes a BESSER B-UML class diagram.

Ours. Reads the structure from an SDMX 2.1 REST API (client, structure), detects each dimension's
role (roles) and builds the domain model plus an import mapping (sdmx_to_buml). Candidate for
upstream as ``besser/utilities/sdmx_to_buml.py``.
"""
from .client import SOURCES, SDMXClient, Source, TooLargeError
from .live import load_structure
from .roles import detect_roles, find_total
from .sdmx_to_buml import (
    DimensionMapping, ImportMapping, code_rows, infer_prefix_parents, literal_name, pascal, sdmx_to_buml, snake,
)
from .structure import (
    Code, Codelist, Component, DataStructure, parse_constraint, parse_hierarchy, parse_structure,
)

__all__ = [
    "SOURCES", "SDMXClient", "Source", "TooLargeError", "load_structure", "detect_roles", "find_total",
    "DimensionMapping", "ImportMapping", "code_rows", "infer_prefix_parents", "literal_name", "pascal",
    "sdmx_to_buml", "snake", "Code", "Codelist", "Component", "DataStructure", "parse_constraint",
    "parse_hierarchy", "parse_structure",
]
