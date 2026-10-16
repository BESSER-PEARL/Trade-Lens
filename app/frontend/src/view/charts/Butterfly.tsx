// Butterfly: imports (steel, growing left) and exports (sky, growing right) per code on one
// scale, names in the middle. The honest ranking next to the flow map, and a compact stand-in
// for the crossroads on a phone. Agents' highlight marks outline both bars in their pen.

import React, { useMemo } from "react";
import { ChartTip } from "./ChartTip";
import { fmtEur, fmtEurTick, niceTicks, truncate } from "./format";
import { FONT_UI, TOKENS } from "./palette";
import type { PenMark } from "./palette";
import { SvgBadge } from "./parts";
import { marksFor } from "./panelData";
import { useMeasure } from "./useMeasure";
import { useRoving } from "./useRoving";

export interface ButterflyRow {
  code: string;
  name: string;
  imports: number | null;
  exports: number | null;
}

export interface ButterflyProps {
  rows: ButterflyRow[];
  title: string;
  marks?: PenMark[];
  rowHeight?: number;
  leftLabel?: string;
  rightLabel?: string;
  /** Click or Enter on a row: the page's own highlight (agent "user"). */
  onSelect?: (row: ButterflyRow) => void;
}

export const Butterfly: React.FC<ButterflyProps> = ({ rows, title, marks = [], rowHeight = 30, leftLabel = "Bought", rightLabel = "Sold", onSelect }) => {
  const { ref, width } = useMeasure<HTMLDivElement>(640);
  const roving = useRoving<SVGGElement>(rows.length, { onActivate: onSelect ? (i) => rows[i] && onSelect(rows[i]) : undefined });
  const max = Math.max(1, ...rows.map((r) => Math.max(r.imports || 0, r.exports || 0)));
  const ticks = useMemo(() => niceTicks(0, max, width < 520 ? 2 : 4), [max, width]);
  const topV = Math.max(max, ticks[ticks.length - 1] || 0);
  const narrow = width < 480;
  const head = 30;
  const mid = Math.min(170, Math.max(92, width * 0.24));
  const labelPad = narrow ? 50 : 64;
  const side = Math.max(40, (width - mid) / 2 - labelPad);
  const cx = width / 2;
  const h = head + rows.length * rowHeight + 6;
  const bar = Math.min(18, rowHeight - 10);
  const sx = (v: number | null) => ((v || 0) / topV) * side;
  const li = cx - mid / 2;
  const ri = cx + mid / 2;
  const spots: Record<string, string> = {};
  rows.forEach((r) => marksFor(marks, r.code).forEach((m) => {
    if (spots[m.id] === undefined) spots[m.id] = r.code;
  }));
  const rounded = (x0: number, x1: number, y: number, dir: "left" | "right") => {
    const r = Math.min(4, Math.abs(x1 - x0) / 2);
    if (Math.abs(x1 - x0) < 0.5) return "";
    return dir === "right"
      ? `M${x0},${y} H${x1 - r} a${r},${r} 0 0 1 ${r},${r} V${y + bar - r} a${r},${r} 0 0 1 ${-r},${r} H${x0} Z`
      : `M${x1},${y} H${x0 + r} a${r},${r} 0 0 0 ${-r},${r} V${y + bar - r} a${r},${r} 0 0 0 ${r},${r} H${x1} Z`;
  };
  const fmtSide = (v: number | null) => (v === null ? "–" : fmtEur(v));
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
          <g aria-hidden="true" fontSize={12.5} fontWeight={700}>
            <text x={li - 4} y={13} textAnchor="end" fill={TOKENS.steelText}>
              {leftLabel}
            </text>
            <text x={ri + 4} y={13} textAnchor="start" fill={TOKENS.skyText}>
              {rightLabel}
            </text>
          </g>
          <g aria-hidden="true" fontSize={11} fill={TOKENS.muted}>
            {ticks.map((t) =>
              t > 0 ? (
                <g key={t}>
                  <line x1={li - sx(t)} x2={li - sx(t)} y1={head} y2={h - 4} stroke={TOKENS.grid} />
                  <line x1={ri + sx(t)} x2={ri + sx(t)} y1={head} y2={h - 4} stroke={TOKENS.grid} />
                  {!narrow && (
                    <text x={li - sx(t)} y={head - 3} textAnchor="middle" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {fmtEurTick(t)}
                    </text>
                  )}
                  {!narrow && (
                    <text x={ri + sx(t)} y={head - 3} textAnchor="middle" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {fmtEurTick(t)}
                    </text>
                  )}
                </g>
              ) : null,
            )}
          </g>
          {rows.map((r, i) => {
            const y = head + i * rowHeight + (rowHeight - bar) / 2;
            const ms = marksFor(marks, r.code);
            const isHover = roving.hover?.i === i;
            const balance = r.imports !== null && r.exports !== null ? r.exports - r.imports : null;
            const label = `${r.name}: bought ${fmtSide(r.imports)}, sold ${fmtSide(r.exports)}${balance !== null ? `, balance ${fmtEur(balance, true)}` : ""}`;
            const dl = rounded(li - sx(r.imports), li, y, "left");
            const dr = rounded(ri, ri + sx(r.exports), y, "right");
            const outText = fmtSide(r.exports);
            const badges = ms.filter((m) => m.showNumber && spots[m.id] === r.code);
            return (
              <g key={r.code} ref={roving.register(i)} {...roving.itemProps(i)} role={onSelect ? "button" : "img"} aria-label={label} style={{ outline: "none", cursor: onSelect ? "pointer" : "default" }}>
                <rect x={0} y={head + i * rowHeight} width={width} height={rowHeight} fill={isHover ? TOKENS.ruleSoft : "transparent"} rx={4} />
                {dl && <path d={dl} fill={TOKENS.steel} />}
                {dr && <path d={dr} fill={TOKENS.sky} />}
                {ms.map((m) => (
                  <g key={m.id} opacity={m.opacity}>
                    {dl && <path d={dl} fill="none" stroke={m.color} strokeWidth={2.5} />}
                    {dr && <path d={dr} fill="none" stroke={m.color} strokeWidth={2.5} />}
                  </g>
                ))}
                <text x={cx} y={y + bar / 2 + 4} textAnchor="middle" fontSize={12.5} fill={ms.length ? ms[0].ink : TOKENS.ink} fontWeight={ms.length ? 700 : 500}>
                  <title>{r.name}</title>
                  {truncate(r.name, Math.floor(mid / 6.6))}
                </text>
                <text x={li - sx(r.imports) - 6} y={y + bar / 2 + 4} textAnchor="end" fontSize={11.5} fill={TOKENS.steelText} style={{ fontVariantNumeric: "tabular-nums" }}>
                  {fmtSide(r.imports)}
                </text>
                <text x={ri + sx(r.exports) + 6} y={y + bar / 2 + 4} textAnchor="start" fontSize={11.5} fill={TOKENS.skyText} style={{ fontVariantNumeric: "tabular-nums" }}>
                  {outText}
                </text>
                {badges.map((m, k) => (
                  <SvgBadge
                    key={m.id}
                    x={Math.min(width - 11, ri + sx(r.exports) + 6 + 6.3 * outText.length + 14 + k * 22)}
                    y={y + bar / 2}
                    n={m.n}
                    color={m.color}
                    opacity={m.opacity}
                  />
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
                  { value: fmtSide(hovered.imports), label: "bought", swatch: TOKENS.steel },
                  { value: fmtSide(hovered.exports), label: "sold", swatch: TOKENS.sky },
                  ...(hovered.imports !== null && hovered.exports !== null ? [{ value: fmtEur(hovered.exports - hovered.imports, true), label: "balance" }] : []),
                ]
              : []
          }
        />
      </div>
    </div>
  );
};
