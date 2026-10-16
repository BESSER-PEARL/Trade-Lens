// Comparisons (contract 1.2): the pure transforms behind ComparePanel, shared by the compare
// panel and by line and bar panels that carry comparison options. No React here.
//
// Two layouts, the same rules as compare_layout() in app/backend/view/validate.py:
// - "series": 1 to 6 series over time (/insights/series), drawn in options.compare mode:
//   absolute | index (100 at base_period) | share (of reference, or of the sum) | yoy | diff (minus reference);
//   options.facet draws small multiples, options.align "month" lays years over Jan..Dec, options.balance
//   shades exports against imports.
// - "pair": two snapshots keyed a and b (/insights/ranking or /insights/compare), side by side, with the
//   change per row: absolute | share | yoy (growth a to b) | diff (b minus a).
//
// Every figure comes from the API rows; the transforms only rebase, divide and subtract.

import { periodToX } from "./marks";
import { TOKENS } from "./tokens";
import type { Panel, Query } from "./types";

export type CompareMode = "absolute" | "index" | "share" | "yoy" | "diff";
export type CompareLayout = "series" | "pair";

export const COMPARE_MODES: CompareMode[] = ["absolute", "index", "share", "yoy", "diff"];
export const MAX_COMPARE_SERIES = 6;
const LINE_OPTIONS = ["compare", "base_period", "reference", "focus", "facet", "align", "balance"];
const BAR_OPTIONS = ["compare", "sort", "focus"];
const SERIES = "/insights/series";

export interface CompareOptions {
  mode: CompareMode;
  basePeriod?: string;
  reference?: string;
  focus?: string;
  facet: boolean;
  align: "time" | "month";
  balance: boolean;
  sort: "b" | "a" | "delta";
  limit: number;
}

/** True when the panel is drawn by ComparePanel: a compare panel, or a line or bar with comparison options. */
export function isComparison(panel: Pick<Panel, "type" | "options">): boolean {
  const o = panel.options || {};
  if (panel.type === "compare") return true;
  if (panel.type === "line") return LINE_OPTIONS.some((k) => o[k] !== undefined);
  if (panel.type === "bar") return BAR_OPTIONS.some((k) => o[k] !== undefined);
  return false;
}

/** "series" (lines over time), "pair" (two snapshots a and b) or null. */
export function compareLayout(panel: Pick<Panel, "type" | "queries">): CompareLayout | null {
  const qs = panel.queries || [];
  const keys = qs.map((q) => q.key || "").sort();
  const pair = keys.length === 2 && keys[0] === "a" && keys[1] === "b";
  if (panel.type === "line") return "series";
  if (panel.type === "bar") return pair ? "pair" : null;
  if (qs.length && qs.every((q) => q.endpoint === SERIES)) return "series";
  return pair ? "pair" : null;
}

export function readOptions(panel: Pick<Panel, "options">): CompareOptions {
  const o = panel.options || {};
  const mode = COMPARE_MODES.indexOf(o.compare as CompareMode) >= 0 ? (o.compare as CompareMode) : "absolute";
  const sort = o.sort === "a" || o.sort === "delta" ? o.sort : "b";
  return {
    mode,
    basePeriod: typeof o.base_period === "string" ? o.base_period : undefined,
    reference: typeof o.reference === "string" ? o.reference : undefined,
    focus: typeof o.focus === "string" ? o.focus : undefined,
    facet: o.facet === true,
    align: o.align === "month" ? "month" : "time",
    balance: o.balance === true,
    sort,
    limit: typeof o.limit === "number" && o.limit > 0 ? Math.min(30, Math.round(o.limit)) : 8,
  };
}

// ------------------------------------------------------------------------------------------ series

export interface RawPoint {
  period: string;
  value: number;
}

export interface Point {
  period: string;
  /** Position on the x axis: a year (2018.42 for 2018-06), or a month 1..12 when aligned by month. */
  x: number;
  /** The API's value. */
  raw: number;
  /** The value in the comparison's mode (index, fraction, growth, difference). */
  value: number;
}

export interface SeriesIn {
  key: string;
  label: string;
  /** exports, imports, or the flow of the query when no tone is set. */
  tone?: string;
  rows: RawPoint[];
}

export interface SeriesOut {
  key: string;
  label: string;
  tone?: string;
  points: Point[];
}

/** The unit of the values after a transform: what the axis and the readout format. */
export type Unit = "eur" | "eur_signed" | "index" | "fraction" | "growth";

export interface Transformed {
  series: SeriesOut[];
  unit: Unit;
  /** index: the period at 100 (or "first" when each series starts at its own first period). */
  base?: string;
  /** share / diff: the series used as denominator or subtracted. */
  reference?: SeriesIn;
  /** A rule to draw: 100 for an index, 0 for growth and differences. */
  rule?: number;
  /** Why the transform could not be made, for the panel to say. */
  problem?: string;
}

/** Rows of a series-like response: an array, or {rows: [...]}. */
export function seriesRows(data: any): RawPoint[] {
  const rows = Array.isArray(data) ? data : data && Array.isArray(data.rows) ? data.rows : [];
  return rows
    .filter((r: any) => r && typeof r.value === "number" && Number.isFinite(r.value) && r.period !== undefined)
    .map((r: any) => ({ period: String(r.period), value: r.value as number }));
}

export function xOf(period: string, align: "time" | "month"): number | undefined {
  if (align === "month") {
    const m = /^\d{4}-(\d{2})$/.exec(period);
    return m ? parseInt(m[1], 10) : undefined;
  }
  return periodToX(period);
}

/** The same period a year earlier: 2025 -> 2024, 2025-03 -> 2024-03. */
export function yearBefore(period: string): string {
  const m = /^(\d{4})(.*)$/.exec(period);
  return m ? `${String(parseInt(m[1], 10) - 1).padStart(4, "0")}${m[2]}` : period;
}

function keyOfX(x: number): string {
  return x.toFixed(4);
}

/** The series in the comparison's mode. The reference (share, diff) is taken out of the drawn series. */
export function transformSeries(input: SeriesIn[], opts: CompareOptions): Transformed {
  const align = opts.align;
  const pts = (s: SeriesIn) =>
    s.rows
      .map((r) => ({ period: r.period, x: xOf(r.period, align), raw: r.value }))
      .filter((p): p is { period: string; x: number; raw: number } => p.x !== undefined)
      .sort((a, b) => a.x - b.x);
  const ref = opts.reference ? input.find((s) => s.key === opts.reference) : undefined;
  const drawn = (opts.mode === "share" || opts.mode === "diff") && ref ? input.filter((s) => s !== ref) : input;

  switch (opts.mode) {
    case "index": {
      // One base for all when every series has it (the base_period, else the first period they share);
      // months of different years (align month) each start at their own first month.
      let base: string | undefined;
      if (align !== "month") {
        const sets = input.map((s) => new Set(s.rows.filter((r) => r.value !== 0).map((r) => r.period)));
        const common = (input[0]?.rows || []).map((r) => r.period).filter((p) => sets.every((st) => st.has(p))).sort();
        base = opts.basePeriod && sets.every((st) => st.has(opts.basePeriod as string)) ? opts.basePeriod : common[0];
      }
      const series = drawn.map((s) => {
        const ps = pts(s);
        const b = base !== undefined ? ps.find((p) => p.period === base) : ps.find((p) => p.raw !== 0);
        const bv = b && b.raw !== 0 ? b.raw : undefined;
        return { key: s.key, label: s.label, tone: s.tone, points: bv === undefined ? [] : ps.map((p) => ({ ...p, value: (p.raw / bv) * 100 })) };
      });
      const problem = base === undefined && align !== "month" ? "The series share no period to rebase on." : undefined;
      return { series, unit: "index", base: base || "first", rule: 100, problem };
    }
    case "share": {
      const den: Record<string, number> = {};
      if (ref) pts(ref).forEach((p) => (den[keyOfX(p.x)] = p.raw));
      else {
        // The sum of the series shown, at the periods where every series has a value.
        const counts: Record<string, number> = {};
        drawn.forEach((s) =>
          pts(s).forEach((p) => {
            const k = keyOfX(p.x);
            den[k] = (den[k] || 0) + p.raw;
            counts[k] = (counts[k] || 0) + 1;
          }),
        );
        Object.keys(counts).forEach((k) => {
          if (counts[k] < drawn.length) delete den[k];
        });
      }
      const series = drawn.map((s) => ({
        key: s.key,
        label: s.label,
        tone: s.tone,
        points: pts(s)
          .filter((p) => den[keyOfX(p.x)])
          .map((p) => ({ ...p, value: p.raw / den[keyOfX(p.x)] })),
      }));
      return { series, unit: "fraction", reference: ref };
    }
    case "yoy": {
      const series = drawn.map((s) => {
        const byPeriod: Record<string, number> = {};
        s.rows.forEach((r) => (byPeriod[r.period] = r.value));
        return {
          key: s.key,
          label: s.label,
          tone: s.tone,
          points: pts(s)
            .filter((p) => byPeriod[yearBefore(p.period)])
            .map((p) => ({ ...p, value: p.raw / byPeriod[yearBefore(p.period)] - 1 })),
        };
      });
      const empty = series.every((s) => s.points.length === 0);
      return { series, unit: "growth", rule: 0, problem: empty ? "Growth needs the year before each period; widen start by a year." : undefined };
    }
    case "diff": {
      if (!ref) return { series: [], unit: "eur_signed", problem: "A difference needs options.reference, the series to subtract." };
      const rv: Record<string, number> = {};
      pts(ref).forEach((p) => (rv[keyOfX(p.x)] = p.raw));
      const series = drawn.map((s) => ({
        key: s.key,
        label: s.label,
        tone: s.tone,
        points: pts(s)
          .filter((p) => rv[keyOfX(p.x)] !== undefined)
          .map((p) => ({ ...p, value: p.raw - rv[keyOfX(p.x)] })),
      }));
      return { series, unit: "eur_signed", reference: ref, rule: 0 };
    }
    default:
      return { series: drawn.map((s) => ({ key: s.key, label: s.label, tone: s.tone, points: pts(s).map((p) => ({ ...p, value: p.raw })) })), unit: "eur" };
  }
}

export interface BalancePoint {
  x: number;
  period: string;
  exports: number;
  imports: number;
  balance: number;
}

/** Exports minus imports at every period both have (options.balance). */
export function balanceOf(input: SeriesIn[], flows: Record<string, string>, align: "time" | "month"): BalancePoint[] {
  const ex = input.find((s) => flows[s.key] === "EXPORT");
  const im = input.find((s) => flows[s.key] === "IMPORT");
  if (!ex || !im) return [];
  const imp: Record<string, number> = {};
  im.rows.forEach((r) => (imp[r.period] = r.value));
  return ex.rows
    .filter((r) => imp[r.period] !== undefined)
    .map((r) => ({ x: xOf(r.period, align) as number, period: r.period, exports: r.value, imports: imp[r.period], balance: r.value - imp[r.period] }))
    .filter((b) => b.x !== undefined)
    .sort((a, b) => a.x - b.x);
}

export interface Readout {
  key: string;
  label: string;
  /** The last period with a value. */
  period?: string;
  /** The last value, in the mode's unit. */
  value?: number;
  /** The change the readout states: growth since the first point (absolute), points above 100 as a growth
   * (index), change in share (fraction, in points), the last growth (yoy), the change of the gap (diff). */
  delta?: number;
  /** points: against the previous period ("+41 pts on 2024"); points_since: since the first ("−22 pts since 2015"). */
  deltaUnit?: Unit | "points" | "points_since";
  /** The first period of the delta. */
  from?: string;
  /** The delta is against another series at the same point (months of two years), not since a period. */
  against?: boolean;
}

/** One readout per drawn series: where it ends, and how it moved. Months of different years laid over each
 * other (align month) compare the focus year's last month with the same month of the other year. */
export function readouts(t: Transformed, align: "time" | "month" = "time", focus?: string): Readout[] {
  if (align === "month" && t.unit === "eur" && t.series.length >= 2) {
    const f = t.series.find((s) => s.key === focus) || t.series[t.series.length - 1];
    const other = t.series.find((s) => s !== f);
    return t.series.map((s) => {
      const last = s.points[s.points.length - 1];
      const out: Readout = { key: s.key, label: s.label, period: last?.period, value: last?.value };
      if (s === f && last && other) {
        const same = other.points.find((p) => Math.abs(p.x - last.x) < 1e-6);
        if (same && same.raw) {
          out.delta = last.raw / same.raw - 1;
          out.deltaUnit = "growth";
          out.from = same.period;
          out.against = true;
        }
      }
      return out;
    });
  }
  return t.series.map((s) => {
    const last = s.points[s.points.length - 1];
    const first = s.points[0];
    const out: Readout = { key: s.key, label: s.label, period: last?.period, value: last?.value, from: first?.period };
    if (!last || !first) return out;
    switch (t.unit) {
      case "eur":
        out.delta = first.raw ? last.raw / first.raw - 1 : undefined;
        out.deltaUnit = "growth";
        break;
      case "index":
        out.delta = last.value / 100 - 1;
        out.deltaUnit = "growth";
        out.from = t.base && t.base !== "first" ? t.base : first.period;
        break;
      case "fraction":
        out.delta = last.value - first.value;
        out.deltaUnit = "points_since";
        break;
      case "growth": {
        const prev = s.points[s.points.length - 2];
        out.delta = prev ? last.value - prev.value : undefined;
        out.deltaUnit = "points";
        out.from = prev?.period;
        break;
      }
      case "eur_signed":
        out.delta = last.value - first.value;
        out.deltaUnit = "eur_signed";
        break;
    }
    return out;
  });
}

// ------------------------------------------------------------------------------------------ pair

export interface PairRow {
  code: string;
  name: string;
  a?: number;
  b?: number;
  aShare?: number;
  bShare?: number;
  /** b minus a, in euros. */
  change?: number;
  /** b / a - 1. */
  growth?: number;
  /** The value drawn in the mode: b (absolute), b's share, growth or change. */
  value?: number;
  focus: boolean;
}

/** Items of a snapshot: /insights/ranking {items}, /insights/compare [...]. OTHER and totals are left out. */
export function snapshotItems(data: any): Array<{ code: string; name: string; value: number; share?: number }> {
  const rows = Array.isArray(data) ? data : data && Array.isArray(data.items) ? data.items : [];
  return rows
    .filter((r: any) => r && typeof r.value === "number" && r.code !== undefined && String(r.code) !== "OTHER")
    .map((r: any) => ({
      code: String(r.code),
      name: String(r.name ?? r.code).replace(/\s*◀$/, ""),
      value: r.value as number,
      share: typeof r.share === "number" ? r.share : undefined,
    }));
}

/** Rows of two snapshots joined on code, sorted and cut to options.limit (the focus always stays). */
export function pairRows(aData: any, bData: any, opts: CompareOptions): PairRow[] {
  const a = snapshotItems(aData);
  const b = snapshotItems(bData);
  const byCode: Record<string, PairRow> = {};
  const order: string[] = [];
  const put = (code: string, name: string) => {
    if (!byCode[code]) {
      byCode[code] = { code, name, focus: !!opts.focus && code === opts.focus };
      order.push(code);
    }
    return byCode[code];
  };
  b.forEach((r) => {
    const row = put(r.code, r.name);
    row.b = r.value;
    row.bShare = r.share;
  });
  a.forEach((r) => {
    const row = put(r.code, r.name);
    row.a = r.value;
    row.aShare = r.share;
  });
  const rows = order.map((c) => {
    const r = byCode[c];
    if (r.a !== undefined && r.b !== undefined) {
      r.change = r.b - r.a;
      r.growth = r.a ? r.b / r.a - 1 : undefined;
    }
    r.value =
      opts.mode === "share" ? r.bShare : opts.mode === "yoy" ? r.growth : opts.mode === "diff" ? r.change : r.b;
    return r;
  });
  const by = (r: PairRow) =>
    opts.sort === "a" ? r.a ?? -Infinity : opts.sort === "delta" ? Math.abs(r.change ?? 0) : opts.mode === "yoy" ? r.growth ?? -Infinity : r.b ?? -Infinity;
  const sorted = rows.filter((r) => r.value !== undefined).sort((x, y) => by(y) - by(x));
  const top = sorted.slice(0, opts.limit);
  const focus = sorted.find((r) => r.focus);
  if (focus && top.indexOf(focus) < 0) top.push(focus);
  return top;
}

// ------------------------------------------------------------------------------------------ colours

/** Greys for the series that are not the focus, darkest first, each with its own dash. */
export const NEUTRALS: Array<{ color: string; text: string; dash: string }> = [
  { color: "#3B4858", text: "#3B4858", dash: "" },
  { color: "#8C98A5", text: "#5E6B7A", dash: "6 3" },
  { color: "#56636F", text: "#46525E", dash: "2 3" },
  { color: "#A9B3BE", text: "#5E6B7A", dash: "9 3 2 3" },
  { color: "#6B7784", text: "#56636F", dash: "4 4" },
];

export interface SeriesStyle {
  color: string;
  text: string;
  width: number;
  dash: string;
  focus: boolean;
}

/** The flow of a query (EXPORT, IMPORT), from its params. */
export function flowOf(q: Query | undefined): string | undefined {
  const f = q?.params?.flow;
  return typeof f === "string" ? f.toUpperCase() : undefined;
}

function dataColour(tone: string | undefined): { color: string; text: string } {
  return tone === "imports" ? { color: TOKENS.steel, text: TOKENS.steelText } : { color: TOKENS.sky, text: TOKENS.skyText };
}

/**
 * Data colours only: exports sky and imports steel when the series are one export and one import series
 * (or carry those two tones); otherwise the focus series in its data colour (sky, or steel for an import
 * series) and the others in neutral greys with dashes. Never an agent's pen.
 */
export function seriesStyles(series: Array<{ key: string; tone?: string }>, focus?: string): Record<string, SeriesStyle> {
  const out: Record<string, SeriesStyle> = {};
  const tones = series.map((s) => s.tone);
  const twoWay = series.length === 2 && tones.indexOf("exports") >= 0 && tones.indexOf("imports") >= 0;
  if (twoWay) {
    series.forEach((s) => {
      const c = dataColour(s.tone);
      out[s.key] = { ...c, width: s.tone === "exports" ? 3.5 : 2.5, dash: "", focus: s.tone === "exports" };
    });
    return out;
  }
  const focusKey = focus && series.some((s) => s.key === focus) ? focus : series[0]?.key;
  let n = 0;
  series.forEach((s) => {
    if (s.key === focusKey) {
      out[s.key] = { ...dataColour(s.tone), width: 3.5, dash: "", focus: true };
    } else {
      const g = NEUTRALS[n++ % NEUTRALS.length];
      out[s.key] = { color: g.color, text: g.text, width: 2, dash: g.dash, focus: false };
    }
  });
  return out;
}

// ------------------------------------------------------------------------------------------ formats

const MINUS = "−";

function trim(s: string): string {
  return s.indexOf(".") >= 0 ? s.replace(/\.?0+$/, "") : s;
}

/** A growth or share fraction as a percentage: 0.273 -> 27%, 0.042 -> 4.2%. */
export function pct(v: number | undefined, signed = false): string {
  if (v === undefined || !Number.isFinite(v)) return "–";
  const p = v * 100;
  const a = Math.abs(p);
  const body = a >= 10 ? a.toFixed(0) : a >= 1 ? trim(a.toFixed(1)) : a === 0 ? "0" : trim(a.toFixed(2));
  const sign = p < 0 ? MINUS : signed && p > 0 ? "+" : "";
  return `${sign}${body}%`;
}

/** Percentage points: 0.031 -> +3.1 pts. */
export function points(v: number | undefined): string {
  if (v === undefined || !Number.isFinite(v)) return "–";
  const p = v * 100;
  const a = Math.abs(p);
  const body = a >= 10 ? a.toFixed(0) : trim(a.toFixed(1));
  return `${p < 0 ? MINUS : p > 0 ? "+" : ""}${body} pts`;
}

/** An index value: 131.4 -> 131. */
export function indexText(v: number | undefined): string {
  if (v === undefined || !Number.isFinite(v)) return "–";
  return Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-US") : String(Math.round(v));
}

const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** An x position as an axis label: a year, or a month name when aligned by month. */
export function xLabel(x: number, align: "time" | "month"): string {
  if (align === "month") return MONTH_SHORT[Math.round(x) - 1] || "";
  return String(Math.floor(x + 1e-6));
}

/** Short words for the mode, for the table header and the screen reader. */
export function modeWords(t: Transformed, opts: CompareOptions): string {
  switch (opts.mode) {
    case "index":
      return t.base && t.base !== "first" ? `Index, ${t.base} = 100` : "Index, first period = 100";
    case "share":
      return t.reference ? `Share of ${t.reference.label}` : "Share of the series shown";
    case "yoy":
      return "Growth on the year before";
    case "diff":
      return t.reference ? `Difference from ${t.reference.label}` : "Difference";
    default:
      return "Value";
  }
}
