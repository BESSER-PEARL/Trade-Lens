// Line panels: one line per query (imports steel, exports sky), an optional
// light fill of the gap between two series, direct labels with the last value
// at each line end, a crosshair with a readout on hover, touch or the arrow
// keys, and the marks agents draw on time:
//   trend       fitted line from /insights/trend, dashed in the agent's pen
//   event       a dated rule (or a span with until), its label on a row at the top
//   forecast    hatched band from /insights/forecast after the last actual point
//   annotation  a dot on the panel's own point at x (y is read from the data)
//   highlight   a from/to span
//   trend_break a rule at `at` with the level before and after (from /trends/analyze)
// Queries and mark queries follow the page context ("$period" ...); a mark
// about another period than the page shows is dimmed with an "about ..." tag.

import React from "react";
import { ErrorCard, penFill, problemCard, spreadLabels, textWidth, useWidth } from "./common";
import { formatAxis, formatEur, formatPeriod, formatValue, tryPath } from "./facts";
import { periodToX, type DrawnMark } from "./marks";
import { FONT_UI, NUM_STYLE, TOKENS, toneColor } from "./tokens";
import { QueryState, firstProblem, useQueries } from "./useQuery";
import {
  OFF_CONTEXT_OPACITY,
  aboutTag,
  markOffContext,
  markPeriod,
  substituteQueries,
  substituteQuery,
  usePageContext,
} from "./ViewStore";
import { ComparePanel } from "./ComparePanel";
import { isComparison } from "./compare";
import { ADJUSTED_WORDS, RECORDED_WORDS, adjustable, adjustedQuery, setAdjustedShown, useSeasonallyAdjusted } from "./seasonal";
import type { Panel, Query } from "./types";

/** Rows of a series-like response: an array, or {rows: [...]}, or Ivan's {points: [...]}. */
function rowsOf(data: any): any[] {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.rows)) return data.rows;
  if (data && Array.isArray(data.points)) return data.points;
  return [];
}

/** Round ticks of 1, 2, 2.5 or 5 times a power of ten covering [lo, hi], about `target` steps. */
export function niceRange(lo: number, hi: number, target = 4): { ticks: number[]; lo: number; hi: number } {
  if (!(hi > lo)) {
    const pad = Math.abs(hi) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const raw = (hi - lo) / target;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) || 10 * pow;
  const a = Math.floor(lo / step + 1e-9) * step;
  const b = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let t = a; t <= b + step * 0.001; t += step) ticks.push(Math.round(t * 1e6) / 1e6);
  return { ticks, lo: a, hi: b };
}

/** Kept for other panels: ticks from 0 to max. */
export function niceTicks(max: number, target = 4): number[] {
  return niceRange(0, max, target).ticks;
}

function labelOfX(x: number, monthly: boolean): string {
  if (!monthly) return String(Math.round(x));
  const y = Math.floor(x + 1e-6);
  const m = Math.round((x - y) * 12) + 1;
  return formatPeriod(`${y}-${String(m).padStart(2, "0")}`);
}

function f(n: number): string {
  return n.toFixed(1);
}

export interface LinePanelProps {
  panel: Panel;
  marks?: DrawnMark[];
  height?: number;
}

interface Series {
  key: string;
  label: string;
  color: string;
  text: string;
  width: number;
}

interface Placed {
  d: DrawnMark;
  off: boolean;
  about?: string;
}

/** One scenario of a forecast at the band's end ("if the smartphone line stops"). */
interface Scenario {
  x: number;
  v: number;
  label: string;
}

/**
 * "If smartphones stop" from {line: "smartphones", case: "stops"}, else the scenario's own label. The
 * "continues" case holds the line at its level of the last three months; the central forecast reads the
 * line as part of the series, so it assumes the recent rise goes on and can sit above both.
 */
function scenarioLabel(s: any): string {
  const line = String(s?.line || "").replace(/_/g, " ");
  if (line && s?.case === "stops") return `If ${line} stop`;
  if (line && s?.case === "continues") return `If ${line} stay at their recent level`;
  return String(s?.label || s?.id || "Scenario");
}

/** A label in lines no wider than maxW, the value at the end of the last line when it fits there. */
function labelLines(name: string, value: string, maxW: number, size: number): string[] {
  const lines: string[] = [];
  let cur = "";
  name.split(" ").forEach((w) => {
    const next = cur ? `${cur} ${w}` : w;
    if (cur && textWidth(next, size, true) > maxW) {
      lines.push(cur);
      cur = w;
    } else cur = next;
  });
  if (cur) lines.push(cur);
  if (!value) return lines;
  const last = lines[lines.length - 1];
  if (last !== undefined && textWidth(`${last} ${value}`, size, true) <= maxW) lines[lines.length - 1] = `${last} ${value}`;
  else lines.push(value);
  return lines;
}

/** Centres for boxes of height h anchored at y, none overlapping, inside [min, max]. */
function spreadBoxes(boxes: Array<{ y: number; h: number }>, min: number, max: number, pad = 3): number[] {
  const idx = boxes.map((b, i) => ({ ...b, i })).sort((a, b) => a.y - b.y);
  const pos = idx.map((b) => b.y);
  for (let k = 0; k < 4; k++) {
    for (let j = 1; j < pos.length; j++) {
      const need = (idx[j - 1].h + idx[j].h) / 2 + pad;
      if (pos[j] - pos[j - 1] < need) pos[j] = pos[j - 1] + need;
    }
    const n = pos.length - 1;
    if (n >= 0 && pos[n] + idx[n].h / 2 > max) pos[n] = max - idx[n].h / 2;
    for (let j = n - 1; j >= 0; j--) {
      const need = (idx[j].h + idx[j + 1].h) / 2 + pad;
      if (pos[j + 1] - pos[j] < need) pos[j] = pos[j + 1] - need;
    }
    if (n >= 0 && pos[0] - idx[0].h / 2 < min) pos[0] = min + idx[0].h / 2;
  }
  const out: number[] = new Array(boxes.length);
  idx.forEach((b, j) => (out[b.i] = pos[j]));
  return out;
}

const MARK_QUERY_KINDS = ["trend", "forecast", "trend_break"];

/**
 * A line panel. With comparison options (contract 1.2: compare, base_period, reference, focus, facet,
 * align, balance) it is drawn by ComparePanel, the comparisons' renderer; otherwise as below.
 */
export const LinePanel: React.FC<LinePanelProps> = (props) =>
  isComparison(props.panel) ? <ComparePanel panel={props.panel} marks={props.marks} height={props.height} /> : <TimeLines {...props} />;

const TimeLines: React.FC<LinePanelProps> = ({ panel, marks = [], height }) => {
  const ctx = usePageContext();
  const xField = String(panel.options?.x || "period");
  const yField = String(panel.options?.y || "value");
  const format = String(panel.options?.format || "eur");
  // "As recorded" / "Seasonally adjusted": a display choice of the reader, not part of the page's state (no
  // op), shared with "How this was made" (seasonal.ts). The recorded values are always fetched and drawn (a
  // faint line under the adjusted one): the marks, the forecast and the notes are about them. The adjusted
  // values come from the same query with adjust=true; a series too short to adjust says so and stays as
  // recorded.
  const [adjusted, setAdjusted] = useSeasonallyAdjusted(panel.id);
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const adjQueries = React.useMemo(() => queries.map((q) => (adjusted ? adjustedQuery(q) : null)), [queries, adjusted]);
  const markQueries = marks.map((d) => (MARK_QUERY_KINDS.indexOf(String(d.mark.kind)) >= 0 && d.mark.query ? substituteQuery(d.mark.query as Query, ctx) : null));
  const states = useQueries(queries);
  const adjStates = useQueries(adjQueries);
  const markStates = useQueries(markQueries);
  const { ref, width } = useWidth<HTMLDivElement>(760);
  const [hover, setHover] = React.useState<number | null>(null);
  const [tipPos, setTipPos] = React.useState<{ x: number; y: number } | null>(null);
  // The event label shown in full (hovered), when the chart had to shorten it.
  const [openLabel, setOpenLabel] = React.useState<string | null>(null);
  const svgId = React.useId().replace(/[^a-zA-Z0-9_-]/g, "");

  // ---------------------------------------------------------------- the seasonal adjustment's state
  // off; loading; on (every line adjusted); refused (too short, or not monthly: a 422 that says why); failed.
  const adjAsked = adjQueries.map((q, i) => (q ? adjStates[i] : null)).filter((s): s is QueryState => !!s);
  const adjRefused = adjAsked.find((s) => s.error && s.error.status === 422);
  const adjFailed = adjAsked.find((s) => (s.error && s.error.status !== 422) || s.notLoaded);
  const adjState: "off" | "loading" | "on" | "refused" | "failed" = !adjusted || !adjAsked.length
    ? "off"
    : adjRefused
      ? "refused"
      : adjFailed
        ? "failed"
        : adjAsked.some((s) => s.loading)
          ? "loading"
          : "on";
  const showAdj = adjState === "on";
  // what is drawn, for "How this was made": seasonally adjusted only when the adjusted values are on screen
  React.useEffect(() => setAdjustedShown(panel.id, showAdj), [panel.id, showAdj]);
  React.useEffect(() => () => setAdjustedShown(panel.id, false), [panel.id]);

  const problem = firstProblem(states);
  if (problem) return problemCard(problem, height || 300);

  // ---------------------------------------------------------------- data rows by x
  // byX: the values drawn (adjusted when shown); recByX: the values as recorded (marks and forecasts read these).
  const byX: Record<string, any> = {};
  const recByX: Record<string, any> = {};
  const row = (x: number) => {
    const k = x.toFixed(4);
    if (!byX[k]) byX[k] = { x };
    return byX[k];
  };
  const seriesKeys = queries.map((q, i) => q.key || `s${i}`);
  const lastActual: Record<string, { x: number; v: number }> = {};
  const lastRecorded: Record<string, { x: number; v: number }> = {};
  let monthly = false;
  queries.forEach((q, i) => {
    const key = seriesKeys[i];
    const adjBy: Record<string, number> = {};
    if (showAdj && adjQueries[i])
      rowsOf(adjStates[i].data).forEach((r) => {
        const x = periodToX(r?.[xField]);
        if (x !== undefined && typeof r?.adjusted === "number") adjBy[x.toFixed(4)] = r.adjusted;
      });
    rowsOf(states[i].data).forEach((r) => {
      const p = r?.[xField];
      const x = periodToX(p);
      const v = r?.[yField];
      if (x === undefined || typeof v !== "number") return;
      if (/^\d{4}-\d{2}$/.test(String(p))) monthly = true;
      const k = x.toFixed(4);
      (recByX[k] = recByX[k] || { x })[key] = v;
      if (typeof r?.value_eur === "number") recByX[k][`${key}__eur`] = r.value_eur; // a quantity's value in euros (1.8)
      if (!lastRecorded[key] || x > lastRecorded[key].x) lastRecorded[key] = { x, v };
      const shown = showAdj && adjQueries[i] ? adjBy[k] : v;
      if (typeof shown !== "number") return;
      row(x)[key] = shown;
      if (!lastActual[key] || x > lastActual[key].x) lastActual[key] = { x, v: shown };
    });
  });
  const actualXs = Object.keys(byX).map((k) => byX[k].x as number).sort((a, b) => a - b);
  if (actualXs.length === 0) {
    return <ErrorCard state={states[0]} title="No data to draw" message={`The query returned no ${xField} and ${yField} values.`} />;
  }
  const panelFreq = String(queries[0]?.params?.freq || "A");
  // Monthly totals only (the backend refuses a series under five years, and says so).
  const canToggleAdjust = monthly && queries.length > 0 && queries.every((q) => adjustable(q));

  // ---------------------------------------------------------------- marks with data of their own
  const failures: Array<{ d: DrawnMark; state?: QueryState; reason?: string }> = [];
  const trendLines: Array<Placed & { pts: Array<[number, number]> }> = [];
  const bands: Array<Placed & { pts: Array<[number, number, number, number | undefined]>; lastX?: number; scen: Scenario[]; key: string; level?: number }> = [];
  const firstKey = seriesKeys[0];
  const placedOf = (d: DrawnMark): Placed => {
    const off = markOffContext(d.mark, ctx);
    return { d, off, about: off ? markPeriod(d.mark) : undefined };
  };

  marks.forEach((d, i) => {
    const m = d.mark;
    const seriesKey = m.series && seriesKeys.indexOf(m.series) >= 0 ? m.series : firstKey;
    if (m.kind === "trend") {
      const st = markStates[i];
      if (!st || !st.ready) return failures.push({ d, state: st });
      const pts: Array<[number, number]> = [];
      rowsOf(st.data).forEach((r) => {
        const x = periodToX(r?.period);
        if (x !== undefined && typeof r?.fitted === "number") pts.push([x, r.fitted]);
      });
      if (!pts.length) return failures.push({ d, reason: "the trend has no fitted values" });
      trendLines.push({ ...placedOf(d), pts });
    } else if (m.kind === "forecast") {
      const st = markStates[i];
      if (!st || !st.ready) return failures.push({ d, state: st });
      // a forecast is of the values as recorded: it starts from the last recorded month, adjusted or not
      const last = lastRecorded[seriesKey];
      const pts: Array<[number, number, number, number | undefined]> = [];
      // A yearly forecast (aggregate=A) on a monthly chart is drawn from its months (the response's "monthly").
      const bandRows = panelFreq === "M" && Array.isArray(st.data?.monthly) && st.data.monthly.length ? st.data.monthly : rowsOf(st.data);
      bandRows.forEach((r: any) => {
        const x = periodToX(r?.period);
        if (x === undefined || typeof r?.low !== "number" || typeof r?.high !== "number") return;
        if (last && x <= last.x + 1e-6) return;
        pts.push([x, r.low, r.high, typeof r.value === "number" ? r.value : undefined]);
      });
      if (!pts.length) return failures.push({ d, reason: "the forecast has no periods after the data" });
      // Scenarios (contract 1.3, params.scenarios): "the smartphone line stops / continues" at the band's end.
      const endX = pts[pts.length - 1][0];
      const scen: Scenario[] = [];
      if (m.params?.scenarios && Array.isArray(st.data?.scenarios)) {
        st.data.scenarios.forEach((s: any) => {
          const r = rowsOf(s).find((r: any) => periodToX(r?.period) !== undefined && Math.abs((periodToX(r.period) as number) - endX) < 1e-6);
          if (r && typeof r.value === "number") scen.push({ x: endX, v: r.value, label: scenarioLabel(s) });
        });
      }
      if (last) pts.unshift([last.x, last.v, last.v, last.v]);
      const level = Number(st.data?.interval ?? (m.query as Query | undefined)?.params?.interval);
      bands.push({ ...placedOf(d), pts, lastX: last?.x, scen, key: seriesKey, level: Number.isFinite(level) && level > 0 && level < 1 ? level : undefined });
    }
    return undefined;
  });

  // ---------------------------------------------------------------- scales
  const allX = actualXs.slice();
  bands.forEach((b) => b.pts.forEach((p) => allX.push(p[0])));
  trendLines.forEach((t) => t.pts.forEach((p) => allX.push(p[0])));
  const minX = Math.min.apply(null, allX);
  const maxX = Math.max.apply(null, allX);
  let yMin = Infinity;
  let yMax = -Infinity;
  const see = (v: any) => {
    if (typeof v === "number" && Number.isFinite(v)) {
      yMin = Math.min(yMin, v);
      yMax = Math.max(yMax, v);
    }
  };
  actualXs.forEach((x) => seriesKeys.forEach((k) => see(byX[x.toFixed(4)][k])));
  if (showAdj) Object.keys(recByX).forEach((k) => seriesKeys.forEach((key) => see(recByX[k][key])));
  bands.forEach((b) => b.pts.forEach((p) => (see(p[1]), see(p[2]))));
  bands.forEach((b) => b.scen.forEach((s) => see(s.v)));
  trendLines.forEach((t) => t.pts.forEach((p) => see(p[1])));
  if (yMin > 0 && yMin < (yMax - yMin) * 0.35) yMin = 0; // close to zero: show zero
  // The same ticks on a phone and a desktop: an axis that starts at 5 on one and 0 on the other reads as two charts.
  const yr = niceRange(yMin, yMax, 4);

  const isEur = format === "eur" || format === "eur_signed" || format === "compact";
  // The axis unit follows the size of the values: a series of a few thousand euros reads "EUR thousand" with
  // ticks 0, 2, 4, never "EUR m" with ticks 0.01 and 0.
  const axisMax = Math.max(Math.abs(yr.hi), Math.abs(yr.lo));
  const [unitDiv, unitWord] = axisMax >= 1e9 ? [1e9, "EUR bn"] : axisMax >= 1e6 ? [1e6, "EUR m"] : axisMax >= 1e3 ? [1e3, "EUR thousand"] : [1, "EUR"];
  const unitLabel = isEur ? unitWord : "";
  const tickLabel = (v: number) => {
    if (isEur) return String(Math.round((v / unitDiv) * 100) / 100);
    return formatAxis(v, format);
  };
  const fmtValue = (v: number) => (isEur ? formatEur(v) : formatValue(v, format));

  // Series and their end labels.
  const series: Series[] = queries.map((q, i) => {
    const tone = toneColor(q.tone);
    return { key: seriesKeys[i], label: q.label || seriesKeys[i], color: tone.fill, text: tone.text, width: q.tone === "exports" ? 3.5 : 2.5 };
  });
  const narrow = width < 560;
  const endLabels = series
    .filter((s) => lastActual[s.key])
    .map((s) => ({ s, value: lastActual[s.key].v, x: lastActual[s.key].x, name: s.label, text: fmtValue(lastActual[s.key].v) }));
  const endW = Math.max(0, ...endLabels.map((e) => Math.max(textWidth(e.name, 13, true), textWidth(e.text, 13, true)) + (narrow ? 0 : textWidth(" ", 13))));
  // A forecast after the data gets a zone of its own at the right. The observed lines are labelled where they
  // end (the last year of data), inside that zone; the right column then holds the forecast's own labels:
  // the central figure and the scenarios, in the forecaster's ink.
  const actualMax = actualXs[actualXs.length - 1];
  const hasFuture = bands.length > 0 && maxX > actualMax + 1e-6 && actualMax > minX;
  const colMax = narrow ? 112 : 168;
  const colW = colMax - 12;
  type ColLabel = { b: (typeof bands)[number]; kind: "central" | "scenario"; x: number; v: number; lines: string[]; h: number };
  const colLabels: ColLabel[] = [];
  bands.forEach((b) => {
    const end = b.pts[b.pts.length - 1];
    if (typeof end[3] === "number") {
      const lines = labelLines(b.scen.length ? "Forecast, if the recent rise goes on" : "Forecast", fmtValue(end[3]), colW, 12.5);
      colLabels.push({ b, kind: "central", x: end[0], v: end[3], lines, h: lines.length * 14 });
    }
    b.scen.forEach((s) => {
      const lines = labelLines(s.label, fmtValue(s.v), colW, 12);
      colLabels.push({ b, kind: "scenario", x: s.x, v: s.v, lines, h: lines.length * 14 });
    });
  });
  const colW_ = Math.max(0, ...colLabels.map((l) => Math.max(...l.lines.map((t) => textWidth(t, l.kind === "central" ? 12.5 : 12, true)))));
  const right = hasFuture
    ? Math.min(colMax, Math.ceil(colW_ + 14))
    : Math.min(narrow ? 92 : 132, Math.ceil(narrow ? endW + 10 : textWidth("Imports €25.1 bn", 13, true) + 14));

  // Event-like marks on label rows at the top.
  type Timed = Placed & { kind: "event" | "span" | "break"; x: number; x2?: number; label: string; row: number; flip?: boolean; short?: string };
  const timed: Timed[] = [];
  const points: Array<Placed & { x: number; y: number; label: string }> = [];
  const breaks: Array<Placed & { x: number; label: string; before?: number; after?: number; from: number; to: number }> = [];
  marks
    .slice()
    .sort((a, b) => (periodToX(a.mark.params?.at ?? a.mark.params?.from ?? a.mark.params?.x) ?? 0) - (periodToX(b.mark.params?.at ?? b.mark.params?.from ?? b.mark.params?.x) ?? 0))
    .forEach((d) => {
      const m = d.mark;
      const p = m.params || {};
      const seriesKey = m.series && seriesKeys.indexOf(m.series) >= 0 ? m.series : firstKey;
      if (m.kind === "event") {
        const x = periodToX(p.at);
        if (x === undefined) return failures.push({ d, reason: `bad period ${p.at}` });
        const x2 = periodToX(p.until);
        timed.push({ ...placedOf(d), kind: x2 !== undefined ? "span" : "event", x, x2, label: String(p.label || ""), row: 0 });
      } else if (m.kind === "highlight") {
        const x = periodToX(p.from);
        const x2 = periodToX(p.to);
        if (x === undefined || x2 === undefined) return failures.push({ d, reason: "a highlight on a line needs from and to" });
        timed.push({ ...placedOf(d), kind: "span", x, x2, label: String(p.label || ""), row: 0 });
      } else if (m.kind === "annotation") {
        const x = periodToX(p.x);
        // a note is about the value as recorded: its dot sits on the recorded line (faint when adjusted)
        const hit = x !== undefined ? recByX[x.toFixed(4)] : undefined;
        const y = hit ? hit[seriesKey] : undefined;
        if (x === undefined || typeof y !== "number") return failures.push({ d, reason: `no ${seriesKey} value at ${p.x}` });
        points.push({ ...placedOf(d), x, y, label: String(p.label || "") });
      } else if (m.kind === "trend_break") {
        const x = periodToX(p.at);
        if (x === undefined) return failures.push({ d, reason: `bad period ${p.at}` });
        const i = marks.indexOf(d);
        const st = markStates[i];
        // Levels: the facts named by before/after (query "self"), else the change point itself.
        const level = (name: string | undefined, field: "before_mean" | "after_mean"): number | undefined => {
          if (!st?.ready) return undefined;
          const fact: any = name ? (m.facts as any)?.[name] : undefined;
          if (fact && fact.query === "self" && fact.path) {
            const v = tryPath(st.data, fact.path);
            if (typeof v === "number") return v;
          }
          const v = tryPath(st.data, `changepoints.[period=${p.at}].${field}`);
          return typeof v === "number" ? v : undefined;
        };
        // Levels are drawn only when the analysis runs on the panel's own frequency.
        const sameFreq = String((m.query as Query | undefined)?.params?.freq || "A") === panelFreq;
        const unit = monthly ? 1 / 12 : 1;
        const from = periodToX(p.from) ?? x - 6 * unit;
        const to = periodToX(p.to) ?? x + 6 * unit;
        breaks.push({
          ...placedOf(d),
          x,
          label: String(p.label || ""),
          before: sameFreq ? level(p.before, "before_mean") : undefined,
          after: sameFreq ? level(p.after, "after_mean") : undefined,
          from: Math.max(minX, from),
          to: Math.min(maxX, to),
        });
        timed.push({ ...placedOf(d), kind: "break", x, label: String(p.label || ""), row: 0 });
      } else if (m.kind !== "trend" && m.kind !== "forecast") {
        failures.push({ d, reason: `a ${m.kind} mark cannot be drawn on a line` });
      }
      return undefined;
    });

  // ---------------------------------------------------------------- layout
  const left = 44;
  const W = Math.max(280, width);
  const plotW = Math.max(120, W - left - right);
  // The forecast zone is wide enough to read the band and the observed lines' end labels: one forecast year
  // on a 27-year axis would otherwise be a sliver.
  // At least ~140 px on a desktop (a sixth of the plot), ~64 px on a phone, at most a quarter (a third on a phone).
  // The observed lines' end labels sit in the zone, after the last year of data: one line on a desktop, two on a phone.
  const endInW = Math.max(
    0,
    ...endLabels.map((e) => (narrow ? Math.max(textWidth(e.name, 12.5, true), textWidth(e.text, 12.5, true)) : textWidth(`${e.name} ${e.text}`, 13, true))),
  );
  const futureW = hasFuture
    ? Math.min(
        plotW * (narrow ? 0.36 : 0.3),
        Math.max(((maxX - actualMax) / Math.max(1e-9, maxX - minX)) * plotW, narrow ? 64 : Math.max(140, plotW * 0.17), endInW + 16),
      )
    : 0;
  const pastW = plotW - futureW;
  const sx = (x: number) =>
    !hasFuture
      ? left + ((x - minX) / Math.max(1e-9, maxX - minX)) * plotW
      : x <= actualMax
        ? left + ((x - minX) / Math.max(1e-9, actualMax - minX)) * pastW
        : left + pastW + ((x - actualMax) / Math.max(1e-9, maxX - actualMax)) * futureW;
  // Label rows: each label reads rightwards from its rule, or leftwards when that keeps it on a row already
  // used (or inside the chart), so a handful of events take one or two rows, not one each. A label never
  // sits on another label nor on the dashed rule of an event above it (a rule runs down from its own row),
  // and a long one is shortened with an ellipsis: hovering it, or the crosshair on its year, shows it whole.
  const maxLabelW = narrow ? 118 : 190;
  const rowSpans: Array<Array<[number, number]>> = [];
  const rules: Array<{ x: number; row: number }> = [];
  const free = (r: number, a: number, b: number) =>
    (rowSpans[r] || []).every(([c, d]) => b < c - 6 || a > d + 6) && rules.every((u) => u.row >= r || u.x < a - 4 || u.x > b + 4);
  // An event's own rule must not cross a label placed on a row below its own.
  const ruleClear = (x: number, r: number) => rowSpans.every((spans, rr) => rr <= r || spans.every(([c, d]) => x < c - 4 || x > d + 4));
  timed.forEach((t) => {
    if (textWidth(t.label, 12.5, true) > maxLabelW) {
      const keep = Math.max(4, Math.floor(maxLabelW / (12.5 * 0.56)) - 1);
      t.short = t.label.slice(0, keep).replace(/[\s,;:.-]+$/, "") + "…";
    }
    const w = textWidth(t.short || t.label, 12.5, true) + (t.d.showNumber ? 26 : 0) + (t.about ? textWidth(aboutTag(t.about), 12) + 8 : 0) + 10;
    const x = sx(t.x);
    const rightOk = x + 5 + w <= W - 2;
    const leftOk = x - 5 - w >= 2;
    const ownRule = t.kind === "event";
    for (let r = 0; ; r++) {
      const ruleOk = !ownRule || ruleClear(x, r) || r >= 5;
      const tryRight = rightOk && ruleOk && (free(r, x + 5, x + 5 + w) || r >= 5);
      const tryLeft = leftOk && ruleOk && (free(r, x - 5 - w, x - 5) || r >= 5);
      if (tryRight || tryLeft || (!rightOk && !leftOk) || r >= 6) {
        // In the right half the free space is on the left (the data runs to the edge): read leftwards first.
        const preferLeft = x > left + plotW / 2;
        t.flip = tryLeft && (preferLeft || !tryRight);
        const span: [number, number] = t.flip ? [x - 5 - w, x - 5] : [x + 5, x + 5 + w];
        (rowSpans[r] = rowSpans[r] || []).push(span);
        t.row = r;
        if (ownRule) rules.push({ x, row: r });
        break;
      }
    }
  });
  const rowsUsed = rowSpans.length;
  const top = 22 + rowsUsed * 18;
  const H = (height || (narrow ? 250 : 300)) + rowsUsed * 18;
  const bottom = 26;
  const plotH = H - top - bottom;
  const sy = (v: number) => top + plotH - ((v - yr.lo) / Math.max(1e-9, yr.hi - yr.lo)) * plotH;

  // X ticks: whole years, spaced to fit.
  const minYear = Math.ceil(minX - 1e-6);
  const maxYear = Math.floor(maxX + 1e-6);
  const span = Math.max(1, maxYear - minYear);
  const step = [1, 2, 5, 10, 20].find((s) => (pastW / span) * s >= (narrow ? 40 : 46)) || 20;
  const xTicks: number[] = [];
  for (let y = minYear; y <= maxYear; y++) if ((y - minYear) % step === 0 || (y === maxYear && (maxYear - minYear) % step >= step / 2)) xTicks.push(y);
  if (xTicks[xTicks.length - 1] !== maxYear && (maxYear - xTicks[xTicks.length - 1]) * (pastW / span) >= 34) xTicks.push(maxYear);
  const forecastXs = new Set<number>();
  bands.forEach((b) => b.pts.slice(1).forEach((p) => forecastXs.add(Math.round(p[0]))));
  const ctxX = periodToX(ctx.period);
  const ctxYear = ctxX !== undefined ? Math.floor(ctxX + 1e-6) : undefined;
  // The year the page shows and the forecast years always get a tick; plain ticks too close to them go.
  const pxPerYear = pastW / span;
  const priority = [ctxYear, ...Array.from(forecastXs)].filter((y): y is number => y !== undefined && y >= minYear && y <= maxYear);
  priority.forEach((y) => {
    if (xTicks.indexOf(y) < 0) xTicks.push(y);
  });
  xTicks.sort((a, b) => a - b);
  void pxPerYear;
  const gapPx = (a: number, b: number) => Math.abs(sx(a) - sx(b));
  for (let i = xTicks.length - 1; i >= 1; i--) {
    const t = xTicks[i];
    if (t !== ctxYear && gapPx(t, xTicks[i - 1]) < 26) xTicks.splice(i, 1);
  }
  for (let i = xTicks.length - 1; i >= 0; i--) {
    const t = xTicks[i];
    if (priority.indexOf(t) >= 0) continue;
    if (priority.some((p) => p !== t && gapPx(p, t) < 38)) xTicks.splice(i, 1);
  }
  /** Two priority ticks side by side (2025 shown, 2026 forecast): the later one is written short. */
  const tickText = (t: number) => {
    const prev = xTicks[xTicks.indexOf(t) - 1];
    return prev !== undefined && gapPx(t, prev) < 38 ? `’${String(t).slice(2)}` : String(t);
  };

  // Paths.
  const recXs = Object.keys(recByX).map((k) => recByX[k].x as number).sort((a, b) => a - b);
  const pathOf = (key: string, src: Record<string, any> = byX, xs: number[] = actualXs) => {
    let d = "";
    let pen = false;
    xs.forEach((x) => {
      const v = (src[x.toFixed(4)] || {})[key];
      if (typeof v !== "number") {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${f(sx(x))} ${f(sy(v))} `;
      pen = true;
    });
    return d.trim();
  };
  let gapPath = "";
  if (panel.options?.gap_fill && seriesKeys.length >= 2) {
    const [a, b] = seriesKeys;
    const both = actualXs.filter((x) => typeof byX[x.toFixed(4)][a] === "number" && typeof byX[x.toFixed(4)][b] === "number");
    if (both.length > 1) {
      gapPath =
        "M" +
        both.map((x) => `${f(sx(x))} ${f(sy(byX[x.toFixed(4)][a]))}`).join(" L") +
        " L" +
        both
          .slice()
          .reverse()
          .map((x) => `${f(sx(x))} ${f(sy(byX[x.toFixed(4)][b]))}`)
          .join(" L") +
        " Z";
    }
  }

  // End labels, spread apart.
  const labelX = sx(maxX) + 8;
  // With a forecast the observed lines are labelled in the zone, right after their last point, at their own
  // value: never off at the far edge with a dotted line that reads as a forecast. A label that would sit on a
  // band goes under it (over it when the band falls), its own band first.
  const inX = sx(actualMax) + 8;
  const endH = narrow ? 30 : 16;
  let ys: number[];
  if (!hasFuture) {
    ys = spreadLabels(endLabels.map((e) => sy(e.value)), narrow ? 30 : 18, top + 6, top + plotH);
  } else {
    const boxes = bands.map((b) => {
      const yy = b.pts.reduce((acc: number[], p) => acc.concat([sy(p[1]), sy(p[2])]), []);
      const a = b.pts[0][3];
      const z = b.pts[b.pts.length - 1][3];
      return { top: Math.min(...yy), bot: Math.max(...yy), rising: typeof a === "number" && typeof z === "number" ? z >= a : true, key: b.key };
    });
    const want = endLabels.map((e) => {
      let y = sy(e.value);
      boxes
        .slice()
        .sort((a, b) => Number(b.key === e.s.key) - Number(a.key === e.s.key))
        .forEach((bx) => {
          if (y + endH / 2 <= bx.top || y - endH / 2 >= bx.bot) return;
          const below = bx.bot + endH / 2 + 4;
          const above = bx.top - endH / 2 - 4;
          y = bx.key === e.s.key ? (bx.rising ? below : above) : Math.abs(below - y) < Math.abs(above - y) ? below : above;
        });
      return y;
    });
    // clear of the "Forecast" word at the foot of the zone
    ys = spreadLabels(want, endH + 2, top + endH / 2, top + plotH - 20 - endH / 2);
  }
  const colYs = spreadBoxes(
    colLabels.map((l) => ({ y: sy(l.v), h: l.h })),
    top + 2,
    top + plotH,
    6,
  );

  // Where a forecast's number circle goes: above its band (under it for a second band), at the middle of the
  // zone, unless an end label or a forecast label is there; then right of the end labels, then the other side
  // of the band.
  const labelBoxes: Array<[number, number, number, number]> = endLabels.map((e, i) => {
    const lx = hasFuture ? inX : labelX;
    const w = narrow ? Math.max(textWidth(e.name, 12.5, true), textWidth(e.text, 12.5, true)) : textWidth(`${e.name} ${e.text}`, 13, true);
    const yTop = narrow ? ys[i] - 14 : ys[i] - 9;
    const yBot = narrow ? ys[i] + 16 : ys[i] + 9;
    return [lx - 2, yTop, lx + w + 2, yBot];
  });
  colLabels.forEach((l, i) => {
    const w = Math.max(...l.lines.map((t) => textWidth(t, l.kind === "central" ? 12.5 : 12, true)));
    labelBoxes.push([labelX - 2, colYs[i] - l.h / 2, labelX + w + 2, colYs[i] + l.h / 2]);
  });
  const clearOfLabels = (x: number, y: number, r = 11) => labelBoxes.every(([x0, y0, x1, y1]) => x + r < x0 || x - r > x1 || y + r < y0 || y - r > y1);
  const bandCircle = (b: (typeof bands)[number], bi: number): { x: number; y: number } => {
    const x0 = sx(b.pts[0][0]);
    const x1 = sx(b.pts[b.pts.length - 1][0]);
    const above = Math.min(...b.pts.map((p) => sy(p[2]))) - 14;
    const below = Math.max(...b.pts.map((p) => sy(p[1]))) + 14;
    const ys_ = bi % 2 === 0 ? [above, below] : [below, above];
    const right = Math.max(x0, ...labelBoxes.filter(([, , bx1]) => bx1 < x1).map(([, , bx1]) => bx1 + 13));
    const xs = [(x0 + x1) / 2, Math.min(x1 - 6, right)];
    for (const y of ys_) for (const x of xs) if (y > top - 4 && y < plotBottom + 4 && clearOfLabels(x, y)) return { x, y };
    return { x: (x0 + x1) / 2, y: ys_[0] };
  };

  // Hover readout.
  const hoverXs = actualXs.concat(
    bands
      .reduce((acc: number[], b) => acc.concat(b.pts.slice(1).map((p) => p[0])), [])
      .filter((x) => actualXs.indexOf(x) < 0),
  );
  hoverXs.sort((a, b) => a - b);
  const hx = hover !== null ? hoverXs[Math.max(0, Math.min(hoverXs.length - 1, hover))] : undefined;
  const nearest = (px: number) => {
    let best = 0;
    let bd = Infinity;
    hoverXs.forEach((x, i) => {
      const dd = Math.abs(sx(x) - px);
      if (dd < bd) {
        bd = dd;
        best = i;
      }
    });
    return best;
  };
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < left - 10 || px > left + plotW + 10) {
      setHover(null);
      return;
    }
    setHover(nearest(px));
    setTipPos({ x: e.clientX - r.left, y: e.clientY - r.top });
  };
  const onKey = (e: React.KeyboardEvent<SVGSVGElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End" && e.key !== "Escape") return;
    e.preventDefault();
    if (e.key === "Escape") return setHover(null);
    const cur = hover === null ? hoverXs.length - 1 : hover;
    const next = e.key === "Home" ? 0 : e.key === "End" ? hoverXs.length - 1 : cur + (e.key === "ArrowLeft" ? -1 : 1);
    const i = Math.max(0, Math.min(hoverXs.length - 1, next));
    setHover(i);
    setTipPos(null);
  };

  const tipRows: Array<{ swatch: string; value: string; label: string }> = [];
  if (hx !== undefined) {
    const r = byX[hx.toFixed(4)] || {};
    const rec = recByX[hx.toFixed(4)] || {};
    series.forEach((s) => {
      if (typeof r[s.key] === "number") tipRows.push({ swatch: s.color, value: fmtValue(r[s.key]), label: showAdj ? `${s.label}, seasonally adjusted` : s.label });
      if (showAdj && typeof rec[s.key] === "number") tipRows.push({ swatch: `${s.color}66`, value: fmtValue(rec[s.key]), label: `${s.label}, as recorded` });
      if (typeof rec[`${s.key}__eur`] === "number") tipRows.push({ swatch: "transparent", value: formatEur(rec[`${s.key}__eur`]), label: `${s.label}, value` });
    });
    if (gapPath && typeof r[seriesKeys[0]] === "number" && typeof r[seriesKeys[1]] === "number") {
      tipRows.push({ swatch: "transparent", value: fmtValue(Math.abs(r[seriesKeys[0]] - r[seriesKeys[1]])), label: "between them" });
    }
    bands.forEach((b) => {
      const p = b.pts.find((q) => Math.abs(q[0] - hx) < 1e-6 && q !== b.pts[0]);
      if (p && typeof p[3] === "number") tipRows.push({ swatch: b.d.ink, value: fmtValue(p[3]), label: "forecast" });
      if (p) tipRows.push({ swatch: b.d.color, value: `${fmtValue(p[1])} to ${fmtValue(p[2])}`, label: `${b.level ? `${Math.round(b.level * 100)}% ` : ""}likely range` });
    });
  }
  const tipLeftPx = hx !== undefined ? (tipPos ? tipPos.x : (sx(hx) / W) * width) : 0;
  const flip = tipLeftPx > width - 210;

  const aria =
    `${panel.title}${showAdj ? ", seasonally adjusted" : ""}. ` +
    endLabels.map((e) => `${e.name}: ${e.text} in ${labelOfX(e.x, monthly)}`).join("; ") +
    (marks.length ? `. ${marks.length} agent ${marks.length === 1 ? "mark" : "marks"}.` : ".");

  const plotBottom = top + plotH;
  const dim = (p: Placed) => (p.off ? OFF_CONTEXT_OPACITY : 1);

  return (
    <div ref={ref} style={{ position: "relative", display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
      {canToggleAdjust && (
        <div className="cx-adjust-toggle" role="group" aria-label="Values shown">
          <button
            type="button"
            className={`cx-adjust-btn${!adjusted ? " cx-adjust-btn--on" : ""}`}
            aria-pressed={!adjusted}
            title="The values as they were recorded, month by month"
            onClick={() => setAdjusted(false)}
          >
            As recorded
          </button>
          <button
            type="button"
            className={`cx-adjust-btn${adjusted ? " cx-adjust-btn--on" : ""}`}
            aria-pressed={adjusted}
            title="The usual month-to-month pattern taken out, so a month can be compared with the one before"
            onClick={() => setAdjusted(true)}
          >
            Seasonally adjusted
          </button>
        </div>
      )}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H * (width / W)}
        role="img"
        aria-label={aria}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerDown={onMove}
        onPointerLeave={() => setHover(null)}
        onKeyDown={onKey}
        onBlur={() => setHover(null)}
        style={{ display: "block", overflow: "visible", fontFamily: FONT_UI, touchAction: "pan-y", outline: "none" }}
      >
        <defs>
          {bands.map((b) => (
            <pattern key={b.d.mark.id} id={`h-${svgId}-${b.d.mark.id}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="6" stroke={b.d.color} strokeWidth={1.5} />
            </pattern>
          ))}
        </defs>
        <g aria-hidden="true">
          {/* grid and y axis */}
          {yr.ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={left + plotW} y1={f(sy(t))} y2={f(sy(t))} stroke={t === 0 ? TOKENS.field : TOKENS.grid} strokeWidth={1} />
              <text x={left - 8} y={f(sy(t) + 4)} textAnchor="end" style={{ fontSize: 12, fill: TOKENS.muted, ...NUM_STYLE }}>
                {tickLabel(t)}
              </text>
            </g>
          ))}
          {unitLabel && (
            <text x={0} y={f(top - 10)} textAnchor="start" style={{ fontSize: 12, fontWeight: 600, fill: TOKENS.muted }}>
              {unitLabel}
            </text>
          )}
          {xTicks.map((t) => {
            const isCtx = ctxYear === t;
            const fc = forecastXs.has(t);
            return (
              <text
                key={t}
                x={f(sx(t))}
                y={H - 6}
                textAnchor="middle"
                style={{ fontSize: 12, fill: isCtx ? TOKENS.ink : fc ? bands[0]?.d.ink || TOKENS.muted : TOKENS.muted, fontWeight: isCtx ? 700 : 400, ...NUM_STYLE }}
              >
                {tickText(t)}
              </text>
            );
          })}

          {/* spans under the data */}
          {timed
            .filter((t) => t.kind === "span" && t.x2 !== undefined)
            .map((t) => (
              <g key={`span-${t.d.mark.id}`} data-mark={t.d.mark.id} opacity={dim(t)}>
                <rect x={f(sx(t.x))} y={top} width={f(Math.max(2, sx(t.x2 as number) - sx(t.x)))} height={f(plotH)} fill={t.d.color} fillOpacity={0.09} />
                <line x1={f(sx(t.x))} x2={f(sx(t.x2 as number))} y1={top} y2={top} stroke={t.d.color} strokeWidth={2} />
              </g>
            ))}

          {gapPath && <path d={gapPath} fill={TOKENS.steel} fillOpacity={0.13} />}

          {/* the forecast zone: after the last month of data */}
          {hasFuture && (
            <g pointerEvents="none">
              <rect x={f(sx(actualMax))} y={top} width={f(left + plotW - sx(actualMax))} height={f(plotBottom - top)} fill={TOKENS.ink} fillOpacity={0.035} />
              <line x1={f(sx(actualMax))} x2={f(sx(actualMax))} y1={top} y2={plotBottom} stroke={TOKENS.ink} strokeOpacity={0.25} strokeDasharray="3 3" />
              {/* at the foot of the zone: the top belongs to the event labels and the bands' numbers */}
              <text x={f(sx(actualMax) + 6)} y={f(plotBottom - 6)} style={{ fontSize: 11.5, fill: TOKENS.muted }}>
                Forecast
              </text>
            </g>
          )}

          {/* forecast bands */}
          {bands.map((b, bi) => {
            const up = b.pts.map((p) => `${f(sx(p[0]))} ${f(sy(p[2]))}`);
            const down = b.pts
              .slice()
              .reverse()
              .map((p) => `${f(sx(p[0]))} ${f(sy(p[1]))}`);
            const mid = b.pts.filter((p) => typeof p[3] === "number");
            const last = b.pts[b.pts.length - 1];
            return (
              <g key={`band-${b.d.mark.id}`} data-mark={b.d.mark.id} opacity={dim(b)}>
                {/* the likely range (the mark's interval, 80% by default): a soft fill with a fine hatch */}
                <path d={`M${up.join(" L")} L${down.join(" L")} Z`} fill={b.d.color} fillOpacity={0.16} stroke="none" />
                <path d={`M${up.join(" L")} L${down.join(" L")} Z`} fill={`url(#h-${svgId}-${b.d.mark.id})`} fillOpacity={0.45} stroke={b.d.color} strokeOpacity={0.7} strokeWidth={1} />
                {/* the central forecast, dashed: a figure that is not data yet */}
                {mid.length > 1 && (
                  <path d={"M" + mid.map((p) => `${f(sx(p[0]))} ${f(sy(p[3] as number))}`).join(" L")} fill="none" stroke={b.d.ink} strokeWidth={2.5} strokeDasharray="6 4" strokeLinecap="round" />
                )}
                {/* the number inside the zone, above the band, clear of the end labels on the right; a second band's
                    number sits under its band so two never touch */}
                {b.d.showNumber && <SvgCircle {...bandCircle(b, bi)} n={b.d.number} color={b.d.color} />}
              </g>
            );
          })}

          {/* trend lines */}
          {trendLines.map((t) => (
            <g key={`trend-${t.d.mark.id}`} data-mark={t.d.mark.id} opacity={dim(t)}>
              <path d={"M" + t.pts.map((p) => `${f(sx(p[0]))} ${f(sy(p[1]))}`).join(" L")} fill="none" stroke={t.d.color} strokeWidth={2} strokeDasharray="6 4" />
              {t.d.showNumber && <SvgCircle x={sx(t.pts[t.pts.length - 1][0])} y={sy(t.pts[t.pts.length - 1][1]) - 16} n={t.d.number} color={t.d.color} />}
            </g>
          ))}

          {/* the values as recorded, faint, under the seasonally adjusted lines */}
          {showAdj &&
            series.map((s) => (
              <path key={`rec-${s.key}`} data-recorded={s.key} d={pathOf(s.key, recByX, recXs)} fill="none" stroke={s.color} strokeOpacity={0.35} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
            ))}

          {/* the data */}
          {series.map((s) => (
            <path key={s.key} d={pathOf(s.key)} fill="none" stroke={s.color} strokeWidth={s.width} strokeLinejoin="round" strokeLinecap="round" />
          ))}

          {/* the year the page shows */}
          {ctxX !== undefined &&
            ctxX >= minX - 1e-6 &&
            ctxX <= maxX + 1e-6 &&
            series.map((s) => {
              const r = byX[ctxX.toFixed(4)];
              const v = r ? r[s.key] : undefined;
              return typeof v === "number" ? <circle key={`ctx-${s.key}`} cx={f(sx(ctxX))} cy={f(sy(v))} r={4.5} fill="#FFFFFF" stroke={s.color} strokeWidth={2.5} /> : null;
            })}

          {/* trend breaks: a rule at the break and the level before and after */}
          {breaks.map((b) => (
            <g key={`brk-${b.d.mark.id}`} data-mark={b.d.mark.id} opacity={dim(b)}>
              <line x1={f(sx(b.x))} x2={f(sx(b.x))} y1={top - 4 + 0} y2={plotBottom} stroke={b.d.color} strokeWidth={1.5} />
              {typeof b.before === "number" && (
                <line x1={f(sx(b.from))} x2={f(sx(b.x))} y1={f(sy(b.before))} y2={f(sy(b.before))} stroke={b.d.color} strokeWidth={2.5} strokeDasharray="2 3" />
              )}
              {typeof b.after === "number" && (
                <line x1={f(sx(b.x))} x2={f(sx(b.to))} y1={f(sy(b.after))} y2={f(sy(b.after))} stroke={b.d.color} strokeWidth={2.5} />
              )}
            </g>
          ))}

          {/* events and the labels of everything timed; a shortened label opens on hover or under the crosshair
              and is drawn last, over its neighbours */}
          {timed
            .map((t) => ({ t, open: !!t.short && (openLabel === t.d.mark.id || (hx !== undefined && Math.abs(hx - t.x) < (monthly ? 0.5 : 0.75))) }))
            .sort((a, b) => Number(a.open) - Number(b.open))
            .map(({ t, open }) => {
              const x = sx(t.x);
              const ly = 14 + t.row * 18;
              const n = t.d.showNumber;
              const text = open ? t.label : t.short || t.label;
              const tw = textWidth(text, 12.5, true) + (t.about ? textWidth(aboutTag(t.about), 12) + 8 : 0);
              const tx = t.flip ? x - (n ? 27 : 5) : x + (n ? 27 : 5);
              // An opened label that would run off the chart reads the other way.
              const end = open ? (t.flip ? tx - tw >= 0 : tx + tw > W) : t.flip;
              return (
                <g
                  key={`lbl-${t.d.mark.id}`}
                  data-mark={t.d.mark.id}
                  opacity={dim(t)}
                  onPointerEnter={t.short ? () => setOpenLabel(t.d.mark.id) : undefined}
                  onPointerLeave={t.short ? () => setOpenLabel((o) => (o === t.d.mark.id ? null : o)) : undefined}
                >
                  {t.short && <title>{t.label}</title>}
                  {t.kind === "event" && <line x1={f(x)} x2={f(x)} y1={ly + 5} y2={plotBottom} stroke={t.d.color} strokeWidth={1.25} strokeDasharray="3 3" />}
                  {open && <rect x={f(end ? tx - tw - 4 : tx - 4)} y={ly - 13} width={f(tw + 8)} height={18} rx={3} fill={TOKENS.paper} stroke={TOKENS.rule} />}
                  {n && <SvgCircle x={t.flip ? x - 13 : x + 13} y={ly - 4} n={t.d.number} color={t.d.color} r={9} />}
                  <text x={f(tx)} y={ly} textAnchor={end ? "end" : "start"} style={{ fontSize: 12.5, fontWeight: 600, fill: t.d.ink }}>
                    {text}
                    {t.about && <tspan style={{ fontWeight: 500, fill: TOKENS.muted }}>{`  ${aboutTag(t.about)}`}</tspan>}
                  </text>
                </g>
              );
            })}

          {/* annotations: a dot on the data with a short leader */}
          {points.map((p) => {
            const x = sx(p.x);
            const y = sy(p.y);
            const upward = y > top + 40;
            const ty = upward ? y - 30 : y + 34;
            const anchorEnd = x > left + plotW - 120;
            return (
              <g key={`pt-${p.d.mark.id}`} data-mark={p.d.mark.id} opacity={dim(p)}>
                <line x1={f(x)} x2={f(x)} y1={f(y)} y2={f(upward ? ty + 6 : ty - 14)} stroke={p.d.color} strokeWidth={1.25} />
                <circle cx={f(x)} cy={f(y)} r={5} fill={p.d.color} stroke="#FFFFFF" strokeWidth={1.5} />
                {p.d.showNumber && <SvgCircle x={anchorEnd ? x - 12 : x + 12} y={ty - 4} n={p.d.number} color={p.d.color} r={9} />}
                <text x={f(anchorEnd ? x - (p.d.showNumber ? 26 : 6) : x + (p.d.showNumber ? 26 : 6))} y={f(ty)} textAnchor={anchorEnd ? "end" : "start"} style={{ fontSize: 12.5, fontWeight: 600, fill: p.d.ink }}>
                  {p.label}
                  {p.about && <tspan style={{ fontWeight: 500, fill: TOKENS.muted }}>{`  ${aboutTag(p.about)}`}</tspan>}
                </text>
              </g>
            );
          })}

          {/* direct labels at the line ends */}
          {endLabels.map((e, i) => {
            const y = ys[i];
            const ax = sx(e.x);
            const ay = sy(e.value);
            const lx = hasFuture ? inX : labelX;
            // with a forecast zone no leader: a dotted line into the future would read as a forecast
            const leader = !hasFuture && (labelX - ax > 14 || Math.abs(y - ay) > 3);
            return (
              <g key={`end-${e.s.key}`}>
                {leader && <path d={`M${f(ax + 5)} ${f(ay)} L${f(labelX - 4)} ${f(y)}`} stroke={e.s.color} strokeWidth={1} strokeDasharray={labelX - ax > 14 ? "2 3" : undefined} fill="none" />}
                {narrow ? (
                  <text x={f(lx)} y={f(y - 2)} style={{ fontSize: 12.5, fontWeight: 700, fill: e.s.text }}>
                    <tspan x={f(lx)}>{e.name}</tspan>
                    <tspan x={f(lx)} dy={14} style={{ ...NUM_STYLE }}>
                      {e.text}
                    </tspan>
                  </text>
                ) : (
                  <text x={f(lx)} y={f(y + 4.5)} style={{ fontSize: 13, fontWeight: 700, fill: e.s.text }}>
                    {e.name}{" "}
                    <tspan style={{ ...NUM_STYLE, fontWeight: 600 }}>{e.text}</tspan>
                  </text>
                )}
              </g>
            );
          })}

          {/* the forecast's own labels in the right column: the central figure (a dot) and the scenarios (rings) */}
          {colLabels.map((l, i) => {
            const ax = sx(l.x);
            const ay = sy(l.v);
            const y = colYs[i];
            const central = l.kind === "central";
            const size = central ? 12.5 : 12;
            const y0 = y - l.h / 2 + 11;
            return (
              <g key={`col-${l.b.d.mark.id}-${i}`} data-mark={l.b.d.mark.id} opacity={dim(l.b)}>
                <path d={`M${f(ax + 5)} ${f(ay)} L${f(labelX - 4)} ${f(y)}`} stroke={l.b.d.color} strokeWidth={1} strokeDasharray={central ? undefined : "2 3"} fill="none" />
                {central ? (
                  <circle cx={f(ax)} cy={f(ay)} r={4} fill={l.b.d.ink} stroke="#FFFFFF" strokeWidth={1.5} />
                ) : (
                  <circle cx={f(ax)} cy={f(ay)} r={4} fill="#FFFFFF" stroke={l.b.d.color} strokeWidth={2} />
                )}
                <text x={f(labelX)} y={f(y0)} style={{ fontSize: size, fontWeight: central ? 700 : 600, fill: l.b.d.ink }}>
                  {l.lines.map((t, j) => (
                    <tspan key={j} x={f(labelX)} dy={j ? 14 : 0} style={j === l.lines.length - 1 ? NUM_STYLE : undefined}>
                      {t}
                    </tspan>
                  ))}
                </text>
              </g>
            );
          })}

          {/* crosshair */}
          {hx !== undefined && (
            <g pointerEvents="none">
              <line x1={f(sx(hx))} x2={f(sx(hx))} y1={top} y2={plotBottom} stroke={TOKENS.ink} strokeOpacity={0.35} strokeWidth={1} />
              {series.map((s) => {
                const v = (byX[hx.toFixed(4)] || {})[s.key];
                return typeof v === "number" ? <circle key={`hv-${s.key}`} cx={f(sx(hx))} cy={f(sy(v))} r={4} fill={s.color} stroke="#FFFFFF" strokeWidth={1.5} /> : null;
              })}
            </g>
          )}
        </g>
      </svg>

      {canToggleAdjust && adjState !== "off" && (
        <div className="cx-line-legend cx-adjust-legend" role="status" style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 12.5, color: TOKENS.muted, fontFamily: FONT_UI }}>
          {adjState === "on" && (
            <>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <svg width="22" height="8" aria-hidden="true">
                  <line x1="1" x2="21" y1="4" y2="4" stroke={series[0]?.color} strokeWidth={3} strokeLinecap="round" />
                </svg>
                {ADJUSTED_WORDS}
              </span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <svg width="22" height="8" aria-hidden="true">
                  <line x1="1" x2="21" y1="4" y2="4" stroke={series[0]?.color} strokeOpacity={0.35} strokeWidth={1.5} strokeLinecap="round" />
                </svg>
                {RECORDED_WORDS}
                {bands.length ? ", what the forecast and the notes are about" : points.length ? ", what the notes are about" : ""}
              </span>
            </>
          )}
          {adjState === "loading" && <span>Taking out the usual month-to-month pattern…</span>}
          {adjState === "refused" && <span style={{ color: TOKENS.ink }}>{adjRefused?.error?.message || "Too short to adjust. The values are shown as recorded."}</span>}
          {adjState === "failed" && (
            <span style={{ color: TOKENS.ink }}>
              The seasonal adjustment could not be computed{adjFailed?.error?.message ? `: ${adjFailed.error.message}` : ""}. The values are shown as recorded.
            </span>
          )}
        </div>
      )}

      {hasFuture && (
        <div className="cx-line-legend" style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 12.5, color: TOKENS.muted, fontFamily: FONT_UI }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <svg width="22" height="8" aria-hidden="true">
              <line x1="1" x2="21" y1="4" y2="4" stroke={bands[0].d.ink} strokeWidth={2.5} strokeDasharray="6 4" strokeLinecap="round" />
            </svg>
            forecast{bands[0].scen.length ? ", if the recent rise goes on" : ""}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span aria-hidden="true" style={{ width: 16, height: 10, borderRadius: 2, background: bands[0].d.color, opacity: 0.35, border: `1px solid ${bands[0].d.color}` }} />
            {bands[0].level ? `${Math.round(bands[0].level * 100)}% likely range` : "likely range"}
          </span>
          {bands.some((b) => b.scen.length > 0) && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <svg width="12" height="12" aria-hidden="true">
                <circle cx="6" cy="6" r="4" fill="#FFFFFF" stroke={bands[0].d.color} strokeWidth={2} />
              </svg>
              scenarios
            </span>
          )}
        </div>
      )}

      {hx !== undefined && tipRows.length > 0 && (
        <div
          role="status"
          aria-live="polite"
          style={{
            position: "absolute",
            top: Math.max(0, (tipPos ? tipPos.y : (top / H) * (H * (width / W))) - 12),
            left: flip ? undefined : tipLeftPx + 14,
            right: flip ? Math.max(0, width - tipLeftPx + 14) : undefined,
            pointerEvents: "none",
            background: TOKENS.surface,
            color: TOKENS.ink,
            fontFamily: FONT_UI,
            fontSize: 13,
            lineHeight: 1.4,
            padding: "8px 10px",
            borderRadius: 8,
            boxShadow: `0 0 0 1px ${TOKENS.rule}, 0 6px 18px rgba(20, 32, 46, 0.12)`,
            minWidth: 150,
            zIndex: 3,
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 2 }}>{labelOfX(hx, monthly)}</div>
          {tipRows.map((r, i) => (
            <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 8, whiteSpace: "nowrap" }}>
              <span aria-hidden="true" style={{ display: "inline-block", width: 12, height: 3, borderRadius: 2, background: r.swatch, flex: "none" }} />
              <span style={{ fontWeight: 700, ...NUM_STYLE }}>{r.value}</span>
              <span style={{ color: TOKENS.muted }}>{r.label}</span>
            </div>
          ))}
        </div>
      )}

      {failures.map(({ d, state, reason }) =>
        state?.error || state?.notLoaded ? (
          <ErrorCard
            key={d.mark.id}
            state={state}
            title={`Mark ${d.number} (${d.agentTitle.toLowerCase()}) could not be drawn`}
            message={state.notLoaded ? "Not loaded" : undefined}
          />
        ) : state?.loading ? null : (
          <span key={d.mark.id} style={{ fontSize: 13, color: TOKENS.muted }}>
            Mark {d.number} could not be drawn: {reason || "its data is missing"}.
          </span>
        ),
      )}
    </div>
  );
};

/** The numbered circle inside the chart, in the agent's pen. */
const SvgCircle: React.FC<{ x: number; y: number; n: number; color: string; r?: number }> = ({ x, y, n, color, r = 10 }) => (
  <g>
    <circle cx={f(x)} cy={f(y)} r={r} fill={penFill(color)} stroke="#FFFFFF" strokeWidth={2} />
    <text x={f(x)} y={f(y + r * 0.42)} textAnchor="middle" style={{ fontSize: r * 1.2, fontWeight: 700, fill: "#FFFFFF", ...NUM_STYLE }}>
      {n}
    </text>
  </g>
);
