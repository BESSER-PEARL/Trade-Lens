// Number formats of the charts. Euros use the page's own formatter (../facts), so a chart and
// a fact never disagree: 25097941118 -> €25.1 bn, 613389780 -> €613 m.

import { NBSP, formatEur } from "../facts";

const MINUS = "−";

export function fmtEur(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return formatEur(v, signed);
}

/** A fraction (0.875) as a percentage, with the precision small shares need. */
export function fmtPct(s: number | null | undefined, signed = false): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return "–";
  const p = s * 100;
  const sign = p < 0 ? MINUS : signed && p > 0 ? "+" : "";
  const a = Math.abs(p);
  if (a === 0) return "0%";
  if (a >= 10) return `${sign}${a.toFixed(0)}%`;
  if (a >= 1) return `${sign}${a.toFixed(1)}%`;
  if (a >= 0.01) return `${sign}${a.toFixed(2)}%`;
  return `${sign}${a.toPrecision(2)}%`;
}

/** A value already in percent (5.1) with its sign. */
export function fmtPercent(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return fmtPct(v / 100, signed);
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  const s = Math.abs(v).toLocaleString("en", { maximumFractionDigits: digits });
  return (v < 0 ? MINUS : "") + s;
}

export function fmtOrdinal(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  const i = Math.round(n);
  const mod100 = i % 100;
  const suffix = mod100 >= 11 && mod100 <= 13 ? "th" : ["th", "st", "nd", "rd"][i % 10] || "th";
  return `${i}${suffix}`;
}

/** Clean axis ticks for a euro axis: 0, €5 bn, €10 bn (the unit on every tick: no axis title needed). */
export function fmtEurTick(v: number): string {
  if (v === 0) return "0";
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : "";
  const trim = (x: number) => String(Math.round(x * 100) / 100);
  if (a >= 1e9) return `${sign}€${trim(a / 1e9)}${NBSP}bn`;
  if (a >= 1e6) return `${sign}€${trim(a / 1e6)}${NBSP}m`;
  if (a >= 1e3) return `${sign}€${trim(a / 1e3)}${NBSP}k`;
  return `${sign}€${trim(a)}`;
}

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** Round "nice" ticks between min and max, about n of them. */
export function niceTicks(min: number, max: number, n = 5): number[] {
  if (!(max > min)) return [min];
  const span = max - min;
  const raw = span / Math.max(1, n);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9 * step; t += step) out.push(+t.toFixed(10));
  return out;
}

/** "Nuclear reactors, boilers, machinery ...; parts thereof" -> the part before the first semicolon. */
export function shortName(name: string | undefined | null): string {
  const s = String(name || "");
  const cut = s.indexOf(";");
  const t = (cut > 0 ? s.slice(0, cut) : s).split(" (")[0];
  return t.trim();
}

/** Product codes go in front of their name (85 Electrical machinery); partner codes do not. */
/** Short HS chapter names, for lists where the official wording is a paragraph. */
export const CHAPTER_SHORT: Record<string, string> = {
  "02": "Meat",
  "04": "Dairy, eggs and honey",
  "22": "Beverages",
  "24": "Tobacco",
  "27": "Mineral fuels and oils",
  "29": "Organic chemicals",
  "30": "Pharmaceuticals",
  "33": "Perfumes and cosmetics",
  "38": "Chemical products",
  "39": "Plastics",
  "40": "Rubber",
  "44": "Wood",
  "48": "Paper and paperboard",
  "56": "Wadding, felt and nonwovens",
  "59": "Coated textile fabrics",
  "64": "Footwear",
  "70": "Glass",
  "71": "Pearls, stones and precious metals",
  "72": "Iron and steel",
  "73": "Articles of iron or steel",
  "76": "Aluminium",
  "84": "Machinery",
  "85": "Electrical machinery",
  "87": "Vehicles and parts",
  "88": "Aircraft",
  "90": "Optical and medical instruments",
  "94": "Furniture",
};

/** "85 Electrical machinery"; with showCodes false (a reader whose profile hides codes) "Electrical machinery". */
export function itemLabel(code: string, name: string | undefined | null, showCodes = true): string {
  const n = CHAPTER_SHORT[code] || shortName(name) || code;
  return showCodes && /^\d{2,8}$/.test(code) && n !== code ? `${code} ${n}` : n;
}

export function truncate(s: string, n: number): string {
  if (n <= 1) return "";
  return s.length > n ? `${s.slice(0, Math.max(1, n - 1))}…` : s;
}

/** 2025-03 -> March 2025, 2025 -> 2025. */
export function periodLabel(p: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(String(p));
  return m ? `${MONTHS_LONG[parseInt(m[2], 10) - 1]} ${m[1]}` : String(p);
}
