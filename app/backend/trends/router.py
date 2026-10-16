# SPDX-License-Identifier: MIT
"""``/trends`` endpoints: filter options for the Trends page, and trend analysis on a
filtered series (STL decomposition + ruptures change-point detection, see ``analysis.py``).

Reuses ``routers.insights`` for everything about *which* observations to look at (dimension
filters, live SDMX fetch-on-demand, period lists): ``analyze`` filters exactly like
``/insights/series`` because it calls that endpoint's function directly, so a query such as
``?flow=EXPORT&product=84&freq=M&start=2018-01`` behaves identically in both places.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

import numpy as np

from database import get_db
from routers.insights import CONFIG, DIMS, FOCUS, OBS, TIME, VALUE_EXPR, _filters, _history_rows, _is_member, _labels, _where
from routers.insights import meta as insights_meta
from routers.insights import series as insights_series

from .analysis import MIN_POINTS, analyze_series, seasonal_pattern, stl_on_history

router = APIRouter(prefix="/trends", tags=["Trends"])

# Short HS chapter names for prose and pickers, the same list as CHAPTER_SHORT in
# app/frontend/src/view/charts/format.ts (keep the two in step).
CHAPTER_SHORT = {
    "02": "Meat", "04": "Dairy, eggs and honey", "22": "Beverages", "24": "Tobacco",
    "27": "Mineral fuels and oils", "29": "Organic chemicals", "30": "Pharmaceuticals",
    "33": "Perfumes and cosmetics", "38": "Chemical products", "39": "Plastics", "40": "Rubber", "44": "Wood",
    "48": "Paper and paperboard", "56": "Wadding, felt and nonwovens", "59": "Coated textile fabrics",
    "64": "Footwear", "70": "Glass", "71": "Pearls, stones and precious metals", "72": "Iron and steel",
    "73": "Articles of iron or steel", "76": "Aluminium", "84": "Machinery", "85": "Electrical machinery",
    "87": "Vehicles and parts", "88": "Aircraft", "90": "Optical and medical instruments", "94": "Furniture",
}


def _short_product(code: str, label: Optional[str]) -> str:
    """"Machinery" for 84; else the official label before its first ";" or " (", in sentence case."""
    if code in CHAPTER_SHORT:
        return CHAPTER_SHORT[code]
    text_ = (label or "").split(";")[0].split(" (")[0].strip()
    if not text_:
        return ""
    if text_.isupper():
        text_ = text_[:1] + text_[1:].lower()
    return text_


def goods_name(db: Session, product: Optional[str]) -> str:
    """The product filter as a short lower-case name for prose: "goods" for the total, "rubber" for 40,
    "rubber and plastics" for 40,39; an unknown code reads "HS <code> goods"."""
    total = DIMS["product"].get("total_code") or "TOTAL"
    codes = [c.strip() for c in str(product or "").split(",") if c.strip()]
    if not codes or codes == [total]:
        return "goods"
    found = {}
    rows = db.execute(text('SELECT code, label FROM product WHERE code IN ({})'.format(
        ", ".join(f":c{i}" for i in range(len(codes))))), {f"c{i}": c for i, c in enumerate(codes)}).fetchall()
    for c, l in rows:
        found[c] = l
    names = []
    for c in codes:
        if c == total:
            names.append("goods")
            continue
        n = _short_product(c, found.get(c))
        names.append(n.lower() if n else f"HS {c} goods")
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _reporter_options(db: Session) -> list[dict]:
    """Reporter countries present in the database, ranked by total trade value. Today this is
    Luxembourg (the focus, full detail) plus the other EU countries loaded only for the
    "Luxembourg vs EU peers" chart (annual totals only — no partner/product breakdown). Listed
    anyway: more reporters may be loaded later, and the filter should not hide that choice.
    """
    reporter = DIMS["reporter"]
    focus = FOCUS.get("reporter")
    sql = (f'SELECT o."{reporter["column"]}" AS code, SUM({VALUE_EXPR}) AS value FROM "{OBS}" o '
           f'GROUP BY o."{reporter["column"]}" ORDER BY value DESC')
    rows = db.execute(text(sql)).fetchall()
    codes = [c for c, _ in rows if _is_member(reporter, c, db)]
    labels = _labels(db, reporter, codes)
    # The focus reporter (Luxembourg) has the full per-partner, per-product, monthly depth; the
    # others only have annual totals (loaded for the "vs EU peers" chart) — pinned first
    # regardless of raw trade value, since it is the dataset's actual point of interest.
    ordered = ([focus] if focus in codes else []) + [c for c in codes if c != focus]
    return [{"code": c, "label": labels[c]} for c in ordered]


# Partner codes that are no country of today: former countries (with the years the data has them) and
# the codes Eurostat uses for trade it cannot or may not assign to a country. The picker lists them after
# the countries, in groups of their own, under plain names.
FORMER = {"AN": "Netherlands Antilles", "CS": "Serbia and Montenegro", "YU": "Yugoslavia", "SU": "Soviet Union",
          "DD": "German Democratic Republic", "XP": "West Bank and Gaza Strip", "TP": "East Timor",
          "QT": "West Indies", "XA": "American Oceania"}
NOT_A_COUNTRY = {"QQ": "Ship and aircraft stores", "QR": "Ship and aircraft stores, within the EU",
                 "QS": "Ship and aircraft stores, outside the EU", "QU": "Partner not stated",
                 "QV": "Partner not stated, within the EU", "QW": "Partner not stated, outside the EU",
                 "QX": "Partner withheld (commercial or military reasons)",
                 "QY": "Partner withheld (commercial or military reasons), within the EU",
                 "QZ": "Partner withheld (commercial or military reasons), outside the EU"}
_YEARS = re.compile(r"\s*\((\d{4})-(\d{4})\)")


def tidy_label(label: str) -> str:
    """A source label made readable: of "Western Sahara(2013-2500);Ceuta and Melilla(1976-1976)" the
    name still in use, without its years; a parenthesis a cut note left open closed off ("Occupied
    Palestinian Territory (West Bank" -> "Occupied Palestinian Territory")."""
    parts = [p.strip() for p in str(label).split(";") if p.strip()] or [str(label)]
    if len(parts) > 1 or _YEARS.search(parts[0]):
        live = [p for p in parts if (m := _YEARS.search(p)) and int(m.group(2)) >= 2100]
        label = _YEARS.sub("", (live or parts)[0])
    while label.count("(") > label.count(")"):
        label = label[:label.rfind("(")].rstrip(" ,")
    return re.sub(r"\s+", " ", label).strip()


def _partner_options(db: Session) -> list[dict]:
    """Partner countries with real, per-partner trade data for the focus reporter (Luxembourg),
    ranked by total trade value. Only Luxembourg has this depth: the other reporters in the
    database (loaded for the "Luxembourg vs EU peers" chart) only ever have ``partner=WORLD``,
    so a per-partner trend view only makes sense for Luxembourg.
    """
    partner, reporter = DIMS["partner"], DIMS["reporter"]
    total_code = partner.get("total_code")
    focus = FOCUS.get("reporter")
    sql = (f'SELECT o."{partner["column"]}" AS code, SUM({VALUE_EXPR}) AS value, MIN(o."{TIME}"), '
           f'MAX(o."{TIME}") FROM "{OBS}" o '
           f'WHERE o."{reporter["column"]}" = :reporter GROUP BY o."{partner["column"]}" ORDER BY value DESC')
    rows = db.execute(text(sql), {"reporter": focus}).fetchall()
    span = {r[0]: (r[2], r[3]) for r in rows}
    codes = [r[0] for r in rows if r[0] != total_code and _is_member(partner, r[0], db)]
    labels = _labels(db, partner, codes)
    # Countries first (largest trade first), then former countries, then the codes that are no country.
    countries, former, other = [], [], []
    for c in codes:
        if c in FORMER:
            lo, hi = (str(y or "")[:4] for y in span.get(c, ("", "")))
            years = f"{lo}–{hi}" if lo and hi and lo != hi else lo
            former.append({"code": c, "label": f"{FORMER[c]} (former, data {years})" if years else f"{FORMER[c]} (former)",
                           "group": "former"})
        elif c in NOT_A_COUNTRY:
            other.append({"code": c, "label": NOT_A_COUNTRY[c], "group": "other"})
        else:
            countries.append({"code": c, "label": tidy_label(labels[c]), "group": "country"})
    world = [{"code": total_code, "label": "World (all partners)", "group": "world"}] if total_code else []
    return world + countries + former + other


@router.get("/options")
def options(db: Session = Depends(get_db)):
    """Everything the Trends page needs to build its filter form: the product chapters
    (HS2, level 1), the reporter and partner countries, the flow/indicator choices, and the
    periods available per frequency.

    Built from ``/insights/meta`` plus three lookups, since ``meta`` only lists the values of
    enumerated dimensions (flow, indicators) — "product", "reporter" and "partner" are classes
    with dozens to thousands of codes, so the Trends page only offers product chapters (HS2
    level 1) and the countries that actually occur in the loaded data.
    """
    m = insights_meta(db)
    dims = {d["id"]: d for d in m["dimensions"]}
    products = db.execute(text('SELECT code, label FROM product WHERE level = 1 ORDER BY code')).fetchall()
    total = dims.get("product", {}).get("total_code")
    # Chapters only: "00" ("Total for countries whose data are confidential...") is no product group.
    product_options = ([{"code": total, "label": "Total (all products)"}] if total else []) + \
        [{"code": c, "label": _short_product(c, l), "full": l} for c, l in products
         if re.fullmatch(r"\d{2}", c) and c != "00" and "confidential" not in (l or "").lower()]
    return {
        "products": product_options,
        "reporters": _reporter_options(db),
        "partners": _partner_options(db),
        "flow": dims.get("flow"),
        "indicators": dims.get("indicators"),
        "reporter_default": m["defaults"].get("reporter"),
        "partner_default": m["defaults"].get("partner"),
        "periods": m["periods"],
        "unit": m["unit"],
    }


def _fetch_series(db: Session, filters: dict, start: Optional[str], end: Optional[str]) -> list[dict]:
    """Same query as ``/insights/series``, for a filter set built in Python rather than read off
    a request (used to swap in one product code per row of the heatmap)."""
    params: dict = {}
    where = []
    for i, (dim_id, values) in enumerate(filters.items()):
        col = DIMS[dim_id]["column"]
        names = []
        for j, v in enumerate(values):
            p = f"f{i}_{j}"
            params[p] = v
            names.append(f":{p}")
        where.append(f'o."{col}" IN ({", ".join(names)})')
    if start:
        where.append(f'o."{TIME}" >= :start')
        params["start"] = start
    if end:
        where.append(f'o."{TIME}" <= :end')
        params["end"] = end
    sql = (f'SELECT o."{TIME}", SUM({VALUE_EXPR}) FROM "{OBS}" o WHERE {" AND ".join(where) or "1=1"} '
           f'GROUP BY o."{TIME}" ORDER BY o."{TIME}"')
    return [{"period": p, "value": float(v or 0)} for p, v in db.execute(text(sql), params).fetchall()]


def _top_products(db: Session, base_filters: dict, start: Optional[str], end: Optional[str], top: int) -> list[dict]:
    """The ``top`` HS2 chapters by total value over ``start``–``end`` under ``base_filters`` —
    summed across the whole window, not a single period's snapshot (which can be an empty,
    not-yet-published provisional month)."""
    product = DIMS["product"]
    params: dict = {}
    where = _where(base_filters, params)
    if start:
        where.append(f'o."{TIME}" >= :start')
        params["start"] = start
    if end:
        where.append(f'o."{TIME}" <= :end')
        params["end"] = end
    params["top"] = top
    sql = (f'SELECT o."{product["column"]}" AS code, SUM({VALUE_EXPR}) AS value FROM "{OBS}" o '
           f'JOIN "product" d ON d.code = o."{product["column"]}" '
           f'WHERE {" AND ".join(where) if where else "1=1"} AND d.level = 1 '
           f'GROUP BY o."{product["column"]}" HAVING value > 0 ORDER BY value DESC LIMIT :top')
    rows = db.execute(text(sql), params).fetchall()
    codes = [c for c, _ in rows]
    labels = _labels(db, product, codes)
    return [{"code": c, "name": labels[c]} for c in codes]


@router.get("/seasonal-heatmap")
def seasonal_heatmap(request: Request, start: Optional[str] = None, end: Optional[str] = None,
                      top: int = 12, db: Session = Depends(get_db)):
    """Seasonal profile (average % effect per month/quarter) for the top products under the
    current filters (flow, reporter, partner), side by side — a month × product grid that shows
    which products share a seasonal dip/peak and which don't, instead of reading one product's
    chart at a time.
    """
    freq = request.query_params.get("freq", "M")
    cycle = {"M": 12, "Q": 4}.get(freq)
    if not cycle:
        raise HTTPException(400, "The seasonal heatmap needs a monthly or quarterly frequency (?freq=M or ?freq=Q).")

    base_filters = _filters(request, exclude={"product", CONFIG["time_dimension"]}, db=db)
    products = _top_products(db, base_filters, start, end, top)
    if not products:
        raise HTTPException(503, "No data available for this selection.")

    rows = []
    for item in products:
        # fitted on the product's whole history, the pattern averaged over start..end (analysis.stl_on_history)
        history = _fetch_series(db, {**base_filters, "product": [item["code"]]}, None, None)
        series_rows = [r for r in history if (not start or r["period"] >= start) and (not end or r["period"] <= end)]
        if not series_rows:
            continue
        try:
            fit = stl_on_history(history, cycle)
        except ValueError:  # TooShort included: under five years of history, left out
            continue
        values = np.asarray([r["value"] for r in series_rows], dtype=float)
        seasonal = np.asarray([fit[r["period"]][1] for r in series_rows], dtype=float)
        typical_level = float(np.mean(np.abs(values))) or 1.0
        pattern = seasonal_pattern([r["period"] for r in series_rows], seasonal, cycle, typical_level)
        rows.append({"code": item["code"], "name": item["name"], "pattern": pattern})
    return {"cycle": cycle, "rows": rows}


@router.get("/analyze")
def analyze(request: Request, start: Optional[str] = None, end: Optional[str] = None,
            stl_period: Optional[int] = None, min_size: int = 6, penalty: float = 3.0,
            db: Session = Depends(get_db)):
    """Trend direction, STL decomposition and candidate change points for the filtered series.

    Filters are the same dimension query parameters as ``/insights/series`` (``product``,
    ``flow``, ``indicators``, ``freq``, ``reporter``, ``partner``, ...); ``start``/``end``
    bound the period range. ``stl_period`` overrides the guessed seasonal cycle length (12 for
    monthly, 4 for quarterly); ``min_size`` is the minimum run length ruptures will call a
    change point, and ``penalty`` trades off sensitivity (lower = more change points) against
    false positives.
    """
    envelope = insights_series(request, adjust=False, start=start, end=end, envelope=True, db=db)
    if not envelope["loaded"]:
        raise HTTPException(503, envelope.get("error") or "This slice of the data is not available right now.")
    rows = envelope["rows"]
    if len(rows) < MIN_POINTS:
        raise HTTPException(400, f"Not enough data points for a trend analysis: got {len(rows)}, need at least "
                                 f"{MIN_POINTS}. Widen the period range or relax a filter.")
    # The seasonal adjustment is fitted on the slice's whole history in the database, then cut to the span
    # (analysis.analyze_series): a span's first month is not an edge of the data.
    history = _history_rows(db, _filters(request, exclude={CONFIG["time_dimension"]}, db=db)) if (start or end) else rows
    result = analyze_series(rows, stl_period=stl_period, min_size=min_size, penalty=penalty, history=history)
    return {
        **result,
        "n_points": len(rows),
        "start": rows[0]["period"],
        "end": rows[-1]["period"],
        "goods": goods_name(db, request.query_params.get("product")),
        "fetched_live": envelope["fetched_live"],
    }
