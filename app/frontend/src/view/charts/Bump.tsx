// Bump chart: ranks over the years, one line per partner (or product), rank 1 at the top.
// Every line is named at both ends, by Luxembourg's rank of that partner: "1 Germany" on
// the left in the first year, "Germany 1" on the right in the last. The lines named in
// options.labels (or the top three of the last year) are drawn in the data colour; the
// rest stay quiet grey, and hovering or focusing any line brings it forward. Agents'
// highlights draw their lines in the pen, with the number at the right end.

import React, { useMemo } from "react";
import { ChartTip } from "./ChartTip";
import { fmtOrdinal, truncate } from "./format";
import { FONT_UI, TOKENS, toneColor } from "./palette";
import type { PenMark } from "./palette";
import { SvgBadge } from "./parts";
import { marksFor } from "./panelData";
import { useMeasure } from "./useMeasure";
import { useRoving } from "./useRoving";

export interface BumpSeries {
  code: string;
  name: string;
  ranks: Array<number | null>;
}

export interface BumpProps {
  periods: string[];
  series: BumpSeries[];
  title: string;
  tone?: string;
  /** Codes drawn in the data colour. */
  emphasis?: string[];
  marks?: PenMark[];
  /** Ranks below this are not drawn (the line breaks). */
  maxRank?: number;
  rowGap?: number;
  /** Click or Enter on a line: the page's own highlight (agent "user"). */
  onSelect?: (s: BumpSeries) => void;
}

function lastOf(ranks: Array<number | null>): { i: number; r: number } | null {
  for (let i = ranks.length - 1; i >= 0; i--) if (ranks[i] !== null) return { i, r: ranks[i] as number };
  return null;
}
function firstOf(ranks: Array<number | null>): { i: number; r: number } | null {
  for (let i = 0; i < ranks.length; i++) if (ranks[i] !== null) return { i, r: ranks[i] as number };
  return null;
}

export const Bump: React.FC<BumpProps> = ({ periods, series, title, tone = "exports", emphasis = [], marks = [], maxRank, rowGap = 26, onSelect }) => {
  const { ref, width } = useMeasure<HTMLDivElement>(640);
  const colour = toneColor(tone).fill;
  const deep = Math.max(1, ...series.map((s) => Math.max(0, ...s.ranks.map((r) => (r === null ? 0 : r)))));
  const bottomRank = Math.min(maxRank || deep, deep, 24);
  const ordered = useMemo(
    () => series.slice().sort((a, b) => (lastOf(a.ranks)?.r ?? 999) - (lastOf(b.ranks)?.r ?? 999)),
    [series],
  );
  const roving = useRoving<SVGGElement>(ordered.length, { onActivate: onSelect ? (i) => ordered[i] && onSelect(ordered[i]) : undefined });
  const narrow = width < 520;
  const nameChars = narrow ? 11 : 18;
  const gutterL = narrow ? 92 : 150;
  const gutterR = narrow ? 104 : 170;
  const top = 28;
  const h = top + (bottomRank - 1) * rowGap + 22;
  const plotW = Math.max(60, width - gutterL - gutterR);
  const x = (i: number) => gutterL + (periods.length > 1 ? (i / (periods.length - 1)) * plotW : plotW / 2);
  const y = (r: number) => top + (r - 1) * rowGap;
  const every = Math.max(1, Math.ceil(38 / (plotW / Math.max(1, periods.length - 1))));
  const hoverCode = roving.hover ? ordered[roving.hover.i]?.code : null;

  const pathOf = (s: BumpSeries) => {
    let d = "";
    let pen = false;
    s.ranks.forEach((r, i) => {
      if (r === null || r > bottomRank) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(r).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  };

  const styleOf = (s: BumpSeries) => {
    const ms = marksFor(marks, s.code);
    if (hoverCode === s.code) return { stroke: TOKENS.ink, width: 3.5, label: TOKENS.ink, weight: 700, mark: ms[0] };
    if (ms.length) return { stroke: ms[0].color, width: 3.25, label: ms[0].ink, weight: 700, mark: ms[0] };
    if (emphasis.indexOf(s.code) >= 0) return { stroke: colour, width: 3, label: TOKENS.ink, weight: 700, mark: undefined };
    return { stroke: TOKENS.field, width: 1.75, label: TOKENS.muted, weight: 400, mark: undefined };
  };
  // Quiet lines first, the named ones on top. The order never follows the hover (moving a
  // focused node would blur it); the hovered line is drawn again on top instead.
  const rest = (s: BumpSeries) => (marksFor(marks, s.code).length ? 2 : emphasis.indexOf(s.code) >= 0 ? 1 : 0);
  const drawOrder = ordered.map((s, i) => ({ s, i, st: styleOf(s) })).sort((a, b) => rest(a.s) - rest(b.s) || a.i - b.i);
  const hovered = roving.hover ? ordered[roving.hover.i] : undefined;
  const best = (s: BumpSeries) => Math.min.apply(null, s.ranks.filter((r): r is number => r !== null).concat([999]));

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
          <g aria-hidden="true" fontSize={11} fill={TOKENS.muted}>
            {periods.map((p, i) =>
              (periods.length - 1 - i) % every === 0 ? (
                <g key={p}>
                  <line x1={x(i)} x2={x(i)} y1={top - 8} y2={h - 12} stroke={TOKENS.grid} />
                  <text x={x(i)} y={top - 14} textAnchor="middle" style={{ fontVariantNumeric: "tabular-nums" }}>
                    {p}
                  </text>
                </g>
              ) : null,
            )}
          </g>
          {drawOrder.map(({ s, i, st }) => {
            const d = pathOf(s);
            const f = firstOf(s.ranks);
            const l = lastOf(s.ranks);
            const quiet = !!hoverCode && hoverCode !== s.code;
            const label = `${s.name}: ${f ? `${fmtOrdinal(f.r)} in ${periods[f.i]}` : "no rank"}${l && f && l.i !== f.i ? `, ${fmtOrdinal(l.r)} in ${periods[l.i]}` : ""}, best ${fmtOrdinal(best(s))}`;
            const leftOk = f && f.r <= bottomRank;
            const rightOk = l && l.r <= bottomRank;
            return (
              <g
                key={s.code}
                ref={roving.register(i)}
                {...roving.itemProps(i)}
                role={onSelect ? "button" : "img"}
                aria-label={label}
                style={{ outline: "none", cursor: onSelect ? "pointer" : "default" }}
                opacity={quiet ? 0.45 : st.mark ? st.mark.opacity : 1}
              >
                <path d={d} fill="none" stroke="transparent" strokeWidth={16} strokeLinecap="round" strokeLinejoin="round" />
                <path d={d} fill="none" stroke={st.stroke} strokeWidth={st.width} strokeLinecap="round" strokeLinejoin="round" />
                {s.ranks.map((r, k) =>
                  r !== null && r <= bottomRank ? (
                    <circle key={k} cx={x(k)} cy={y(r)} r={st.width > 2 ? 4 : 2.75} fill={st.stroke} stroke={TOKENS.surface} strokeWidth={1.5} />
                  ) : null,
                )}
                {leftOk && f && (
                  <text x={x(f.i) - 9} y={y(f.r) + 4} textAnchor="end" fontSize={12} fill={st.label} fontWeight={st.weight}>
                    <tspan style={{ fontVariantNumeric: "tabular-nums" }} fill={TOKENS.muted} fontWeight={400}>
                      {f.r}
                    </tspan>{" "}
                    {truncate(s.name, nameChars)}
                  </text>
                )}
                {rightOk && l && (
                  <text x={x(l.i) + 9} y={y(l.r) + 4} textAnchor="start" fontSize={12} fill={st.label} fontWeight={st.weight}>
                    {truncate(s.name, nameChars)}{" "}
                    <tspan style={{ fontVariantNumeric: "tabular-nums" }} fill={TOKENS.muted} fontWeight={400}>
                      {l.r}
                    </tspan>
                  </text>
                )}
                {st.mark && st.mark.showNumber && rightOk && l && (
                  <SvgBadge x={Math.min(width - 11, x(l.i) + 14 + 6.6 * (truncate(s.name, nameChars).length + 3))} y={y(l.r)} n={st.mark.n} color={st.mark.color} r={9} />
                )}
              </g>
            );
          })}
          {hovered && (
            <g aria-hidden="true" pointerEvents="none">
              <path d={pathOf(hovered)} fill="none" stroke={TOKENS.surface} strokeWidth={7} strokeLinecap="round" strokeLinejoin="round" />
              <path d={pathOf(hovered)} fill="none" stroke={TOKENS.ink} strokeWidth={3.5} strokeLinecap="round" strokeLinejoin="round" />
              {hovered.ranks.map((r, k) =>
                r !== null && r <= bottomRank ? <circle key={k} cx={x(k)} cy={y(r)} r={4} fill={TOKENS.ink} stroke={TOKENS.surface} strokeWidth={1.5} /> : null,
              )}
            </g>
          )}
        </svg>
        <ChartTip
          x={roving.hover?.x ?? 0}
          y={roving.hover?.y ?? 0}
          width={width}
          visible={!!hovered}
          title={hovered?.name}
          rows={
            hovered
              ? periods
                  .map((p, i) => ({ p, r: hovered.ranks[i] }))
                  .filter((x2, i, all) => i === 0 || i === all.length - 1 || x2.r !== all[i - 1].r)
                  .slice(-6)
                  .map((x2) => ({ value: x2.r === null ? "not ranked" : fmtOrdinal(x2.r), label: x2.p }))
              : []
          }
        />
      </div>
    </div>
  );
};
