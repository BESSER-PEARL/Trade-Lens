# SPDX-License-Identifier: MIT
"""Load SDMX data into the database of a BESSER-generated backend.

The schema is never hand-written: the loader imports the ``sql_alchemy.py`` that
BESSER generated from the imported model, so tables, enums and foreign keys
always match the model.
"""
from __future__ import annotations

import enum
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Iterable, Optional, Union

import pandas as pd
from sqlalchemy import create_engine, inspect as sa_inspect, text

from ..importer.client import SDMXClient, TooLargeError
from ..importer.sdmx_to_buml import ImportMapping, code_rows
from ..importer.structure import DataStructure

log = logging.getLogger(__name__)

KeySpec = Union[str, dict[str, Union[str, Iterable[str]]]]
CHUNK = 150  # codes per request when an "all codes" dimension must be split (keeps URLs short)

# Which slices of the dataflow are in the database. Lets the generated analytics API tell
# "zero" from "never loaded" and fetch what is missing live (see InsightsGenerator).
REGISTRY = "_loaded_slice"
REGISTRY_DDL = (f'CREATE TABLE IF NOT EXISTS "{REGISTRY}" (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                'spec TEXT NOT NULL, start_period TEXT, end_period TEXT, rows INTEGER, source TEXT, loaded_at TEXT)')


@dataclass
class Slice:
    """One data query: a series key (string or {dimension: code(s)}) and a period range."""
    key: KeySpec = "all"
    start: Optional[str] = None
    end: Optional[str] = None
    label: str = ""
    extra: dict = field(default_factory=dict)


def import_module(path: Union[str, Path], name: str = "besser_generated_orm") -> ModuleType:
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build_key(dsd: DataStructure, key: KeySpec) -> str:
    if isinstance(key, str):
        return key
    parts = []
    for dim in dsd.dimensions:
        v = key.get(dim.id, "")
        parts.append(v if isinstance(v, str) else "+".join(v))
    return ".".join(parts)


def slice_spec(dsd: DataStructure, key: KeySpec) -> dict[str, Union[str, list[str]]]:
    """A slice key as {dimension id: sorted codes, or "*" for all codes}."""
    if isinstance(key, str):
        parts = [] if key in ("", "all") else key.split(".")
        parts += [""] * (len(dsd.dimensions) - len(parts))
        key = {d.id: (p.split("+") if p else "") for d, p in zip(dsd.dimensions, parts)}
    spec: dict[str, Union[str, list[str]]] = {}
    for dim in dsd.dimensions:
        v = key.get(dim.id, "")
        values = [v] if isinstance(v, str) else list(v)
        values = [x for x in values if x]
        spec[dim.id] = sorted(set(values)) if values else "*"
    return spec


def register_slice(conn, spec: dict, start: Optional[str], end: Optional[str], rows: int, source: str) -> None:
    """Record that ``spec`` x [start, end] is in the database (even when it holds no data)."""
    import json
    from datetime import datetime, timezone

    conn.execute(text(REGISTRY_DDL))
    conn.execute(text(f'INSERT INTO "{REGISTRY}" (spec, start_period, end_period, rows, source, loaded_at) '
                      'VALUES (:spec, :start, :end, :rows, :source, :at)'),
                 {"spec": json.dumps(spec, sort_keys=True), "start": start, "end": end, "rows": rows,
                  "source": source, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def _years(start: str, end: str) -> list[tuple[str, str]]:
    y0, y1 = int(start[:4]), int(end[:4])
    return [(str(y), str(y)) for y in range(y0, y1 + 1)]


def fetch(client: SDMXClient, dsd: DataStructure, s: Slice, _depth: int = 0) -> pd.DataFrame:
    """Fetch a slice. When the provider says it is too large, split it: first by year,
    then by halving the longest multi-code dimension, until every part goes through."""
    key = build_key(dsd, s.key)
    try:
        return client.data(dsd.dataflow_id, key, s.start, s.end)
    except TooLargeError:
        parts: list[Slice] = []
        if s.start and s.end and s.start[:4] != s.end[:4]:
            for a, b in _years(s.start, s.end):
                lo = s.start if a == s.start[:4] else a
                hi = s.end if b == s.end[:4] else b
                parts.append(Slice(s.key, lo, hi, s.label))
        elif isinstance(s.key, dict):
            multi = {d: v for d, v in s.key.items() if not isinstance(v, str) and len(v) > 1}
            if multi:
                dim, values = max(multi.items(), key=lambda kv: len(kv[1]))
                half = len(values) // 2
                for chunk in (values[:half], values[half:]):
                    parts.append(Slice({**s.key, dim: chunk if len(chunk) > 1 else chunk[0]}, s.start, s.end, s.label))
            else:
                # Last resort: an "all codes" dimension becomes explicit chunks of the codes in use.
                open_dims = [c for c in dsd.dimensions if s.key.get(c.id, "") == ""]
                if not open_dims:
                    raise
                comp = max(open_dims, key=lambda c: len(dsd.used_codes(c)))
                codes = dsd.used_codes(comp)
                if len(codes) < 2:
                    raise
                for i in range(0, len(codes), CHUNK):
                    chunk = codes[i:i + CHUNK]
                    parts.append(Slice({**s.key, comp.id: chunk if len(chunk) > 1 else chunk[0]},
                                       s.start, s.end, s.label))
        else:
            raise
        log.info("  %s%s too large, splitting into %d parts", "  " * _depth, s.label or key, len(parts))
        frames = [fetch(client, dsd, p, _depth + 1) for p in parts]
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


class Loader:
    def __init__(self, orm: ModuleType, db_url: str, dsd: DataStructure, mapping: ImportMapping):
        self.orm = orm
        self.dsd = dsd
        self.mapping = mapping
        self.engine = create_engine(db_url)
        self.obs_cls = getattr(orm, mapping.observation_class)
        self._known_codes: dict[str, set[str]] = {}
        self._seen: set[tuple] = set()

    # -- schema -----------------------------------------------------------------
    def reset(self) -> None:
        self.orm.Base.metadata.drop_all(self.engine)
        self.orm.Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS "{REGISTRY}"'))
            conn.execute(text(REGISTRY_DDL))

    def _fk_column(self, cls, relationship: str) -> str:
        rel = sa_inspect(cls).relationships[relationship]
        return next(iter(rel.local_columns)).key

    # -- reference data ---------------------------------------------------------
    def load_codelists(self) -> None:
        for dm in self.mapping.dimensions:
            if dm.kind != "class" or dm.type_name in self._known_codes:
                continue  # dimensions sharing a codelist share one class
            cls = getattr(self.orm, dm.type_name)
            rows = code_rows(self.dsd, dm)
            if dm.hierarchy:
                parent_col = self._fk_column(cls, "parent")
                for r in rows:
                    r[parent_col] = r.pop("parent")
            with self.engine.begin() as conn:
                conn.execute(cls.__table__.insert(), rows)
            self._known_codes[dm.type_name] = {r["code"] for r in rows}
            log.info("  %s: %d codes", dm.type_name, len(rows))

    def load_known_codes(self) -> None:
        """Read the codes already in the database, so that appending does not insert them again."""
        for dm in self.mapping.dimensions:
            if dm.kind != "class" or dm.type_name in self._known_codes:
                continue
            table = getattr(self.orm, dm.type_name).__table__
            with self.engine.connect() as conn:
                self._known_codes[dm.type_name] = {row[0] for row in conn.execute(table.select().with_only_columns(table.c.code))}

    # -- observations -----------------------------------------------------------
    def _column(self, df: pd.DataFrame, wanted: str) -> Optional[str]:
        lower = {c.lower(): c for c in df.columns}
        return lower.get(wanted.lower())

    def _rows(self, df: pd.DataFrame) -> list[dict]:
        m = self.mapping
        cols: dict[str, tuple[str, object]] = {}
        missing_codes: dict[str, set[str]] = {}
        for dm in m.dimensions:
            src = self._column(df, dm.id)
            if src is None:
                raise KeyError(f"Column for dimension {dm.id!r} not in data: {list(df.columns)}")
            if dm.kind == "enum":
                enum_cls = getattr(self.orm, dm.type_name)
                conv = {code: enum_cls[lit] for code, lit in dm.literals.items()}
                cols[dm.attr] = (src, conv)
            elif dm.kind == "class":
                fk = self._fk_column(self.obs_cls, dm.attr)
                cols[fk] = (src, None)
                unknown = set(df[src].unique()) - self._known_codes.get(dm.type_name, set())
                if unknown:
                    missing_codes.setdefault(dm.type_name, set()).update(unknown)
            else:
                cols[dm.attr] = (src, None)
        time_col = self._column(df, "TIME_PERIOD")
        value_col = self._column(df, "OBS_VALUE")
        attr_cols = {name: self._column(df, sid) for sid, name in m.attributes.items()}

        self._add_missing_codes(missing_codes)

        records = []
        for rec in df.to_dict("records"):
            row = {}
            for target, (src, conv) in cols.items():
                v = rec[src]
                row[target] = conv[v] if conv is not None else v
            if m.time_attr:
                row[m.time_attr] = rec[time_col]
            raw = rec[value_col]
            row[m.value_attr] = float(raw) if raw not in ("", None) else None
            for name, src in attr_cols.items():
                row[name] = (rec[src] or None) if src else None
            records.append(row)
        return records

    def _add_missing_codes(self, missing: dict[str, set[str]]) -> None:
        for type_name, codes in missing.items():
            dm = next(d for d in self.mapping.dimensions if d.type_name == type_name)
            cls = getattr(self.orm, type_name)
            rows = [{"code": c, "label": c} for c in sorted(codes)]
            if dm.hierarchy:
                for r in rows:
                    r["level"] = 0
            with self.engine.begin() as conn:
                conn.execute(cls.__table__.insert(), rows)
            self._known_codes.setdefault(type_name, set()).update(codes)
            log.warning("  %s: added %d codes missing from the codelist", dm.type_name, len(rows))

    def _key_columns(self) -> list[str]:
        cols = [self._fk_column(self.obs_cls, d.attr) if d.kind == "class" else d.attr for d in self.mapping.dimensions]
        return cols + ([self.mapping.time_attr] if self.mapping.time_attr else [])

    def _replace_existing(self, conn, rows: list[dict]) -> None:
        """Appending: a row already in the database (an earlier load, or a slice the analytics API fetched live)
        is replaced by the new one, never doubled."""
        keys = self._key_columns()
        cond = " AND ".join(f'"{k}" = :{k}' for k in keys)
        conn.execute(text(f'DELETE FROM "{self.obs_cls.__tablename__}" WHERE {cond}'),
                     [{k: (r[k].name if isinstance(r[k], enum.Enum) else r[k]) for k in keys} for r in rows])

    def load_slices(self, client: SDMXClient, slices: list[Slice], chunk: int = 50_000, replace: bool = False) -> int:
        """Download and insert the slices. ``replace`` (when appending to a database that has rows): rows whose
        key is already there replace them instead of doubling them."""
        table = self.obs_cls.__table__
        total = 0
        if replace:
            self.index()   # the series-key index makes the replace a lookup per row
        for s in slices:
            df = fetch(client, self.dsd, s)
            with self.engine.begin() as conn:  # loaded, even if empty: "zero" is not "missing"
                register_slice(conn, slice_spec(self.dsd, s.key), s.start, s.end, len(df), "load")
            if df.empty:
                log.info("  %s: no data", s.label or s.key)
                continue
            dims = [c for c in (self._column(df, d.id) for d in self.dsd.dimensions) if c]
            key_cols = dims + [self._column(df, "TIME_PERIOD")]
            df = df.drop_duplicates(subset=key_cols)
            # Slices may overlap (e.g. WORLD x HS2 in two queries): keep the first copy.
            keys = list(df[key_cols].itertuples(index=False, name=None))
            fresh = [k not in self._seen for k in keys]
            self._seen.update(keys)
            df = df[fresh]
            if df.empty:
                log.info("  %s: nothing new", s.label or s.key)
                continue
            rows = self._rows(df)
            for i in range(0, len(rows), chunk):
                with self.engine.begin() as conn:   # one transaction per chunk: the database is never locked long
                    if replace:
                        self._replace_existing(conn, rows[i:i + chunk])
                    conn.execute(table.insert(), rows[i:i + chunk])
            total += len(rows)
            log.info("  %s: %d observations", s.label or s.key, len(rows))
        return total

    def register_slices(self, slices: list[Slice]) -> None:
        """Record slices as loaded without downloading them: for a database filled before the
        registry existed (the analytics API then knows what is there and fetches the rest live)."""
        with self.engine.begin() as conn:
            conn.execute(text(REGISTRY_DDL))
            conn.execute(text(f'DELETE FROM "{REGISTRY}" WHERE source = :s'), {"s": "load"})
            for s in slices:
                register_slice(conn, slice_spec(self.dsd, s.key), s.start, s.end, -1, "load")

    def index(self) -> None:
        """Indexes on the observation foreign keys and time, for dashboard queries."""
        table = self.obs_cls.__tablename__
        cols = [self._fk_column(self.obs_cls, d.attr) if d.kind == "class" else d.attr for d in self.mapping.dimensions]
        if self.mapping.time_attr:
            cols.append(self.mapping.time_attr)
        # One index on the whole series key: a query of one slice (and the analytics API's replace-before-insert of
        # a live fetch, one row at a time) is a lookup, not a scan of every row of one frequency. Classes first
        # (the nomenclature, the most selective, ahead), then enumerations and time.
        classes = [self._fk_column(self.obs_cls, d.attr) for d in reversed(self.mapping.dimensions) if d.kind == "class"]
        rest = [d.attr for d in self.mapping.dimensions if d.kind != "class"]
        key = classes + rest + ([self.mapping.time_attr] if self.mapping.time_attr else [])
        with self.engine.begin() as conn:
            for c in cols:
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{table}_{c}" ON "{table}" ("{c}")'))
            if len(key) > 1:
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{table}_slice" ON "{table}" '
                                  f'({", ".join(chr(34) + c + chr(34) for c in key)})'))
