// Comparisons (contract 1.2): the compare panel, and line and bar panels with comparison options
// (isComparison in ./compare). Two layouts:
// - series over time (1 to 6 /insights/series queries): lines in the comparison's mode (absolute,
//   index, share, yoy, diff), direct labels at the line ends, a readout of where each series ends and
//   how it moved, small multiples with options.facet, exports against imports with options.balance;
// - two snapshots a and b (/insights/ranking or /insights/compare): paired bars per row, or one
//   diverging bar of the growth or the change, with the change read out on every row.
// Data is sky (exports) and steel (imports), the other series neutral greys; only marks use the
// agents' pens. Every figure comes from the API; the transforms only rebase, divide and subtract.

import React from "react";
import { DataTable, SvgBadge, TableToggle, HtmlNumber } from "./charts/parts";
import { ChartTip, localPoint, type TipRow } from "./charts/ChartTip";
import { SKY_RAMP, STEEL_RAMP } from "./charts/palette";
import { useMeasure } from "./charts/useMeasure";
import { ErrorCard, FactText, problemCard } from "./common";
import {
  type CompareOptions,
  type PairRow,
  type Readout,
  type SeriesIn,
  type SeriesOut,
  type SeriesStyle,
  type Transformed,
  type Unit,
  balanceOf,
  compareLayout,
  flowOf,
  indexText,
  modeWords,
  pairRows,
  pct,
  points,
  readOptions,
  readouts,
  seriesRows,
  seriesStyles,
  transformSeries,
  xLabel,
  xOf,
} from "./compare";
import { OFF_CONTEXT_OPACITY, contextOf, markOffContext, substituteFacts, substituteQueries } from "./context";
import { formatEur, formatPeriod, keyed, useFacts } from "./facts";
import type { DrawnMark } from "./marks";
import { FIGURE_STYLE, FONT_AGENT, FONT_UI, NUM_STYLE, TOKENS, isAgentOwner } from "./tokens";
import { firstProblem, useQueries } from "./useQuery";
import { usePageContext } from "./ViewStore";
import type { Panel, Query } from "./types";

export interface ComparePanelProps {
  panel: Panel;
  marks?: DrawnMark[];
  /** Height of the chart of series over time, in px (small multiples are smaller). */
  height?: number;
}

// ------------------------------------------------------------------------------------------ formats

function fmtValue(v: number | undefined, unit: Unit): string {
  if (v === undefined || !Number.isFinite(v)) return "–";
  switch (unit) {
    case "eur":
      return formatEur(v);
    case "eur_signed":
      return formatEur(v, true);
    case "index":
      return indexText(v);
    case "fraction":
      return pct(v);
    case "growth":
      return pct(v, true);
  }
}

function fmtTick(v: number, unit: Unit): string {
  if (unit === "index") return indexText(v);
  if (unit === "fraction") return pct(v);
  if (unit === "growth") return pct(v, v !== 0);
  if (v === 0) return "0";
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : unit === "eur_signed" ? "+" : "";
  const t = (x: number) => String(Math.round(x * 10) / 10);
  if (a >= 1e9) return `${sign}${t(a / 1e9)} bn`;
  if (a >= 1e6) return `${sign}${t(a / 1e6)} m`;
  if (a >= 1e3) return `${sign}${t(a / 1e3)} k`;
  return `${sign}${t(a)}`;
}

function deltaText(r: Readout): string {
  if (r.delta === undefined || !Number.isFinite(r.delta)) return "";
  const since = r.from ? ` ${r.against ? "on" : "since"} ${formatPeriod(r.from)}` : "";
  switch (r.deltaUnit) {
    case "growth":
      return `${pct(r.delta, true)}${since}`;
    case "points":
      return r.from && r.period && r.from !== r.period ? `${points(r.delta)} on ${formatPeriod(r.from)}` : "";
    case "points_since":
      return r.from && r.period && r.from !== r.period ? `${points(r.delta)}${since}` : "";
    case "eur_signed":
      return `${formatEur(r.delta, true)}${since}`;
    default:
      return "";
  }
}

/** Ticks on a round step that cover [min, max]. */
function niceScale(min: number, max: number, n = 4): { lo: number; hi: number; ticks: number[] } {
  if (!(max > min)) {
    const pad = Math.abs(max) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / n;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) || 10 * pow;
  const lo = Math.floor(min / step + 1e-9) * step;
  const hi = Math.ceil(max / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let t = lo; t <= hi + step * 1e-6; t += step) ticks.push(Math.round(t / step) * step);
  return { lo, hi, ticks };
}

/** A query's label: its own, else the code it filters on, else the years it reads. */
function labelOf(q: Query, i: number): string {
  if (q.label) return q.label;
  const p = q.params || {};
  for (const k of ["partner", "product", "reporter"]) if (p[k] !== undefined) return String(p[k]);
  if (p.period !== undefined) return String(p.period);
  if (p.start !== undefined) return String(p.start).slice(0, 4);
  return q.key || `Series ${i + 1}`;
}

/** "Ireland (Eire)" -> Ireland; product codes go in front of their short name (85 Electrical machinery ...). */
function rowName(r: { code: string; name: string }): string {
  const short =
    r.name
      .split(";")[0]
      .split(" (")[0]
      .replace(/,? and (parts|articles|accessories) thereof$/i, "")
      .split(", ")[0]
      .trim() || r.code;
  return /^\d{2,8}$/.test(r.code) && short !== r.code ? `${r.code} ${short}` : short;
}

function toneOf(q: Query): string | undefined {
  if (q.tone === "exports" || q.tone === "imports") return q.tone;
  const f = flowOf(q);
  return f === "IMPORT" ? "imports" : f === "EXPORT" ? "exports" : undefined;
}

// ------------------------------------------------------------------------------------------ marks

interface TimeMark {
  d: DrawnMark;
  kind: "event" | "span" | "point";
  x: number;
  x2?: number;
  series?: string;
  period?: string;
  label: string;
  opacity: number;
}

function timeMarks(marks: DrawnMark[], align: "time" | "month", ctxPeriod: ReturnType<typeof contextOf>): { ok: TimeMark[]; failed: Array<{ d: DrawnMark; reason: string }> } {
  const ok: TimeMark[] = [];
  const failed: Array<{ d: DrawnMark; reason: string }> = [];
  marks.forEach((d) => {
    const p = d.mark.params || {};
    const opacity = markOffContext(d.mark, ctxPeriod) ? OFF_CONTEXT_OPACITY : 1;
    const label = String(p.label || "");
    if (d.mark.kind === "event") {
      const x = xOf(String(p.at || ""), align);
      if (x === undefined) return failed.push({ d, reason: `bad period ${p.at}` });
      const x2 = p.until ? xOf(String(p.until), align) : undefined;
      ok.push({ d, kind: x2 !== undefined ? "span" : "event", x, x2, label, opacity });
    } else if (d.mark.kind === "highlight") {
      const x = xOf(String(p.from || ""), align);
      const x2 = xOf(String(p.to || ""), align);
      if (x === undefined || x2 === undefined) return failed.push({ d, reason: "a highlight over time needs from and to" });
      ok.push({ d, kind: "span", x, x2, label, opacity });
    } else if (d.mark.kind === "annotation") {
      const x = xOf(String(p.x || ""), align);
      if (x === undefined) return failed.push({ d, reason: `bad period ${p.x}` });
      ok.push({ d, kind: "point", x, period: String(p.x), series: d.mark.series, label, opacity });
    } else failed.push({ d, reason: `a ${d.mark.kind} mark is not drawn on a comparison` });
  });
  return { ok, failed };
}

// ------------------------------------------------------------------------------------------ the chart over time

interface SeriesChartProps {
  series: SeriesOut[];
  styles: Record<string, SeriesStyle>;
  unit: Unit;
  align: "time" | "month";
  width: number;
  height: number;
  rule?: number;
  ruleLabel?: string;
  /** Shared y domain (small multiples). */
  domain?: [number, number];
  /** Shared x domain. */
  xDomain?: [number, number];
  /** Series drawn faintly behind (the other series of a small multiple). */
  faint?: SeriesOut[];
  balance?: Array<{ x: number; exports: number; imports: number; balance: number }>;
  marks?: TimeMark[];
  /** Show the number circles of the marks (only on the first small multiple). */
  badges?: boolean;
  labels?: boolean;
  compact?: boolean;
  title: string;
}

export const SeriesChart: React.FC<SeriesChartProps> = ({
  series,
  styles,
  unit,
  align,
  width,
  height,
  rule,
  ruleLabel,
  domain,
  xDomain,
  faint = [],
  balance = [],
  marks = [],
  badges = true,
  labels = true,
  compact = false,
  title,
}) => {
  const wrap = React.useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = React.useState<number | null>(null);
  const [tip, setTip] = React.useState<{ x: number; y: number } | null>(null);

  const all = series.concat(faint);
  const xs = Array.from(new Set(all.reduce((acc: number[], s) => acc.concat(s.points.map((p) => p.x)), []))).sort((a, b) => a - b);
  if (!xs.length) return <span style={{ fontSize: 13, color: TOKENS.muted }}>No values to draw.</span>;
  const [x0, x1] = xDomain || [xs[0], xs[xs.length - 1]];
  let vals = all.reduce((acc: number[], s) => acc.concat(s.points.map((p) => p.value)), []);
  if (rule !== undefined) vals = vals.concat([rule]);
  // Euros start at zero, except months of different years laid over each other: there the gap is the point.
  if (unit === "eur" && align !== "month") vals = vals.concat([0]);
  const scale = domain ? niceScale(domain[0], domain[1], compact ? 3 : 4) : niceScale(Math.min.apply(null, vals), Math.max.apply(null, vals), compact ? 3 : 4);

  const narrow = width < 520;
  const longest = labels ? Math.max.apply(null, series.map((s) => s.label.length).concat([0])) : 0;
  const maxLabel = narrow ? 12 : 22;
  const right = labels ? Math.min(Math.max(48, Math.min(longest, maxLabel) * 7 + 18), width * 0.34) : 10;
  const left = compact ? 40 : 52;
  const hasMarks = marks.length > 0;
  const top = hasMarks && !compact ? 30 : compact ? 8 : 14;
  const bottom = 24;
  const pw = Math.max(40, width - left - right);
  const ph = Math.max(40, height - top - bottom);
  const sx = (x: number) => left + (x1 === x0 ? pw / 2 : ((x - x0) / (x1 - x0)) * pw);
  const sy = (v: number) => top + (scale.hi === scale.lo ? ph / 2 : ((scale.hi - v) / (scale.hi - scale.lo)) * ph);
  const clampY = (y: number) => Math.max(top, Math.min(top + ph, y));

  // x ticks: every year, thinned to fit; months by name.
  let xticks: number[] = [];
  if (align === "month") xticks = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].filter((m) => !narrow || m % 2 === 1 || compact === false);
  else {
    for (let y = Math.ceil(x0 - 1e-6); y <= Math.floor(x1 + 1e-6); y++) xticks.push(y);
    const room = Math.max(2, Math.floor(pw / (compact ? 44 : 46)));
    const step = Math.ceil(xticks.length / room);
    if (step > 1) xticks = xticks.filter((y) => (Math.floor(x1) - y) % step === 0);
  }
  if (compact && xticks.length > 3) xticks = [xticks[0], xticks[Math.floor(xticks.length / 2)], xticks[xticks.length - 1]];

  const path = (s: SeriesOut) =>
    s.points.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${clampY(sy(p.value)).toFixed(1)}`).join("");

  // Direct labels at the line ends, nudged apart.
  const ends = labels
    ? series
        .filter((s) => s.points.length)
        .map((s) => ({ s, y: clampY(sy(s.points[s.points.length - 1].value)), x: sx(s.points[s.points.length - 1].x) }))
        .sort((a, b) => a.y - b.y)
    : [];
  const gap = 15;
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < gap) ends[i].y = ends[i - 1].y + gap;
  for (let i = ends.length - 1; i >= 0; i--) {
    const limit = top + ph + 4 - (ends.length - 1 - i) * gap;
    if (ends[i].y > limit) ends[i].y = limit;
  }

  // Balance shading between exports and imports, segment by segment.
  const shade = balance.slice(1).map((b, i) => {
    const a = balance[i];
    const d = `M${sx(a.x)},${sy(a.exports)}L${sx(b.x)},${sy(b.exports)}L${sx(b.x)},${sy(b.imports)}L${sx(a.x)},${sy(a.imports)}Z`;
    const surplus = a.balance + b.balance >= 0;
    return <path key={i} d={d} fill={surplus ? TOKENS.sky : TOKENS.steel} fillOpacity={surplus ? 0.12 : 0.14} />;
  });

  const hx = hover !== null ? xs[hover] : null;
  const tipRows: TipRow[] =
    hx === null
      ? []
      : series
          .map((s) => ({ s, p: s.points.find((p) => Math.abs(p.x - hx) < 1e-6) }))
          .filter((r) => !!r.p)
          .sort((a, b) => (b.p!.value as number) - (a.p!.value as number))
          .map((r) => ({ value: fmtValue(r.p!.value, unit), label: r.s.label, swatch: styles[r.s.key]?.color }));
  const bal = hx !== null ? balance.find((b) => Math.abs(b.x - hx) < 1e-6) : undefined;
  if (bal) tipRows.push({ value: formatEur(bal.balance, true), label: "balance" });
  const hoverPeriod = hx === null ? "" : (all.find((s) => s.points.some((p) => Math.abs(p.x - hx) < 1e-6))?.points.find((p) => Math.abs(p.x - hx) < 1e-6)?.period || "");

  const nearest = (px: number) => {
    let best = 0;
    xs.forEach((x, i) => {
      if (Math.abs(sx(x) - px) < Math.abs(sx(xs[best]) - px)) best = i;
    });
    return best;
  };

  const onMove = (e: React.PointerEvent) => {
    const pt = localPoint(e, wrap.current);
    const i = nearest(pt.x);
    setHover(i);
    setTip({ x: sx(xs[i]), y: pt.y });
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const i = hover === null ? xs.length - 1 : Math.max(0, Math.min(xs.length - 1, hover + (e.key === "ArrowLeft" ? -1 : 1)));
    setHover(i);
    setTip({ x: sx(xs[i]), y: top + 10 });
  };

  // Marks: event rules, spans and points, in the agent's pen.
  const spanEls: React.ReactNode[] = [];
  const markEls: React.ReactNode[] = [];
  marks.forEach((m) => {
    const c = m.d.color;
    const n = m.d.number;
    const badge = badges && m.d.showNumber;
    if (m.kind === "event") {
      const x = sx(m.x);
      if (x < left - 1 || x > left + pw + 1) return;
      markEls.push(
        <g key={m.d.mark.id} opacity={m.opacity}>
          <line x1={x} x2={x} y1={top} y2={top + ph} stroke={c} strokeWidth={1.25} strokeDasharray="3 3" />
          {!compact && m.label && (
            <text x={x + (badge ? 16 : 4)} y={top - 8} fontFamily={FONT_UI} fontSize={12.5} fontWeight={600} fill={m.d.ink}>
              {m.label}
            </text>
          )}
          {badge && <SvgBadge x={x} y={compact ? top + 10 : top - 12} n={n} color={c} r={compact ? 8 : 10} />}
        </g>,
      );
    } else if (m.kind === "span") {
      const xa = Math.max(left, sx(m.x));
      const xb = Math.min(left + pw, sx(m.x2 as number));
      if (xb < xa) return;
      spanEls.push(
        <g key={m.d.mark.id} opacity={m.opacity}>
          <rect x={xa} y={top} width={Math.max(2, xb - xa)} height={ph} fill={c} fillOpacity={0.08} />
          <line x1={xa} x2={Math.max(xa + 2, xb)} y1={top} y2={top} stroke={c} strokeWidth={2} />
          {!compact && m.label && (
            <text x={xa + (badge ? 16 : 4)} y={top - 8} fontFamily={FONT_UI} fontSize={12.5} fontWeight={600} fill={m.d.ink}>
              {m.label}
            </text>
          )}
          {badge && <SvgBadge x={xa} y={compact ? top + 10 : top - 12} n={n} color={c} r={compact ? 8 : 10} />}
        </g>,
      );
    } else {
      const s = series.find((x) => x.key === m.series) || series.find((x) => styles[x.key]?.focus) || series[0];
      const p = s?.points.find((q) => Math.abs(q.x - m.x) < 1e-6);
      if (!s || !p) return;
      const x = sx(p.x);
      const y = clampY(sy(p.value));
      const ly = Math.max(top + 4, y - 34);
      markEls.push(
        <g key={m.d.mark.id} opacity={m.opacity}>
          <line x1={x} x2={x} y1={y - 6} y2={ly + 6} stroke={c} strokeWidth={1.5} />
          <circle cx={x} cy={y} r={5} fill="#FFFFFF" stroke={c} strokeWidth={2.5} />
          {badges && m.d.showNumber && <SvgBadge x={x} y={ly} n={n} color={c} r={compact ? 8 : 10} />}
          {!compact && m.label && (
            <text x={x + (x > left + pw * 0.7 ? -14 : 14)} y={ly + 4} textAnchor={x > left + pw * 0.7 ? "end" : "start"} fontFamily={FONT_UI} fontSize={12.5} fontWeight={600} fill={m.d.ink}>
              {m.label}
            </text>
          )}
        </g>,
      );
    }
  });

  return (
    <div ref={wrap} style={{ position: "relative", width: "100%" }}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={title}
        tabIndex={0}
        onKeyDown={onKey}
        onBlur={() => {
          setHover(null);
          setTip(null);
        }}
        style={{ display: "block", overflow: "visible", outline: "none", fontFamily: FONT_UI }}
      >
        <g aria-hidden="true">
          {scale.ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={left + pw} y1={sy(t)} y2={sy(t)} stroke={TOKENS.grid} strokeWidth={1} />
              <text x={left - 8} y={sy(t) + 4} textAnchor="end" fontSize={compact ? 11 : 12} fill={TOKENS.muted} style={NUM_STYLE}>
                {fmtTick(t, unit)}
              </text>
            </g>
          ))}
          {xticks.map((x) => (
            <text key={x} x={sx(x)} y={height - 6} textAnchor="middle" fontSize={compact ? 11 : 12} fill={TOKENS.muted} style={NUM_STYLE}>
              {xLabel(x, align)}
            </text>
          ))}
          {rule !== undefined && rule >= scale.lo && rule <= scale.hi && (
            <g>
              <line x1={left} x2={left + pw} y1={sy(rule)} y2={sy(rule)} stroke={TOKENS.muted} strokeWidth={1.25} />
              {ruleLabel && !compact && !narrow && (
                <text x={left + 6} y={sy(rule) - 6} fontSize={12} fill={TOKENS.muted}>
                  {ruleLabel}
                </text>
              )}
            </g>
          )}
          {shade}
          {spanEls}
          {faint.map((s) => (
            <path key={`f-${s.key}`} d={path(s)} fill="none" stroke="#C5CED8" strokeWidth={1.25} strokeLinejoin="round" />
          ))}
          {series
            .slice()
            .sort((a, b) => Number(!!styles[a.key]?.focus) - Number(!!styles[b.key]?.focus))
            .map((s) => {
              const st = styles[s.key];
              const lone = s.points.length === 1;
              return lone ? (
                <circle key={s.key} cx={sx(s.points[0].x)} cy={clampY(sy(s.points[0].value))} r={4} fill={st?.color} />
              ) : (
                <path key={s.key} d={path(s)} fill="none" stroke={st?.color} strokeWidth={compact ? Math.min(2.5, st?.width || 2) : st?.width} strokeDasharray={st?.dash || undefined} strokeLinejoin="round" strokeLinecap="round" />
              );
            })}
          {markEls}
          {ends.map(({ s, x, y }) => {
            const st = styles[s.key];
            const text = s.label.length > maxLabel ? `${s.label.slice(0, maxLabel - 1)}…` : s.label;
            return (
              <text key={`l-${s.key}`} x={x + 8} y={y + 4} fontSize={13} fontWeight={st?.focus ? 700 : 600} fill={st?.text}>
                {text}
              </text>
            );
          })}
          {hx !== null && (
            <g>
              <line x1={sx(hx)} x2={sx(hx)} y1={top} y2={top + ph} stroke={TOKENS.ink} strokeOpacity={0.25} />
              {series.map((s) => {
                const p = s.points.find((q) => Math.abs(q.x - hx) < 1e-6);
                return p ? <circle key={`h-${s.key}`} cx={sx(p.x)} cy={clampY(sy(p.value))} r={3.5} fill={styles[s.key]?.color} stroke="#FFFFFF" strokeWidth={1.5} /> : null;
              })}
            </g>
          )}
        </g>
        <rect
          x={left}
          y={top}
          width={pw}
          height={ph}
          fill="transparent"
          onPointerMove={onMove}
          onPointerDown={onMove}
          onPointerLeave={() => {
            setHover(null);
            setTip(null);
          }}
        />
      </svg>
      <ChartTip x={tip?.x ?? 0} y={tip?.y ?? 0} width={width} title={hoverPeriod ? (align === "month" ? xLabel(hx as number, align) : formatPeriod(hoverPeriod)) : undefined} rows={tipRows} visible={hx !== null && !!tip} />
    </div>
  );
};

// ------------------------------------------------------------------------------------------ readout

const ReadoutRow: React.FC<{ items: Readout[]; styles: Record<string, SeriesStyle>; unit: Unit; extra?: React.ReactNode; narrow: boolean }> = ({ items, styles, unit, extra, narrow }) =>
  narrow ? (
    // Phone: one line per series, the figure and its change on the right.
    <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingBottom: 10, borderBottom: `1px solid ${TOKENS.rule}` }}>
      {items.map((r) => {
        const st = styles[r.key];
        return (
          <div key={r.key} style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, minWidth: 0 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: st?.focus ? 700 : 600, color: st?.text || TOKENS.ink2, minWidth: 0 }}>
              <svg width={16} height={6} aria-hidden="true" style={{ flex: "none" }}>
                <line x1={1} x2={15} y1={3} y2={3} stroke={st?.color} strokeWidth={st?.focus ? 3.5 : 2.5} strokeDasharray={st?.dash || undefined} strokeLinecap="round" />
              </svg>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.label}</span>
            </span>
            <span style={{ display: "flex", alignItems: "baseline", gap: 8, flex: "none" }}>
              <span style={{ fontSize: 12, color: TOKENS.muted, ...NUM_STYLE }}>{deltaText(r).split(" since ")[0].split(" on ")[0]}</span>
              <span style={{ ...FIGURE_STYLE, fontSize: 20, color: TOKENS.ink }}>{fmtValue(r.value, unit)}</span>
            </span>
          </div>
        );
      })}
      {extra}
      {items[0]?.period && (
        <span style={{ fontSize: 12, color: TOKENS.muted }}>
          {`${formatPeriod(items[0].period)}${items[0].from && items[0].delta !== undefined ? `, change ${items[0].deltaUnit === "points" ? "on" : "since"} ${formatPeriod(items[0].from)}` : ""}`}
        </span>
      )}
    </div>
  ) : (
  <div
    style={{
      display: "grid",
      gridTemplateColumns: `repeat(auto-fill, minmax(${narrow ? 140 : 150}px, 1fr))`,
      gap: "10px 20px",
      paddingBottom: 10,
      borderBottom: `1px solid ${TOKENS.rule}`,
    }}
  >
    {items.map((r) => {
      const st = styles[r.key];
      return (
        <div key={r.key} style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: st?.focus ? 700 : 600, color: st?.text || TOKENS.ink2, minWidth: 0 }}>
            <svg width={18} height={6} aria-hidden="true" style={{ flex: "none" }}>
              <line x1={1} x2={17} y1={3} y2={3} stroke={st?.color} strokeWidth={st?.focus ? 3.5 : 2.5} strokeDasharray={st?.dash || undefined} strokeLinecap="round" />
            </svg>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.label}>
              {r.label}
            </span>
          </span>
          <span style={{ ...FIGURE_STYLE, fontSize: narrow ? 24 : 28, color: TOKENS.ink }}>{fmtValue(r.value, unit)}</span>
          <span style={{ fontSize: 12.5, color: TOKENS.muted, ...NUM_STYLE }}>
            {r.period ? `${formatPeriod(r.period)}` : ""}
            {deltaText(r) ? `, ${deltaText(r)}` : ""}
          </span>
        </div>
      );
    })}
    {extra}
  </div>
);

// ------------------------------------------------------------------------------------------ series layout

export const SeriesView: React.FC<{
  panel: Panel;
  queries: Query[];
  states: ReturnType<typeof useQueries>;
  opts: CompareOptions;
  marks: DrawnMark[];
  height: number;
  ctx: ReturnType<typeof contextOf>;
}> = ({ panel, queries, states, opts, marks, height, ctx }) => {
  const { ref, width } = useMeasure<HTMLDivElement>(720);
  const narrow = width < 520;
  const input: SeriesIn[] = queries.map((q, i) => ({ key: q.key || `s${i}`, label: labelOf(q, i), tone: toneOf(q), rows: seriesRows(states[i].data) }));
  const flows: Record<string, string> = {};
  queries.forEach((q, i) => (flows[q.key || `s${i}`] = flowOf(q) || ""));
  const t: Transformed = transformSeries(input, opts);
  const styles = seriesStyles(t.series, opts.focus);
  const balance = opts.balance && opts.mode === "absolute" ? balanceOf(input, flows, opts.align) : [];
  const { ok: tmarks, failed } = timeMarks(marks, opts.align, ctx);
  const items = readouts(t, opts.align, opts.focus);
  const words = modeWords(t, opts);
  const ruleLabel =
    opts.mode === "index" ? (t.base && t.base !== "first" ? `${formatPeriod(t.base)} = 100` : "First period = 100") : opts.mode === "diff" && t.reference ? t.reference.label : undefined;
  const title = `${panel.title}. ${words}.`;
  const lastBal = balance[balance.length - 1];
  const firstBal = balance[0];

  if (t.problem && t.series.every((s) => !s.points.length)) {
    return <ErrorCard state={states[0]} title="This comparison could not be drawn" message={t.problem} />;
  }
  if (t.series.every((s) => !s.points.length)) {
    return <ErrorCard state={states[0]} title="No data to compare" message="The queries returned no values in this span." />;
  }

  const balanceSwatch = lastBal ? (
    <span aria-hidden="true" style={{ width: 14, height: 10, borderRadius: 2, flex: "none", background: lastBal.balance >= 0 ? "rgba(14,143,208,0.18)" : "rgba(122,135,150,0.24)" }} />
  ) : null;
  const balanceItem = !lastBal ? null : narrow ? (
    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10 }}>
      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600, color: TOKENS.ink2 }}>
        {balanceSwatch}
        Balance
      </span>
      <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        {firstBal && firstBal !== lastBal && <span style={{ fontSize: 12, color: TOKENS.muted, ...NUM_STYLE }}>{`${formatEur(firstBal.balance, true)} in ${formatPeriod(firstBal.period)}`}</span>}
        <span style={{ ...FIGURE_STYLE, fontSize: 20, color: TOKENS.ink }}>{formatEur(lastBal.balance, true)}</span>
      </span>
    </div>
  ) : (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600, color: TOKENS.ink2 }}>
        {balanceSwatch}
        Balance
      </span>
      <span style={{ ...FIGURE_STYLE, fontSize: 28, color: TOKENS.ink }}>{formatEur(lastBal.balance, true)}</span>
      <span style={{ fontSize: 12.5, color: TOKENS.muted, ...NUM_STYLE }}>
        {formatPeriod(lastBal.period)}
        {firstBal && firstBal !== lastBal ? `, ${formatEur(firstBal.balance, true)} in ${formatPeriod(firstBal.period)}` : ""}
      </span>
    </div>
  );

  // Small multiples: one chart per series, on one scale, the others faint behind.
  let body: React.ReactNode;
  if (opts.facet && t.series.length > 1) {
    const vals = t.series.reduce((acc: number[], s) => acc.concat(s.points.map((p) => p.value)), t.rule !== undefined ? [t.rule] : t.unit === "eur" ? [0] : []);
    const xsAll = t.series.reduce((acc: number[], s) => acc.concat(s.points.map((p) => p.x)), []);
    const domain: [number, number] = [Math.min.apply(null, vals), Math.max.apply(null, vals)];
    const xDomain: [number, number] = [Math.min.apply(null, xsAll), Math.max.apply(null, xsAll)];
    const cols = width >= 860 ? 3 : width >= 480 ? 2 : 1;
    const cellW = Math.floor((width - (cols - 1) * 24) / cols);
    body = (
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: "20px 24px" }}>
        {t.series.map((s, i) => {
          const own = { [s.key]: { ...(styles[s.key] || { color: TOKENS.sky, text: TOKENS.skyText, width: 2.5, dash: "", focus: true }), color: s.tone === "imports" ? TOKENS.steel : TOKENS.sky, text: s.tone === "imports" ? TOKENS.steelText : TOKENS.skyText, dash: "", focus: true } };
          const r = items[i];
          const facetMarks = tmarks.filter((m) => m.kind !== "point" || (m.series ? m.series === s.key : i === 0));
          return (
            <div key={s.key} style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, paddingTop: 8, borderTop: `1px solid ${TOKENS.rule}` }}>
              <span style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontSize: 14, fontWeight: 700, color: TOKENS.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.label}>
                  {s.label}
                </span>
                <span style={{ ...FIGURE_STYLE, fontSize: 22, color: own[s.key].text, flex: "none" }}>{fmtValue(r?.value, t.unit)}</span>
              </span>
              <span style={{ fontSize: 12.5, color: TOKENS.muted, ...NUM_STYLE }}>{r ? deltaText(r) : ""}</span>
              <SeriesChart
                series={[s]}
                faint={t.series.filter((o) => o !== s)}
                styles={own}
                unit={t.unit}
                align={opts.align}
                width={cellW}
                height={150}
                rule={t.rule}
                domain={domain}
                xDomain={xDomain}
                marks={facetMarks}
                badges={i === 0}
                labels={false}
                compact
                title={`${s.label}: ${title}`}
              />
            </div>
          );
        })}
      </div>
    );
  } else {
    body = (
      <>
        <ReadoutRow items={items} styles={styles} unit={t.unit} extra={balanceItem} narrow={narrow} />
        <SeriesChart
          series={t.series}
          styles={styles}
          unit={t.unit}
          align={opts.align}
          width={width}
          height={narrow ? Math.min(height, 240) : height}
          rule={t.rule}
          ruleLabel={ruleLabel}
          balance={balance}
          marks={tmarks}
          title={title}
        />
      </>
    );
  }

  // The table: every period, the values in the mode, and the euros behind them.
  const periods = Array.from(new Set(t.series.reduce((acc: string[], s) => acc.concat(s.points.map((p) => p.period)), []))).sort();
  const raw = opts.mode !== "absolute";
  const columns = ["Period"].concat(t.series.map((s) => s.label)).concat(raw ? t.series.map((s) => `${s.label}, €`) : []).concat(balance.length ? ["Balance"] : []);
  const rows = periods.map((p) => {
    const at = (s: SeriesOut) => s.points.find((q) => q.period === p);
    const b = balance.find((x) => x.period === p);
    return ([formatPeriod(p)] as (string | number)[])
      .concat(t.series.map((s) => fmtValue(at(s)?.value, t.unit)))
      .concat(raw ? t.series.map((s) => (at(s) ? formatEur(at(s)!.raw) : "–")) : [])
      .concat(balance.length ? [b ? formatEur(b.balance, true) : "–"] : []);
  });

  return (
    <div ref={ref} style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
      {opts.mode !== "absolute" && !panel.subtitle && <span style={{ fontSize: 13, color: TOKENS.muted }}>{words}</span>}
      {body}
      {failed.map(({ d, reason }) => (
        <span key={d.mark.id} style={{ fontSize: 13, color: TOKENS.muted }}>
          Mark {d.number} could not be drawn: {reason}.
        </span>
      ))}
      <Caption panel={panel} queries={queries} states={states} ctx={ctx} />
      <TableToggle>
        <DataTable columns={columns} rows={rows} caption={opts.mode === "absolute" ? `${panel.title}, in euros.` : `${panel.title}. ${words}.`} />
      </TableToggle>
    </div>
  );
};

// ------------------------------------------------------------------------------------------ pair layout

const PairView: React.FC<{
  panel: Panel;
  queries: Query[];
  states: ReturnType<typeof useQueries>;
  opts: CompareOptions;
  marks: DrawnMark[];
  ctx: ReturnType<typeof contextOf>;
}> = ({ panel, queries, states, opts, marks, ctx }) => {
  const ia = queries.findIndex((q) => q.key === "a");
  const ib = queries.findIndex((q) => q.key === "b");
  const qa = queries[ia];
  const qb = queries[ib];
  const rows: PairRow[] = pairRows(states[ia]?.data, states[ib]?.data, opts);
  const labelA = qa?.label || String(qa?.params?.period ?? "a");
  const labelB = qb?.label || String(qb?.params?.period ?? "b");
  const tone = toneOf(qb || qa) === "imports" ? "imports" : "exports";
  const full = tone === "imports" ? TOKENS.steel : TOKENS.sky;
  const light = tone === "imports" ? STEEL_RAMP[2] : SKY_RAMP[2];
  const text = tone === "imports" ? TOKENS.steelText : TOKENS.skyText;
  const anyFocus = rows.some((r) => r.focus);
  const share = opts.mode === "share";
  const diverging = opts.mode === "yoy" || opts.mode === "diff";

  // marks by code
  const byCode: Record<string, DrawnMark[]> = {};
  const failed: Array<{ d: DrawnMark; reason: string }> = [];
  marks.forEach((d) => {
    const p = d.mark.params || {};
    const codes: string[] = d.mark.kind === "highlight" ? p.codes || [] : d.mark.kind === "annotation" && p.x ? [String(p.x)] : [];
    if (!codes.length) return failed.push({ d, reason: `a ${d.mark.kind} mark on two snapshots needs codes` });
    const shown = codes.filter((c) => rows.some((r) => r.code === c));
    if (!shown.length) return failed.push({ d, reason: `${codes.join(", ")} is not among the rows shown` });
    shown.forEach((c) => (byCode[c] = (byCode[c] || []).concat([d])));
  });
  const numbered = new Set<string>();

  if (!rows.length) return <ErrorCard state={states[ib]} title="No rows to compare" message="The two snapshots have no rows in common." />;

  const valA = (r: PairRow) => (share ? r.aShare : r.a);
  const valB = (r: PairRow) => (share ? r.bShare : r.b);
  const fmt = (v: number | undefined) => (share ? pct(v) : v === undefined ? "–" : formatEur(v));
  const max = Math.max.apply(null, rows.map((r) => Math.max(Math.abs(valA(r) ?? 0), Math.abs(valB(r) ?? 0))).concat([0])) || 1;
  const dvals = rows.map((r) => r.value ?? 0);
  const neg = Math.max(0, -Math.min.apply(null, dvals.concat([0])));
  const pos = Math.max(0, Math.max.apply(null, dvals.concat([0])));
  const span = neg + pos || 1;
  const zero = (neg / span) * 100;
  const dfmt = (v: number | undefined) => (opts.mode === "yoy" ? pct(v, true) : v === undefined ? "–" : formatEur(v, true));
  const changeText = (r: PairRow) => {
    if (r.a === undefined) return `new in ${labelB}`;
    if (share) return points(r.bShare !== undefined && r.aShare !== undefined ? r.bShare - r.aShare : undefined);
    return `${formatEur(r.change ?? 0, true)}${r.growth !== undefined ? `, ${pct(r.growth, true)}` : ""}`;
  };

  const legend = diverging ? (
    panel.subtitle ? null : (
      <span style={{ fontSize: 13, color: TOKENS.muted }}>{opts.mode === "yoy" ? `Growth from ${labelA} to ${labelB}` : `Change from ${labelA} to ${labelB}`}</span>
    )
  ) : (
    <span style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 13, color: TOKENS.muted }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <span aria-hidden="true" style={{ width: 14, height: 6, borderRadius: 1, background: light }} />
        {labelA}
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <span aria-hidden="true" style={{ width: 14, height: 8, borderRadius: 1, background: full }} />
        {labelB}
      </span>
      {share && <span>share of the total</span>}
    </span>
  );

  const columns = ["Name", labelA, labelB, "Change", "Growth"].concat(share ? [`Share ${labelA}`, `Share ${labelB}`] : []);
  const tableRows = rows.map((r) =>
    ([rowName(r), r.a !== undefined ? formatEur(r.a) : "–", r.b !== undefined ? formatEur(r.b) : "–", r.change !== undefined ? formatEur(r.change, true) : "–", pct(r.growth, true)] as (string | number)[]).concat(
      share ? [pct(r.aShare), pct(r.bShare)] : [],
    ),
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
      {legend}
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12 }}>
        {rows.map((r) => {
          const ms = byCode[r.code] || [];
          const hl = ms.find((d) => d.mark.kind === "highlight");
          const notes = ms.filter((d) => d.mark.kind === "annotation");
          const emph = r.focus || !!hl;
          const barColour = anyFocus && !r.focus ? "#B7C0CA" : full;
          const dim = (d: DrawnMark) => (markOffContext(d.mark, ctx) ? OFF_CONTEXT_OPACITY : 1);
          return (
            <li key={r.code} style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14, paddingLeft: hl ? 10 : 0, borderLeft: hl ? `3px solid ${hl.color}` : undefined, opacity: hl ? dim(hl) : 1 }}>
              <span style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                <span style={{ display: "inline-flex", gap: 6, alignItems: "center", minWidth: 0 }}>
                  {ms
                    .filter((d) => d.showNumber && !numbered.has(d.mark.id) && (numbered.add(d.mark.id), true))
                    .map((d) => (
                      <span key={d.mark.id} style={{ display: "inline-flex", opacity: dim(d) }}>
                        <HtmlNumber n={d.number} color={d.color} />
                      </span>
                    ))}
                  <span title={r.name} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: emph ? 700 : 400, color: hl ? hl.ink : TOKENS.ink }}>
                    {rowName(r)}
                  </span>
                </span>
                <span style={{ display: "inline-flex", gap: 10, alignItems: "baseline", flex: "none", ...NUM_STYLE }}>
                  {diverging ? (
                    <>
                      {r.b !== undefined && <span style={{ fontSize: 12.5, color: TOKENS.muted }}>{`${formatEur(r.b)} in ${labelB}`}</span>}
                      <span style={{ fontWeight: 700, color: r.focus || !anyFocus ? text : TOKENS.ink2 }}>{dfmt(r.value)}</span>
                    </>
                  ) : (
                    <>
                      <span style={{ fontSize: 12.5, color: TOKENS.muted }}>{changeText(r)}</span>
                      <span style={{ fontWeight: 700, color: text }}>{fmt(valB(r))}</span>
                    </>
                  )}
                </span>
              </span>
              {diverging ? (
                <span style={{ position: "relative", height: 10, background: TOKENS.track }}>
                  <span style={{ position: "absolute", top: -2, bottom: -2, left: `${zero}%`, width: 1.5, background: TOKENS.muted }} />
                  <span
                    style={{
                      position: "absolute",
                      top: 0,
                      height: "100%",
                      left: `${(r.value ?? 0) >= 0 ? zero : zero - (Math.abs(r.value ?? 0) / span) * 100}%`,
                      width: `${(Math.abs(r.value ?? 0) / span) * 100}%`,
                      background: barColour,
                    }}
                  />
                </span>
              ) : (
                <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span style={{ height: 5, background: TOKENS.track }} title={`${labelA}: ${fmt(valA(r))}`}>
                    <span style={{ display: "block", height: "100%", width: `${(Math.abs(valA(r) ?? 0) / max) * 100}%`, background: anyFocus && !r.focus ? "#D5DBE1" : light }} />
                  </span>
                  <span style={{ height: 8, background: TOKENS.track }} title={`${labelB}: ${fmt(valB(r))}`}>
                    <span style={{ display: "block", height: "100%", width: `${(Math.abs(valB(r) ?? 0) / max) * 100}%`, background: barColour }} />
                  </span>
                </span>
              )}
              {notes.map((d) => (
                <span key={d.mark.id} style={{ fontSize: 12.5, fontWeight: 600, color: d.ink, opacity: dim(d) }}>
                  {d.mark.params?.label}
                </span>
              ))}
            </li>
          );
        })}
      </ol>
      {failed.map(({ d, reason }) => (
        <span key={d.mark.id} style={{ fontSize: 13, color: TOKENS.muted }}>
          Mark {d.number} could not be drawn: {reason}.
        </span>
      ))}
      <Caption panel={panel} queries={queries} states={states} ctx={ctx} />
      <TableToggle>
        <DataTable columns={columns} rows={tableRows} caption={`${panel.title}: ${labelA} and ${labelB}.`} />
      </TableToggle>
    </div>
  );
};

// ------------------------------------------------------------------------------------------ caption

const Caption: React.FC<{ panel: Panel; queries: Query[]; states: ReturnType<typeof useQueries>; ctx: ReturnType<typeof contextOf> }> = ({ panel, queries, states, ctx }) => {
  const facts = useFacts(substituteFacts(panel.facts, ctx), keyed(queries, states));
  if (!panel.text) return null;
  const agent = isAgentOwner(panel.agent);
  return (
    <p style={{ margin: 0, fontFamily: agent ? FONT_AGENT : FONT_UI, fontSize: agent ? 15.5 : 13, lineHeight: 1.5, color: agent ? TOKENS.ink : TOKENS.muted, maxWidth: "62em" }}>
      <FactText template={panel.text} facts={facts} />
    </p>
  );
};

// ------------------------------------------------------------------------------------------ the panel

export const ComparePanel: React.FC<ComparePanelProps> = ({ panel, marks = [], height = 300 }) => {
  // The panel's context: the page's, with the chart's own year and goods (contract 1.5).
  const ctx = usePageContext();
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const states = useQueries(queries);
  const layout = compareLayout(panel);
  const opts = readOptions(panel);

  if (!layout) {
    return (
      <ErrorCard
        title="This comparison could not be drawn"
        message="A comparison reads 1 to 6 series from /insights/series, or two snapshots keyed a and b from /insights/ranking or /insights/compare."
      />
    );
  }
  const problem = firstProblem(states);
  if (problem) return problemCard(problem, layout === "series" ? height : 200);
  if (layout === "pair") return <PairView panel={panel} queries={queries} states={states} opts={opts} marks={marks} ctx={ctx} />;
  return <SeriesView panel={panel} queries={queries} states={states} opts={opts} marks={marks} height={height} ctx={ctx} />;
};

export default ComparePanel;
