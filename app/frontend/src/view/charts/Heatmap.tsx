// Heatmap: a rows x cols grid in one sequential hue (the flow's data colour) or a diverging
// sky/steel scale (above / below the reference). Months by years, codes by years and Ivan's
// seasonal profiles all use it. Values are never printed in every cell: the readout (hover
// or keyboard focus, 2-D arrow keys) and the table hold them. Trend breaks from agents are
// drawn in their pen: on months by years, a staircase between the months before and from
// the break, with the break's cell outlined; on codes by years, a rule before the year.

import React, { useMemo } from "react";
import { ChartTip } from "./ChartTip";
import type { TipRow } from "./ChartTip";
import { FONT_UI, SKY_RAMP, STEEL_RAMP, TOKENS, rampFor, toneColor } from "./palette";
import { RampLegend, SvgBadge } from "./parts";
import { truncate } from "./format";
import { useMeasure } from "./useMeasure";
import { useRoving } from "./useRoving";

export interface HeatBreak {
  id: string;
  color: string;
  n: number;
  showNumber: boolean;
  opacity: number;
  label?: string;
  /** staircase: the break is the cell (row, col), in reading order; column: a rule before col. */
  mode: "staircase" | "column";
  row?: string;
  col: string;
}

export interface HeatmapProps {
  rows: string[];
  cols: string[];
  /** values[r][c], null where there is no data. */
  values: Array<Array<number | null>>;
  title: string;
  tone?: string;
  scale?: "sequential" | "diverging";
  /** Reverse the ramp (ranks: 1 is the darkest). */
  reverse?: boolean;
  format: (v: number) => string;
  rowLabel?: (r: string) => string;
  colLabel?: (c: string) => string;
  /** The readout's title of a cell. */
  cellTitle?: (r: string, c: string) => string;
  /** Extra readout rows of a cell (the value itself comes first). */
  cellExtra?: (ri: number, ci: number) => TipRow[];
  valueLabel?: string;
  legendTitle?: string;
  breaks?: HeatBreak[];
  /** Height of a row when there are many rows. */
  rowHeight?: number;
  maxCellWidth?: number;
}

export const Heatmap: React.FC<HeatmapProps> = ({
  rows,
  cols,
  values,
  title,
  tone = "exports",
  scale = "sequential",
  reverse = false,
  format,
  rowLabel = (r) => r,
  colLabel = (c) => c,
  cellTitle,
  cellExtra,
  valueLabel,
  legendTitle,
  breaks = [],
  rowHeight,
  maxCellWidth = 64,
}) => {
  const { ref, width } = useMeasure<HTMLDivElement>(640);
  const ramp = rampFor(tone);
  const roving = useRoving<SVGRectElement>(rows.length * cols.length, { grid: { cols: cols.length } });

  const [lo, hi] = useMemo<[number, number]>(() => {
    let a = Infinity;
    let b = -Infinity;
    values.forEach((row) => row.forEach((v) => {
      if (v === null || !Number.isFinite(v)) return;
      if (v < a) a = v;
      if (v > b) b = v;
    }));
    if (!Number.isFinite(a)) return [0, 1];
    if (scale === "diverging") {
      const m = Math.max(Math.abs(a), Math.abs(b)) || 1;
      return [-m, m];
    }
    return [a, b];
  }, [values, scale]);

  const logScale = scale === "sequential" && !reverse && lo > 0 && hi / lo > 20;
  const longest = Math.max(2, ...rows.map((r) => rowLabel(r).length));
  const gutterL = Math.min(Math.max(44, width * 0.34), 10 + 6.3 * longest);
  const gutterT = 22;
  const gap = 2;
  const hasBadges = breaks.some((b) => b.showNumber);
  const gutterR = hasBadges ? 30 : 4;
  const cellW = Math.max(6, Math.min(maxCellWidth, Math.floor((width - gutterL - gutterR) / Math.max(1, cols.length)) - gap));
  const cellH = rowHeight || Math.max(12, Math.min(cellW, rows.length > 16 ? 18 : 30));
  const w = gutterL + cols.length * (cellW + gap) + gutterR;
  const columnBreaks = breaks.some((b) => b.mode === "column" && b.showNumber);
  const h = gutterT + rows.length * (cellH + gap) + 2 + (columnBreaks ? 24 : 0);
  const maxChars = Math.floor((gutterL - 8) / 6.3);
  const xOf = (j: number) => gutterL + j * (cellW + gap);
  const yOf = (i: number) => gutterT + i * (cellH + gap);

  const colourOf = (v: number | null): string => {
    if (v === null || !Number.isFinite(v)) return TOKENS.ruleSoft;
    if (scale === "diverging") {
      const m = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
      const t = Math.min(1, Math.abs(v) / m);
      const step = t < 0.08 ? 0 : t < 0.25 ? 1 : t < 0.45 ? 2 : t < 0.65 ? 3 : t < 0.85 ? 4 : 5;
      return (v >= 0 ? SKY_RAMP : STEEL_RAMP)[step + 1];
    }
    // Shares and values spread over orders of magnitude: a log scale keeps the small ones apart.
    let t = hi > lo ? (logScale ? (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) : (v - lo) / (hi - lo)) : 1;
    if (reverse) t = 1 - t;
    return ramp[Math.min(6, Math.max(0, Math.floor(t * 6.999)))];
  };

  const what = valueLabel || (tone === "imports" ? "imports" : "exports");
  const hov = roving.hover;
  const hr = hov ? Math.floor(hov.i / cols.length) : -1;
  const hc = hov ? hov.i % cols.length : -1;
  const tipRows: TipRow[] = (() => {
    if (!hov || hr < 0) return [];
    const v = values[hr]?.[hc];
    const base: TipRow[] =
      v === null || v === undefined || !Number.isFinite(v) ? [{ value: "no data" }] : [{ value: format(v), label: what, swatch: toneColor(tone).fill }];
    return base.concat(cellExtra ? cellExtra(hr, hc) : []);
  })();

  const steps =
    scale === "diverging"
      ? [STEEL_RAMP[6], STEEL_RAMP[4], STEEL_RAMP[2], SKY_RAMP[2], SKY_RAMP[4], SKY_RAMP[6]]
      : reverse
        ? ramp.slice(0, 7).reverse()
        : ramp.slice(0, 7);
  const legendLabels =
    scale === "diverging"
      ? [format(lo), "", "", format(0), "", format(hi)]
      : reverse
        ? [format(hi), "", "", "", "", "", format(lo)]
        : logScale
          ? [format(lo), "", "", format(Math.sqrt(lo * hi)), "", "", format(hi)]
          : [format(lo), "", "", "", "", "", format(hi)];

  // Column labels thin out when cells are narrow.
  const colEvery = Math.max(1, Math.ceil(30 / (cellW + gap)));
  const rowEvery = Math.max(1, Math.ceil(12 / (cellH + gap)));

  const breakShapes = breaks.map((b) => {
    const j = cols.indexOf(b.col);
    const i = b.row !== undefined ? rows.indexOf(b.row) : -1;
    if (j < 0 || (b.mode === "staircase" && i < 0)) return { b, d: "", cell: null as null | { x: number; y: number }, badge: null as null | { x: number; y: number } };
    const left = gutterL - 1;
    const right = xOf(cols.length - 1) + cellW + 1;
    if (b.mode === "column") {
      const x = xOf(j) - gap / 2;
      const bottom = yOf(rows.length - 1) + cellH + 2;
      return { b, d: `M${x},${gutterT - 4} V${bottom + 4}`, cell: null, badge: { x, y: bottom + 14 } };
    }
    const xCell = xOf(j) - gap / 2;
    const yTop = yOf(i) - gap / 2;
    const yBot = yOf(i) + cellH + gap / 2;
    // Before the break: every earlier row, and the cells of its own row left of it.
    const d = j === 0 ? `M${left},${yTop} H${right}` : `M${right},${yTop} H${xCell} V${yBot} H${left}`;
    return { b, d, cell: { x: xOf(j), y: yOf(i) }, badge: { x: right + 16, y: yOf(i) + cellH / 2 } };
  });

  return (
    <div ref={ref} style={{ width: "100%", fontFamily: FONT_UI, color: TOKENS.ink }}>
      <div ref={roving.wrap} style={{ position: "relative", width: "100%" }}>
        <svg
          viewBox={`0 0 ${w} ${h}`}
          width={w}
          height={h}
          style={{ display: "block", maxWidth: "100%", height: "auto" }}
          role="group"
          aria-label={title}
          onKeyDown={roving.onKeyDown}
          onMouseLeave={() => roving.setHover(null)}
        >
          <g aria-hidden="true" fontSize={11} fill={TOKENS.muted}>
            {cols.map((c, j) =>
              j % colEvery === 0 ? (
                <text key={c} x={xOf(j) + cellW / 2} y={gutterT - 8} textAnchor="middle" style={{ fontVariantNumeric: "tabular-nums" }}>
                  {colLabel(c)}
                </text>
              ) : null,
            )}
            {rows.map((r, i) =>
              i % rowEvery === 0 ? (
                <text key={r} x={gutterL - 7} y={yOf(i) + cellH / 2 + 4} textAnchor="end" style={{ fontVariantNumeric: "tabular-nums" }}>
                  <title>{rowLabel(r)}</title>
                  {truncate(rowLabel(r), maxChars)}
                </text>
              ) : null,
            )}
          </g>
          {rows.map((r, i) =>
            cols.map((c, j) => {
              const k = i * cols.length + j;
              const v = values[i]?.[j] ?? null;
              const label = `${cellTitle ? cellTitle(r, c) : `${rowLabel(r)}, ${colLabel(c)}`}: ${v === null || !Number.isFinite(v) ? "no data" : format(v)}`;
              return (
                <rect
                  key={k}
                  ref={roving.register(k)}
                  {...roving.itemProps(k)}
                  x={xOf(j)}
                  y={yOf(i)}
                  width={cellW}
                  height={cellH}
                  rx={2}
                  fill={colourOf(v)}
                  stroke={hov && hov.i === k ? TOKENS.ink : "none"}
                  strokeWidth={hov && hov.i === k ? 1.5 : 0}
                  role="img"
                  aria-label={label}
                  style={{ outline: "none" }}
                />
              );
            }),
          )}
          {breakShapes.map(({ b, d, cell, badge }) =>
            d ? (
              <g key={b.id} aria-hidden="true" pointerEvents="none" opacity={b.opacity}>
                <path d={d} fill="none" stroke={TOKENS.surface} strokeWidth={5} strokeLinejoin="round" strokeLinecap="round" />
                <path d={d} fill="none" stroke={b.color} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
                {cell && <rect x={cell.x - 1} y={cell.y - 1} width={cellW + 2} height={cellH + 2} rx={3} fill="none" stroke={b.color} strokeWidth={2} />}
                {b.showNumber && badge && <SvgBadge x={badge.x} y={badge.y} n={b.n} color={b.color} r={9} />}
              </g>
            ) : null,
          )}
        </svg>
        <ChartTip
          x={hov?.x ?? 0}
          y={hov?.y ?? 0}
          width={width}
          visible={!!hov && hr >= 0}
          title={hov && hr >= 0 ? (cellTitle ? cellTitle(rows[hr], cols[hc]) : `${rowLabel(rows[hr])}, ${colLabel(cols[hc])}`) : undefined}
          rows={tipRows}
        />
      </div>
      <div style={{ marginTop: 10 }}>
        <RampLegend title={legendTitle || what} steps={steps} labels={legendLabels} />
      </div>
    </div>
  );
};
