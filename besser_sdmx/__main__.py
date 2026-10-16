# SPDX-License-Identifier: MIT
"""Command line: SDMX dataflow -> BESSER class diagram, and SDMX data -> the generated database.

    python -m besser_sdmx import eurostat-comext DS-045409 --name LuxTrade \
        --class-name CXT_FREE_ISO=Country --out model/
    python -m besser_sdmx load --mapping model/lux_trade_mapping.json \
        --orm app/backend/sql_alchemy.py --slices model/lux_trade_slices.json --db data/lux_trade.db
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from besser.utilities.buml_code_builder import domain_model_to_code

from .importer import SOURCES, SDMXClient, load_structure, sdmx_to_buml, snake

log = logging.getLogger("besser_sdmx")


def cmd_import(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t = time.time()
    log.info("Structure: %s from %s (live, cached in %s)", args.flow, SOURCES[args.source].name, args.cache)
    dsd = load_structure(args.source, args.flow, args.cache, refresh=args.refresh)
    log.info("  parsed in %.0f s", time.time() - t)
    class_names = dict(kv.split("=", 1) for kv in args.class_name)
    model, mapping = sdmx_to_buml(
        dsd,
        model_name=args.name,
        enum_max=args.enum_max,
        class_names=class_names,
        prefix_hierarchy=args.hierarchy or None,
        source=args.source,
    )

    stem = snake(model.name)
    model_file = out / f"{stem}_model.py"
    mapping_file = out / f"{stem}_mapping.json"
    domain_model_to_code(model, str(model_file), model_var_name="domain_model")
    mapping_file.write_text(json.dumps(
        {"source": args.source, "flow": dsd.dataflow_id, "title": dsd.dataflow_name, "mapping": mapping.to_dict()},
        indent=2, ensure_ascii=False,
    ), encoding="utf-8")

    log.info("Dataflow %s: %s", dsd.dataflow_id, dsd.dataflow_name)
    for dm in mapping.dimensions:
        extra = f", hierarchy={dm.hierarchy}" if dm.hierarchy else ""
        size = f" ({len(dm.literals)} literals)" if dm.literals else ""
        log.info("  %-12s -> %-5s %-12s role=%-9s total=%s default=%s%s%s", dm.id, dm.kind, dm.type_name or dm.attr,
                 dm.role, dm.total_code, dm.default_code, size, extra)
    log.info("Model:   %s", model_file)
    log.info("Mapping: %s", mapping_file)


def _expand(dsd, mapping, key: dict) -> dict:
    """Expand '@level:N' into the codes at that hierarchy level."""
    from .importer import code_rows
    if isinstance(key, str):  # a raw SDMX key such as "all" or "A.LU..TOTAL.1+2."
        return key
    out = {}
    for dim, value in key.items():
        values = [value] if isinstance(value, str) else list(value)
        expanded = []
        for v in values:
            if isinstance(v, str) and v.startswith("@level:"):
                dm = next(d for d in mapping.dimensions if d.id == dim)
                lvl = int(v.split(":", 1)[1])
                expanded += [r["code"] for r in code_rows(dsd, dm) if r.get("level") == lvl]
            else:
                expanded.append(v)
        out[dim] = expanded[0] if len(expanded) == 1 else expanded
    return out


def cmd_load(args: argparse.Namespace) -> None:
    from .importer import ImportMapping
    from .loader import Loader, Slice, import_module
    from .patches import patch_generated_backend

    meta = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    mapping = ImportMapping.from_dict(meta["mapping"])

    client = SDMXClient(meta["source"])
    dsd = load_structure(meta["source"], meta["flow"], args.cache)

    orm_path = Path(args.orm)
    patch_generated_backend(orm_path.parent)
    orm = import_module(orm_path)
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    loader = Loader(orm, f"sqlite:///{db.resolve().as_posix()}", dsd, mapping)

    spec = json.loads(Path(args.slices).read_text(encoding="utf-8"))
    slices = [Slice(_expand(dsd, mapping, s["key"]), s.get("start"), s.get("end"), s.get("label", ""))
              for s in spec["slices"]]
    t = time.time()
    if args.register_only:
        loader.register_slices(slices)
        log.info("Registered %d slices as loaded in %s (no download)", len(slices), db)
        return
    if args.append:
        loader.load_known_codes()
    else:
        loader.reset()
        loader.load_codelists()
    n = loader.load_slices(client, slices, replace=args.append)
    loader.index()
    log.info("Loaded %d observations into %s in %.0f s", n, db, time.time() - t)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="besser_sdmx", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import", help="SDMX dataflow -> B-UML domain model + loader mapping")
    imp.add_argument("source", choices=sorted(SOURCES))
    imp.add_argument("flow", help="dataflow id, e.g. DS-045409 or DSD_INT_TRAD@DF_E4103")
    imp.add_argument("--name", help="domain model name (default: dataflow id)")
    imp.add_argument("--out", default="model", help="output folder for the model and mapping")
    imp.add_argument("--cache", default="data/raw", help="where to cache the structure message")
    imp.add_argument("--refresh", action="store_true", help="re-download the structure even if cached")
    imp.add_argument("--enum-max", type=int, default=50, help="flat codelists up to this size become enumerations")
    imp.add_argument("--class-name", action="append", default=[], metavar="ID=Name",
                     help="name a generated class by dimension or codelist id (repeatable)")
    imp.add_argument("--hierarchy", action="append", default=[], metavar="DIM",
                     help="dimension whose codes nest by prefix (default: auto-detect)")
    imp.set_defaults(func=cmd_import)

    load = sub.add_parser("load", help="download data (live) into the database of a BESSER-generated backend")
    load.add_argument("--mapping", required=True, help="mapping JSON written by 'import'")
    load.add_argument("--orm", required=True, help="sql_alchemy.py generated by BESSER from the model")
    load.add_argument("--slices", required=True, help="JSON list of queries to load")
    load.add_argument("--db", required=True, help="SQLite database file to (re)create")
    load.add_argument("--cache", default="data/raw", help="structure cache folder")
    load.add_argument("--append", action="store_true",
                      help="keep existing rows and codelists (a row already there is replaced, never doubled)")
    load.add_argument("--register-only", action="store_true",
                      help="record the slices as loaded without downloading (database filled before the registry)")
    load.set_defaults(func=cmd_load)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
