# SPDX-License-Identifier: MIT
"""Write app/backend/suppl_units.json: the supplementary unit of each CN8 line (number of items, litres, pairs...).

ds-045409 publishes SUPPLEMENTARY_QUANTITY as a bare number: its SDMX structure has no unit attribute. The unit
of each CN8 code is in Eurostat's Comext classification files (bulk download, CC BY 4.0):

  comext/COMEXT_METADATA/CLASSIFICATIONS_AND_RELATIONS/RELATIONS/CN8-SUPPL_UNIT.txt   code, unit, valid from, to
  comext/COMEXT_METADATA/CLASSIFICATIONS_AND_RELATIONS/CLASSIFICATIONS/ENGLISH/SUPPL_UNIT.txt   unit, name

A CN8 code with no row for a year has no supplementary unit that year (it is counted by weight only).

    python scripts/suppl_units.py            # downloads both files and rewrites the JSON
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "backend" / "suppl_units.json"
FILES = "https://ec.europa.eu/eurostat/api/dissemination/files/?file=comext%2FCOMEXT_METADATA%2FCLASSIFICATIONS_AND_RELATIONS%2F"
RELATION = FILES + "RELATIONS%2FCN8-SUPPL_UNIT.txt"
NAMES = FILES + "CLASSIFICATIONS%2FENGLISH%2FSUPPL_UNIT.txt"
FIRST_YEAR = 1999          # the first year in the database

# How a figure reads in a sentence: 12,400 items, 3,100 pairs, 45,000 litres. Units not listed use their name.
SHORT = {"PST": "items", "PA": "pairs", "L": "litres", "L_ALC_100PCT": "litres of pure alcohol", "M": "metres",
         "M2": "m²", "M3": "m³", "G": "grams", "CK": "carats", "CEEL": "cells", "TJ": "terajoules",
         "100_PST": "hundred items", "1000_PST": "thousand items", "1000_L": "thousand litres",
         "1000_M3": "thousand m³", "1000_KWH": "MWh", "GI_FS": "grams of fissile isotopes", "CTL": "tonnes of capacity",
         "KG_90PCT_SDT": "kg of substance 90% dry", "KG_NET_EDA": "kg drained net weight", "KG_U": "kg of uranium",
         "KG_N": "kg of nitrogen", "KG_P2O5": "kg of P2O5", "KG_K2O": "kg of K2O", "KG_NAOH": "kg of NaOH",
         "KG_KOH": "kg of KOH", "KG_H2O2": "kg of H2O2", "KG_MET_AM": "kg of methylamines"}


def _get(url: str) -> str:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:   # the files are Windows-1252 ('2 × 10-4 kg')
        return r.content.decode("cp1252", errors="replace")


def _year(d: str) -> int:
    return int(d.strip()[-4:])


def build(relation: str, names: str) -> dict:
    units = {}
    for line in names.splitlines():
        parts = line.rstrip("\r").split("\t")
        if len(parts) >= 2 and parts[0]:
            units[parts[0]] = {"name": parts[1].strip(), "short": SHORT.get(parts[0], parts[1].strip().lower())}
    lines: dict[str, list] = {}
    for line in relation.splitlines():
        parts = line.rstrip("\r").split("\t")
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        code, unit, a, b = parts[0], parts[1], _year(parts[2]), _year(parts[3])
        if b < FIRST_YEAR or unit == "NO_SU":
            continue
        lines.setdefault(code, []).append([unit, a, min(b, 9999)])
    used = sorted({u for v in lines.values() for u, _, _ in v})
    return {"source": "Eurostat Comext, CN8-SUPPL_UNIT.txt and SUPPL_UNIT.txt (bulk download), CC BY 4.0",
            "retrieved": date.today().isoformat(),
            "units": {u: units.get(u, {"name": u, "short": SHORT.get(u, u.lower())}) for u in used},
            "lines": dict(sorted(lines.items()))}


def main() -> None:
    data = build(_get(RELATION), _get(NAMES))
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(data['lines'])} CN8 lines, {len(data['units'])} units", file=sys.stderr)


if __name__ == "__main__":
    main()
