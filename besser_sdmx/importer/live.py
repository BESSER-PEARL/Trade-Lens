# SPDX-License-Identifier: MIT
"""Fetch a dataflow's structure live from its SDMX API (with a local cache) and parse it.

Completes the data structure with its content constraint (the codes actually in the data) and
its hierarchical codelists, when the provider publishes them.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .client import SDMXClient
from .structure import DataStructure, parse_constraint, parse_hierarchy, parse_structure

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("besser_sdmx")


def _cache_name(source: str, flow: str, kind: str) -> str:
    return f"{source}_{re.sub(r'[^A-Za-z0-9_.-]', '_', flow)}_{kind}.xml"


def load_structure(source: str, flow: str, cache_dir: str | Path = ROOT / "data" / "raw",
                   refresh: bool = False) -> DataStructure:
    """Parse the dataflow's structure, completed with its content constraint and hierarchies (all cached)."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    client = SDMXClient(source)

    def cached(kind: str, fetch):
        path = cache / _cache_name(source, flow, kind)
        if path.exists() and not refresh:
            return path.read_bytes()
        log.info("  fetching %s (live)", kind)
        data = fetch()
        if data:
            path.write_bytes(data)
        return data

    dsd = parse_structure(cached("structure", lambda: client.structure(flow)), flow)
    if not dsd.constraints:
        xml = cached("constraint", lambda: client.content_constraint(flow))
        if xml:
            try:
                dsd.constraints = parse_constraint(xml, flow)
            except Exception as exc:  # malformed or unexpected: roles fall back to full codelists
                log.warning("  content constraint ignored: %s", exc)
    for dim_id, (agency, hcl, version, hierarchy) in dsd.hier_context().items():
        try:
            xml = cached(f"hcl_{hcl}", lambda: client.hierarchical_codelist(agency, hcl, version))
            parents = parse_hierarchy(xml, hierarchy)
            if parents:
                dsd.hierarchies[dim_id] = parents
        except Exception as exc:
            log.warning("  hierarchy %s for %s ignored: %s", hcl, dim_id, exc)
    return dsd
