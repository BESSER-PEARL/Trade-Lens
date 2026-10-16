// Waterfall: what made up a change. It starts at the earlier total, steps through the
// largest movers, closes with one "everything else" step, and ends at the later total.
// When the totals dwarf the steps, the axis starts above zero (said under the chart, and
// the total bars carry a break), so a +€0.85 bn step stays visible next to €16 bn.
// Rises are filled in the flow's data colour, falls are hollow with a 45-degree hatch of
// the same hue, totals are ink. Agents' marks outline their rows in the pen.

import React, { useMemo } from "react";
import { ChartTip } from "./ChartTip";
import { fmtEur, fmtEurTick, niceTicks, truncate } from "./format";
import { FONT_UI, TOKENS, toneColor } from "./palette";
import type { PenMark } from "./palette";
import { SvgBadge, useSvgId } from "./parts";
import { marksFor } from "./panelData";
import { useMeasure } from "./useMeasure";
import { useRoving } from "./useRoving";

export interface WaterfallStep {
  code: string;
  name: string;
  /** The change this item contributes (signed). */
  value: number;
  previous?: number;
  current?: number;
}

export interface WaterfallRow {
  code: string;
  name: string;
  kind: "total" | "step" | "other";
  from: number;
  to: number;
  delta: number;
  step?: WaterfallStep;
}

/** The rows of the chart: start total, steps, the rest, end total. */
export function waterfallRows(
  start: { label: string; value: number } | null,
  end: { label: string; value: number } | null,
  steps: WaterfallStep[],
  maxSteps: number,
  showOther: boolean,
): WaterfallRow[] {
  const sorted = steps.slice().sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const shown = sorted.slice(0, maxSteps);
  const out: WaterfallRow[] = [];
  let cum = start ? start.value : 0;
  if (start) out.push({ code: "__start", name: start.label, kind: "total", from: 0, to: start.value, delta: start.value });
  shown.forEach((s) => {
    out.push({ code: s.code, name: s.name, kind: "step", from: cum, to: cum + s.value, delta: s.value, step: s });
    cum += s.value;
  });
  if (start && end) {
    const other = end.value - cum;
    if (showOther && Math.abs(other) > Math.abs(end.value - start.value) * 1e-6) {
      out.push({ code: "__other", name: "Everything else", kind: "other", from: cum, to: end.value, delta: other });
    }
    out.push({ code: "__end", name: end.label, kind: "total", from: 0, to: end.value, delta: end.value });
  } else {
    out.push({ code: "__net", name: "Net change of the items shown", kind: "total", from: 0, to: cum, delta: cum });
  }
  return out;
}

export interface WaterfallProps {
  title: string;
  rows: WaterfallRow[];
  tone?: string;
  marks?: PenMark[];
  rowHeight?: number;
}

export const Waterfall: React.FC<WaterfallProps> = ({ title, rows, tone = "exports", marks = [], rowHeight = 32 }) => {
  const { ref, width } = useMeasure<HTMLDivElement>(640);
  const colour = toneColor(tone).fill;
  const hatchId = useSvgId("wf-hatch");
  const roving = useRoving<SVGGElement>(rows.length);

  const nTicks = width < 520 ? 3 : 5;
  const scale = useMemo(() => {
    const ends = rows.reduce((acc: number[], r) => (r.kind === "total" ? acc.concat([r.to]) : acc.concat([r.from, r.to])), []);
    const lo0 = Math.min.apply(null, ends);
    const hi0 = Math.max.apply(null, ends);
    const span = Math.max(1, hi0 - lo0);
    const hasTotals = rows.some((r) => r.kind === "total" && r.code !== "__net");
    // Truncate when the smallest end is far from zero compared with the spread of the steps.
    const truncated = hasTotals && lo0 > 0 && lo0 > span * 1.2;
    let lo = truncated ? lo0 - span * 0.45 : Math.min(0, lo0);
    let hi = Math.max(0, hi0);
    const ticks = niceTicks(lo, hi, nTicks);
    if (truncated) {
      const step = ticks.length > 1 ? ticks[1] - ticks[0] : span;
      lo = Math.floor(lo / step) * step;
    }
    hi = Math.max(hi, ticks[ticks.length - 1] ?? hi);
    return { lo, hi, truncated, ticks: niceTicks(lo, hi, nTicks).filter((t) => t >= lo && t <= hi) };
  }, [rows, nTicks]);

  const gutterL = Math.min(240, Math.max(118, width * 0.32));
  const gutterR = 92;
  const plotW = Math.max(80, width - gutterL - gutterR);
  const top = 24;
  const h = top + rows.length * rowHeight + 6;
  const x = (v: number) => gutterL + ((v - scale.lo) / (scale.hi - scale.lo || 1)) * plotW;
  const bar = Math.min(20, rowHeight - 10);
  const maxChars = Math.floor((gutterL - 10) / 6.4);
  const spots: Record<string, string> = {};
  rows.forEach((r) => marksFor(marks, r.code).forEach((m) => {
    if (spots[m.id] === undefined) spots[m.id] = r.code;
  }));
  const fmtDelta = (r: WaterfallRow) => (r.kind === "total" ? fmtEur(r.delta) : fmtEur(r.delta, true));

  const hovered = roving.hover ? rows[roving.hover.i] : undefined;

  return (
    <div ref={ref} style={{ width: "100%", fontFamily: FONT_UI, color: TOKENS.ink }}>
      <div ref={roving.wrap} style={{ position: "relative" }}>
        <svg
          viewBox={`0 0 ${width} ${h}`}
          width={width}
          height={h}
          style={{ display: "block", width: "100%", height: "auto" }}
          role="group"
          aria-label={title}
          onKeyDown={roving.onKeyDown}
          onMouseLeave={() => roving.setHover(null)}
        >
          <defs>
            <pattern id={hatchId} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="6" stroke={colour} strokeWidth="1.2" />
            </pattern>
          </defs>
          <g aria-hidden="true" fontSize={11} fill={TOKENS.muted}>
            {scale.ticks.map((t) => (
              <g key={t}>
                <line x1={x(t)} x2={x(t)} y1={top - 4} y2={h - 4} stroke={t === 0 ? TOKENS.field : TOKENS.grid} strokeWidth={1} />
                <text x={x(t)} y={top - 9} textAnchor="middle" style={{ fontVariantNumeric: "tabular-nums" }}>
                  {fmtEurTick(t)}
                </text>
              </g>
            ))}
          </g>
          {rows.map((r, i) => {
            const y = top + i * rowHeight + (rowHeight - bar) / 2;
            const isTotal = r.kind === "total";
            const a = isTotal ? Math.max(scale.lo, Math.min(r.from, r.to)) : Math.min(r.from, r.to);
            const b = Math.max(r.from, r.to);
            const x0 = x(a);
            const x1 = Math.max(x0 + 1.5, x(b));
            const up = r.delta >= 0;
            const ms = marksFor(marks, r.code);
            const label = `${r.name}: ${fmtDelta(r)}${r.kind === "step" ? `, from ${fmtEur(r.from)} to ${fmtEur(r.to)} running total` : ""}`;
            const rx = Math.min(4, (x1 - x0) / 2);
            const d =
              up || isTotal
                ? `M${x0},${y} H${x1 - rx} a${rx},${rx} 0 0 1 ${rx},${rx} V${y + bar - rx} a${rx},${rx} 0 0 1 ${-rx},${rx} H${x0} Z`
                : `M${x1},${y} H${x0 + rx} a${rx},${rx} 0 0 0 ${-rx},${rx} V${y + bar - rx} a${rx},${rx} 0 0 0 ${rx},${rx} H${x1} Z`;
            const next = rows[i + 1];
            const valueText = fmtDelta(r);
            const valueX = up || isTotal ? x1 + 6 : x0 - 6;
            const badges = ms.filter((m) => m.showNumber && spots[m.id] === r.code);
            const badgeX = up || isTotal ? valueX + 6.4 * valueText.length + 14 : x1 + 14;
            const isHover = roving.hover?.i === i;
            return (
              <g
                key={r.code}
                ref={roving.register(i)}
                {...roving.itemProps(i)}
                role="img"
                aria-label={label}
                style={{ outline: "none" }}
              >
                <rect x={0} y={top + i * rowHeight} width={width} height={rowHeight} rx={4} fill={isHover ? TOKENS.ruleSoft : "transparent"} />
                <text
                  x={gutterL - 10}
                  y={y + bar / 2 + 4}
                  textAnchor="end"
                  fontSize={12.5}
                  fill={ms.length ? ms[0].ink : isTotal ? TOKENS.ink : TOKENS.ink2}
                  fontWeight={isTotal || ms.length ? 700 : 400}
                >
                  <title>{r.name}</title>
                  {truncate(r.name, maxChars)}
                </text>
                {isTotal ? (
                  <path d={d} fill={TOKENS.ink} />
                ) : up ? (
                  <path d={d} fill={r.kind === "other" ? TOKENS.field : colour} />
                ) : (
                  <>
                    <path d={d} fill={TOKENS.surface} />
                    <path d={d} fill={`url(#${hatchId})`} stroke={r.kind === "other" ? TOKENS.field : colour} strokeWidth={1.5} />
                  </>
                )}
                {isTotal && scale.truncated && (
                  // The break: the bar does not start at zero.
                  <path d={`M${x0 + 7},${y - 2} l-5,${bar + 4} M${x0 + 12},${y - 2} l-5,${bar + 4}`} stroke={TOKENS.surface} strokeWidth={2.5} />
                )}
                {ms.map((m) => (
                  <path key={m.id} d={d} fill="none" stroke={m.color} strokeWidth={2.5} opacity={m.opacity} />
                ))}
                {next && next.kind !== "total" && (
                  <line x1={x(r.to)} x2={x(r.to)} y1={y + bar} y2={y + rowHeight} stroke={TOKENS.muted} strokeWidth={1} strokeDasharray="2 2" />
                )}
                <text
                  x={valueX}
                  y={y + bar / 2 + 4}
                  textAnchor={up || isTotal ? "start" : "end"}
                  fontSize={12}
                  fontWeight={isTotal ? 700 : 600}
                  fill={TOKENS.ink}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {valueText}
                </text>
                {badges.map((m, k) => (
                  <SvgBadge key={m.id} x={Math.min(width - 12, badgeX + k * 22)} y={y + bar / 2} n={m.n} color={m.color} opacity={m.opacity} />
                ))}
              </g>
            );
          })}
        </svg>
        <ChartTip
          x={roving.hover?.x ?? 0}
          y={roving.hover?.y ?? 0}
          width={width}
          visible={!!hovered}
          title={hovered?.name}
          rows={
            hovered
              ? [
                  {
                    value: fmtDelta(hovered),
                    label: hovered.kind === "total" ? "total" : hovered.delta >= 0 ? "rise" : "fall",
                    swatch: hovered.kind === "total" ? TOKENS.ink : colour,
                  },
                  ...(hovered.step && hovered.step.previous !== undefined && hovered.step.current !== undefined
                    ? [{ value: `${fmtEur(hovered.step.previous)} to ${fmtEur(hovered.step.current)}`, label: "before and after" }]
                    : []),
                  ...(hovered.kind !== "total" ? [{ value: fmtEur(hovered.to), label: "running total" }] : []),
                ]
              : []
          }
        />
      </div>
      {scale.truncated && (
        <span style={{ display: "block", marginTop: 4, fontSize: 12.5, color: TOKENS.muted }}>
          The axis starts at {fmtEur(scale.lo)}, so the steps can be read; the total bars are cut.
        </span>
      )}
    </div>
  );
};
