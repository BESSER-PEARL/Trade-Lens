# SPDX-License-Identifier: MIT
"""Figures written the way the page writes them (docs/agents.md, Format), for tool results.

The read tools hand the model figures already written out (``"€25.1 bn"``, ``"+8.6%"``), so the
chat reply quotes them as they are instead of doing arithmetic. Text that goes on the page never
carries a figure: it uses {fact} placeholders.
"""
from __future__ import annotations

import re
from typing import Optional

MINUS = "−"


def eur(v: Optional[float], signed: bool = False) -> Optional[str]:
    """25097941118 -> €25.1 bn; 613389780 -> €613 m; signed: +€1.29 bn / −€8.8 bn; 1563.4e9 -> €1,563.4 bn."""
    if v is None:
        return None
    sign = (MINUS if v < 0 else "+") if signed else (MINUS if v < 0 else "")
    a = abs(v)
    if a >= 1e9:
        body = f"{a / 1e9:.2f}".rstrip("0").rstrip(".") if a < 1e10 else f"{a / 1e9:,.1f}".rstrip("0").rstrip(".")
        return f"{sign}€{body} bn"
    if a >= 1e6:
        return f"{sign}€{a / 1e6:,.0f} m"
    if a >= 1e3:
        return f"{sign}€{a / 1e3:.0f} k"
    return f"{sign}€{a:.0f}"


def pct(fraction: Optional[float], signed: bool = False, digits: int = 1) -> Optional[str]:
    """0.0862 -> 8.6% (signed: +8.6%)."""
    if fraction is None:
        return None
    v = fraction * 100
    body = f"{abs(v):.{digits}f}".rstrip("0").rstrip(".") if digits else f"{abs(v):.0f}"
    sign = (MINUS if v < 0 else "+") if signed else (MINUS if v < 0 else "")
    return f"{sign}{body}%"


def short(name: Optional[str], limit: int = 60) -> str:
    """A product or partner name cut to fit a label: the first clause, then hard-cut."""
    name = re.sub(r"\s*\(\d{4}-\d{4}\).*$", "", name or "").strip()
    name = re.split(r"[;,(]", name)[0].strip() or name
    if len(name) <= limit:
        return name
    cut = name[: limit - 1].rsplit(" ", 1)[0]
    cut = re.sub(r"\s+(and|or|of|for|the|in|with|to|a|an)$", "", cut.rstrip())
    return cut + "…"


def sentence(name: str) -> str:
    """'Smartphones for wireless networks' -> 'smartphones for wireless networks'; acronyms stay."""
    if len(name) > 1 and name[1].isupper():
        return name
    return name[:1].lower() + name[1:]


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def period_words(period: str) -> str:
    """'2020-03' -> 'March 2020'; '2020' -> '2020'."""
    period = str(period)
    if len(period) == 7 and period[4] == "-":
        return f"{MONTHS[int(period[5:]) - 1]} {period[:4]}"
    return period


def ordinal(n: int) -> str:
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def value(v, format: Optional[str] = None) -> Optional[str]:
    """A fact value written as the page writes it (docs/agents.md, Format)."""
    if v is None:
        return None
    if format in (None, "text", "period"):
        return period_words(v) if format == "period" else str(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if format == "eur":
        return eur(x)
    if format == "eur_signed":
        return eur(x, signed=True)
    if format == "pct":
        return pct(x)
    if format == "pct_signed":
        return pct(x, signed=True)
    if format == "percent":
        return pct(x / 100)
    if format == "percent_signed":
        return pct(x / 100, signed=True)
    if format == "int" or format == "qty":
        return f"{x:,.0f}"
    if format == "tonnes":                   # contract 1.8: a weight, 122,464 t
        return f"{x:,.0f} t"
    if str(format).startswith("qty:"):       # a count and its unit: 37,654 cars
        return f"{x:,.0f} {str(format)[4:]}"
    if format == "ordinal":
        return ordinal(round(x))
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def times(fraction: float, base: Optional[str] = None) -> str:
    """A very large growth as a multiple: +2024% -> '×21.2', or with the base period '21.2 times the 2024 level'.
    Never 'N times higher': statisticians read that as N + 1 times the base."""
    m = 1 + fraction
    return f"{m:,.1f} times the {base} level" if base else f"×{m:,.1f}"


def growth(fraction: Optional[float], signed: bool = True, base: Optional[str] = None) -> Optional[str]:
    """A growth rate: '+8.6%', or from +300% up a multiple ('×21.2', '21.2 times the 2024 level')."""
    if fraction is not None and fraction >= 3:
        return times(fraction, base)
    return pct(fraction, signed=signed)


_BIG_PCT_RE = re.compile(r"(?:\b(?:up|by|an?\s+(?:increase|rise|jump)\s+of)\s+)?\(?\+(\d{3,}(?:[.,]\d+)?)\s?%\)?")


# 'N times higher (than in 2024)': read as N + 1 times by statisticians; the base year, when said, stays
_TIMES_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:times|x|×)\s+(?:higher|larger|bigger|greater|more)"
                       r"(?:\s+than(?:\s+(?:in|its|their|the))*\s+((?:19|20)\d\d)(?:\s+level)?)?", re.IGNORECASE)
# a verb that says the direction, then a sign that says it again: 'fell −17.4%', 'fell by −3.5%', 'rose +8.6%'
_DOWN_WORDS = (r"fell|falls?|falling|dropped|drops?|dropping|declined|declines?|declining|decreased|decreases?|"
               r"decreasing|shrank|shrunk|shrinks?|shrinking|slipped|contracted|lost|down|"
               r"(?:an?\s+)?(?:fall|drop|decline|decrease|loss|contraction)\s+of")
_UP_WORDS = (r"rose|rises?|rising|risen|grew|grows?|growing|grown|increased|increases?|increasing|climbed|climbs?|"
             r"jumped|jumps?|gained|gains?|up|(?:an?\s+)?(?:rise|increase|gain|growth|jump)\s+of")
_DOUBLE_SIGN_RE = re.compile(rf"\b(?:(?P<down>{_DOWN_WORDS})(?:\s+by)?\s+[−-]|(?P<up>{_UP_WORDS})(?:\s+by)?\s+\+)"
                             r"\s?(?=[€\d])", re.IGNORECASE)


def no_times_higher(text: str) -> str:
    """'21 times higher than in 2024' -> '21 times the 2024 level'; 'grew 6.1 times higher' -> 'grew ×6.1'."""
    def fix(m: re.Match) -> str:
        n = m.group(1).replace(",", ".")
        return f"{n} times the {m.group(2)} level" if m.group(2) else f"×{n}"
    return _TIMES_RE.sub(fix, text or "")


def no_double_signs(text: str) -> str:
    """'fell −17.4%' / 'fell by −3.5%' / 'rose +8.6%' / 'grew by +2.9%' -> 'fell 17.4%' / 'fell 3.5%' /
    'rose 8.6%' / 'grew 2.9%': the verb says the direction once."""
    def fix(m: re.Match) -> str:
        return (m.group("down") or m.group("up")) + " "
    return _DOUBLE_SIGN_RE.sub(fix, text or "")


def as_multiples(text: str) -> str:
    """'+2024%' -> '×21.2', '(+1423.8%)' -> '(×15.2)': from +300% up, growth as a multiple."""
    def fix(m):
        v = float(m.group(1).replace(",", ".")) / 100
        if v < 3:
            return m.group(0)
        whole = m.group(0)
        out = times(v)
        return f"({out})" if whole.startswith("(") or (whole.endswith(")") and "(" in whole) else out
    return _BIG_PCT_RE.sub(fix, text or "")


_LONG_UNIT_RE = re.compile(r"(€\s?\d+(?:[.,]\d+)*)\s?(billion|million|thousand)\b", re.IGNORECASE)
_NO_THOUSANDS_RE = re.compile(r"(€\s?)(\d{4,})((?:\.\d+)?\s?(?:bn|m|k)\b)")
_PERIOD_RE = re.compile(r"\b((?:19|20)\d\d)-(0[1-9]|1[0-2])\b(?![-/]\d)")


def thousands(text: str) -> str:
    """'€1563.4 bn' -> '€1,563.4 bn': amounts of four digits and more get their thousands separator."""
    return _NO_THOUSANDS_RE.sub(lambda m: f"{m.group(1)}{int(m.group(2)):,}{m.group(3)}", text or "")


def months_in_words(text: str) -> str:
    """'the 2021-12 break' -> 'the December 2021 break'. A span of years ('2011-12', 2011 to 2012) stays."""
    def fix(m: re.Match) -> str:
        year, month = m.group(1), m.group(2)
        if int(month) == (int(year) + 1) % 100:   # '2011-12', '2009-10': a span, not a month
            return m.group(0)
        return period_words(f"{year}-{month}")
    return _PERIOD_RE.sub(fix, text or "")


def house_style(text: str) -> str:
    """'€1.5 billion' -> '€1.5 bn', '€613 million' -> '€613 m', as the page writes amounts; ratios never 'N times
    higher', no double signs ('fell −17.4%', 'rose +8.6%'), thousands separators, months in words."""
    unit = {"billion": "bn", "million": "m", "thousand": "k"}
    out = as_multiples(_LONG_UNIT_RE.sub(lambda m: f"{m.group(1)} {unit[m.group(2).lower()]}", text or ""))
    return months_in_words(thousands(no_double_signs(no_times_higher(out))))


_CAUSAL = [(r"\bled to\b", "came with"), (r"\bleads to\b", "comes with"), (r"\blead to\b", "come with"),
           (r"\bcaused\b", "came with"), (r"\bcauses\b", "comes with"), (r"\bdue to\b", "alongside"),
           (r"\bbecause of\b", "alongside"), (r"\bresulted in\b", "came with"), (r"\btriggered\b", "came with")]


def no_causal_claims(text: str) -> str:
    """'The pandemic led to a drop' -> 'The pandemic came with a drop': the data shows dates that line up, not
    causes. The closing 'not proof of cause' sentence stays."""
    out = text or ""
    for pattern, plain in _CAUSAL:
        out = re.sub(pattern, lambda m, p=plain: p[:1].upper() + p[1:] if m.group(0)[:1].isupper() else p, out,
                     flags=re.IGNORECASE)
    return out


# ---------------------------------------------------------------- reading figures back from a reply
_EUR_RE = re.compile(r"([+\-−]?)\s?€\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)\s?"
                     r"(bn|billion|m|million|k|thousand)?\b", re.IGNORECASE)
_THOUSANDS_DIGITS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_PCT_RE = re.compile(r"([+\-−]?)(\d+(?:[.,]\d+)?)\s?%")
_SCALE = {"bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3, None: 1.0}


def figures_in(text: str) -> list[tuple[str, str, float]]:
    """The amounts and percentages written in a text: [(as written, 'eur'|'pct', value)].
    Amounts in euros, percentages as fractions."""
    out = []
    for m in _EUR_RE.finditer(text):
        unit = (m.group(3) or "").lower() or None
        digits = m.group(2)
        number = float(digits.replace(",", "")) if _THOUSANDS_DIGITS_RE.fullmatch(digits) \
            else float(digits.replace(",", "."))
        out.append((m.group(0).strip(), "eur", number * _SCALE[unit]))
    for m in _PCT_RE.finditer(text):
        out.append((m.group(0).strip(), "pct", float(m.group(2).replace(",", ".")) / 100))
    return out


def written_like(value: float, kind: str, seen: float) -> bool:
    """True when a figure written in a reply could be ``seen`` (a number a tool returned), rounded."""
    if kind == "eur":
        a = abs(seen)
        if a == 0:
            return value == 0
        # the reply may round more coarsely than eur() does: allow half a unit of the last digit
        unit = 1e9 if a >= 1e9 else 1e6 if a >= 1e6 else 1e3
        return abs(abs(value) - a) <= max(0.051 * unit, 0.006 * a)
    # percentages: a fraction (0.086), or a value already in percent (8.6)
    for cand in (abs(seen), abs(seen) / 100):
        if abs(abs(value) - cand) <= 0.0051 + 0.01 * cand:
            return True
    return False
