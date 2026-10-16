// SPDX-License-Identifier: MIT
// Number and period formats of the Trends page, in the house style of the Overview page:
// €1.49 bn, €613 m, +8.6%, a true minus sign, and months written out (Jun 2026).

export const MINUS = "−";

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

function trimZeros(s: string): string {
  return s.indexOf(".") >= 0 ? s.replace(/\.?0+$/, "") : s;
}

/** Three significant figures, trailing zeros dropped: 25.1, 8.84, 613. */
function sig3(a: number): string {
  return trimZeros(a.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2));
}

/** €25.1 bn, €613 m, €42 k; any other unit symbol is used the same way. */
export function money(value: number | null | undefined, unit = "€", signed = false): string {
  if (!finite(value)) return "–";
  const a = Math.abs(value);
  let body: string;
  if (a >= 1e9) body = `${unit}${sig3(a / 1e9)} bn`;
  else if (a >= 1e6) body = `${unit}${sig3(a / 1e6)} m`;
  else if (a >= 1e3) body = `${unit}${sig3(a / 1e3)} k`;
  else body = `${unit}${Math.round(a).toLocaleString("en-US")}`;
  if (value < 0) return MINUS + body;
  return signed && value > 0 ? "+" + body : body;
}

/** Axis tick: as short as the step allows, always with its unit (€1.5 bn, €500 m). */
export function moneyTick(value: number, step: number, unit = "€"): string {
  const a = Math.abs(value);
  const sign = value < 0 ? MINUS : "";
  if (a === 0) return `${unit}0`;
  const scale = Math.max(a, step) >= 1e9 ? 1e9 : Math.max(a, step) >= 1e6 ? 1e6 : Math.max(a, step) >= 1e3 ? 1e3 : 1;
  const suffix = scale === 1e9 ? " bn" : scale === 1e6 ? " m" : scale === 1e3 ? " k" : "";
  // As many decimals as the step needs (0.25 bn steps keep two: €1.25 bn, not €1.3 bn).
  const unitStep = step / scale;
  let decimals = 0;
  while (decimals < 3 && Math.abs(Math.round(unitStep * Math.pow(10, decimals)) - unitStep * Math.pow(10, decimals)) > 1e-6) decimals++;
  return `${sign}${unit}${trimZeros((a / scale).toFixed(decimals))}${suffix}`;
}

/** A fraction as a percentage: 0.123 -> 12.3%. */
export const percent = (value: number | null | undefined, digits = 1): string =>
  finite(value) ? `${value < 0 ? MINUS : ""}${Math.abs(value * 100).toFixed(digits)}%` : "–";

/** A fraction as a signed percentage: 0.086 -> +8.6%, -0.02 -> −2.0%. */
export const signedPercent = (value: number | null | undefined, digits = 1): string => {
  if (!finite(value)) return "–";
  const body = `${Math.abs(value * 100).toFixed(digits)}%`;
  return value > 0 ? `+${body}` : value < 0 ? `${MINUS}${body}` : body;
};

/** A value already in percent, signed: 5.1 -> +5.1%. */
export const signedPct = (value: number, digits = 1): string => {
  const body = Math.abs(value).toFixed(digits);
  if (Number(body) === 0) return `${body}%`;
  return `${value > 0 ? "+" : MINUS}${body}%`;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** 2026-06 -> Jun 2026 (or June 2026 with long); 2026-Q2 -> Q2 2026; 2025 -> 2025. */
export function periodLabel(p: string | undefined, long = false): string {
  if (!p) return "";
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  if (m) {
    const i = parseInt(m[2], 10) - 1;
    return `${(long ? MONTHS_LONG : MONTHS)[i] ?? m[2]} ${m[1]}`;
  }
  const q = /^(\d{4})-?Q([1-4])$/i.exec(p);
  if (q) return `Q${q[2]} ${q[1]}`;
  return p;
}

/** An ALL-CAPS label from the source in sentence case, codes kept: "IRON AND STEEL" -> "Iron and steel". */
export function sentenceCase(label: string): string {
  if (!label || /[a-z]/.test(label)) return label;
  const lower = label.toLowerCase();
  return (lower.charAt(0).toUpperCase() + lower.slice(1)).replace(/\b(hs[246]?|cn8?|eu27?|uk|usa?)\b/gi, (w) => w.toUpperCase());
}
