// SPDX-License-Identifier: MIT
// The line chart of the Trends page, in the house style of the Overview's line panel: a quiet
// grid, euro ticks with their unit, a direct label at the end of each line instead of a legend,
// and a crosshair with a readout on hover, touch or the arrow keys.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { money, moneyTick, periodLabel } from "../format";
import { READOUT_STYLE, TOKENS } from "../styles";

export interface ChartSeries {
  key: string;
  label: string;
  /** Stroke colour, and the darker variant for the label text. */
  color: string;
  text: string;
  width: number;
  dash?: string;
  values: Array<number | null>;
  /** Draw the name and last value at the end of the line (default true). */
  endLabel?: boolean;
}

export interface ChartBand {
  lo: Array<number | null>;
  hi: Array<number | null>;
  color: string;
  label: string;
}

export interface ChartRule {
  index: number;
  n: number;
  /** Read out with the crosshair when it stands on the rule. */
  detail: string;
}

interface Props {
  periods: string[];
  series: ChartSeries[];
  band?: ChartBand;
  rules?: ChartRule[];
  /** Index of the last observed period: everything after it is shaded as the forecast zone. */
  zoneFrom?: number;
  unit?: string;
  height?: number;
  narrowHeight?: number;
  ariaLabel: string;
}

const f = (v: number) => Math.round(v * 10) / 10;

/** Width of the element, kept in step with resizes. */
export function useWidth<T extends HTMLElement = HTMLDivElement>(fallback = 720): { ref: (el: T | null) => void; width: number } {
  const [width, setWidth] = useState(fallback);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: T | null) => {
    if (observer.current) observer.current.disconnect();
    observer.current = null;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width || fallback);
    if (typeof ResizeObserver !== "undefined") {
      observer.current = new ResizeObserver((entries) => {
        const w = entries[0]?.contentRect.width;
        if (w) setWidth(w);
      });
      observer.current.observe(el);
    }
  }, [fallback]);
  useEffect(() => () => observer.current?.disconnect(), []);
  return { ref, width };
}

/** Rough width of a label in Archivo, for margins and collisions. */
export function textWidth(s: string, size = 13, bold = false): number {
  return s.length * size * (bold ? 0.56 : 0.52);
}

/** End labels of one or several lines: block centres moved apart so no two blocks overlap, inside [min, max]. */
function spreadBlocks(anchors: number[], heights: number[], min: number, max: number): number[] {
  const order = anchors.map((y, i) => ({ y, i, h: heights[i] })).sort((a, b) => a.y - b.y);
  const ys = order.map((o) => Math.max(min + o.h / 2, Math.min(max - o.h / 2, o.y)));
  for (let k = 1; k < ys.length; k++) {
    const need = (order[k - 1].h + order[k].h) / 2;
    if (ys[k] - ys[k - 1] < need) ys[k] = ys[k - 1] + need;
  }
  const last = ys.length - 1;
  const over = last >= 0 ? ys[last] + order[last].h / 2 - max : 0;
  if (over > 0) {
    ys[last] -= over;
    for (let k = last - 1; k >= 0; k--) ys[k] = Math.min(ys[k], ys[k + 1] - (order[k].h + order[k + 1].h) / 2);
  }
  const out: number[] = new Array(anchors.length);
  order.forEach((o, k) => { out[o.i] = ys[k]; });
  return out;
}

/** Words onto lines no wider than `maxW` px (a single long word keeps a line of its own). */
function wrapWords(label: string, maxW: number, size: number): string[] {
  const lines: string[] = [];
  let cur = "";
  label.split(/\s+/).filter(Boolean).forEach((w) => {
    const next = cur ? `${cur} ${w}` : w;
    if (cur && textWidth(next, size, true) > maxW) {
      lines.push(cur);
      cur = w;
    } else cur = next;
  });
  if (cur || !lines.length) lines.push(cur);
  return lines;
}

function niceStep(span: number, count: number): number {
  if (!(span > 0)) return 1;
  const raw = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return nice * mag;
}

const lastIndex = (values: Array<number | null>): number => {
  for (let i = values.length - 1; i >= 0; i--) if (typeof values[i] === "number") return i;
  return -1;
};

export const LineChart: React.FC<Props> = ({
  periods, series, band, rules = [], zoneFrom, unit = "€", height = 360, narrowHeight = 280, ariaLabel,
}) => {
  const { ref, width } = useWidth(720);
  const [hover, setHover] = useState<number | null>(null);
  const [tipY, setTipY] = useState<number | null>(null);
  const n = periods.length;
  if (n === 0) return null;

  const W = Math.max(280, width);
  const narrow = W < 560;
  const H = narrow ? narrowHeight : height;

  // y scale over every value on the chart
  let min = Infinity;
  let max = -Infinity;
  const take = (v: number | null | undefined) => {
    if (typeof v === "number" && Number.isFinite(v)) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
  };
  series.forEach((s) => s.values.forEach(take));
  if (band) { band.lo.forEach(take); band.hi.forEach(take); }
  if (!Number.isFinite(min)) { min = 0; max = 1; }
  if (min === max) { min -= Math.abs(min) * 0.1 || 1; max += Math.abs(max) * 0.1 || 1; }
  const step = niceStep(max - min, narrow ? 4 : 5);
  const y0 = Math.floor(min / step) * step;
  const y1 = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let t = y0; t <= y1 + step / 2; t += step) ticks.push(Math.round(t / step) * step);
  const tickText = (t: number) => moneyTick(t, step, unit);

  // margins: the ticks on the left, the end labels on the right
  const labelled = series
    .map((s) => ({ s, i: lastIndex(s.values) }))
    .filter((e) => e.s.endLabel !== false && e.i >= 0);
  const left = Math.max(...ticks.map((t) => textWidth(tickText(t), 12))) + 12;
  // End labels wrap onto as many lines as they need within the right margin ("If smartphones stay at their" /
  // "recent level €1.5 bn"), so a long one is never cut, on a phone either.
  const labelSize = narrow ? 12 : 13;
  const lineH = narrow ? 14 : 15;
  const maxLabelW = narrow ? 104 : 210;
  const ends = labelled.map((e) => {
    const val = money(e.s.values[e.i] as number, unit);
    const lines = wrapWords(e.s.label, maxLabelW, labelSize);
    const last = lines[lines.length - 1];
    if (textWidth(`${last} ${val}`, labelSize, true) <= maxLabelW) lines[lines.length - 1] = `${last}\u0000${val}`;
    else lines.push(`\u0000${val}`);
    return { ...e, val, lines };
  });
  const endWidth = ends.reduce((w, e) => Math.max(w, ...e.lines.map((l) => textWidth(l.replace("\u0000", " "), labelSize, true))), 0);
  const right = labelled.length ? Math.min(maxLabelW, endWidth) + 14 : 12;
  const top = 14;
  const bottom = 26;
  const plotW = Math.max(40, W - left - right);
  const plotH = H - top - bottom;
  const plotBottom = top + plotH;
  const sx = (i: number) => left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const sy = (v: number) => top + plotH - ((v - y0) / (y1 - y0 || 1)) * plotH;

  // x ticks: the first period of a year, thinned to what fits
  const yearOf = (p: string) => (/^\d{4}/.test(p) ? parseInt(p.slice(0, 4), 10) : NaN);
  const firsts: number[] = [];
  periods.forEach((p, i) => {
    const y = yearOf(p);
    if (!Number.isNaN(y) && (i === 0 ? /^\d{4}$/.test(p) || /-0?1$/.test(p) || /Q1$/.test(p) : yearOf(periods[i - 1]) !== y)) firsts.push(i);
  });
  const fit = Math.max(2, Math.floor(plotW / (narrow ? 44 : 58)));
  const yearStep = [1, 2, 5, 10, 20, 50].find((k) => firsts.filter((i) => yearOf(periods[i]) % k === 0).length <= fit) ?? 50;
  const xTicks = firsts.filter((i) => yearOf(periods[i]) % yearStep === 0);

  const pathOf = (values: Array<number | null>) => {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (typeof v !== "number") { pen = false; return; }
      d += `${pen ? "L" : "M"}${f(sx(i))} ${f(sy(v))}`;
      pen = true;
    });
    return d;
  };

  let bandPath = "";
  if (band) {
    const idx = periods.map((_, i) => i).filter((i) => typeof band.lo[i] === "number" && typeof band.hi[i] === "number");
    if (idx.length > 1) {
      bandPath =
        "M" + idx.map((i) => `${f(sx(i))} ${f(sy(band.hi[i] as number))}`).join(" L") +
        " L" + idx.slice().reverse().map((i) => `${f(sx(i))} ${f(sy(band.lo[i] as number))}`).join(" L") + " Z";
    }
  }

  // end labels, spread apart
  const labelX = sx(n - 1) + 10;
  const labelYs = spreadBlocks(
    ends.map((e) => sy(e.s.values[e.i] as number)),
    ends.map((e) => e.lines.length * lineH + 4),
    top + 4,
    plotBottom,
  );

  // change-point markers: numbered discs along the top of the plot, stacked when they crowd
  const placed: Array<{ r: ChartRule; x: number; row: number }> = [];
  rules.slice().sort((a, b) => a.index - b.index).forEach((r) => {
    const x = sx(r.index);
    let row = 0;
    while (placed.some((p) => p.row === row && Math.abs(p.x - x) < 22)) row++;
    placed.push({ r, x, row });
  });

  const hasZone = typeof zoneFrom === "number" && zoneFrom >= 0 && zoneFrom < n - 1;

  // crosshair
  const hx = hover !== null ? Math.max(0, Math.min(n - 1, hover)) : null;
  const nearest = (px: number) => (n <= 1 ? 0 : Math.round(((px - left) / plotW) * (n - 1)));
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < left - 12 || px > left + plotW + 12) { setHover(null); return; }
    setHover(Math.max(0, Math.min(n - 1, nearest(px))));
    setTipY(e.clientY - r.top);
  };
  const onKey = (e: React.KeyboardEvent<SVGSVGElement>) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End", "Escape", "PageUp", "PageDown"];
    if (keys.indexOf(e.key) < 0) return;
    e.preventDefault();
    if (e.key === "Escape") { setHover(null); return; }
    const jump = e.key === "PageUp" ? -12 : e.key === "PageDown" ? 12 : e.key === "ArrowLeft" ? -1 : 1;
    const key = e.key;
    setHover((prev) => {
      const next = key === "Home" ? 0 : key === "End" || prev === null ? n - 1 : prev + jump;
      return Math.max(0, Math.min(n - 1, next));
    });
    setTipY(null);
  };

  const tipRows: Array<{ swatch: string; dash?: boolean; value: string; label: string }> = [];
  let tipRule: ChartRule | undefined;
  if (hx !== null) {
    series.forEach((s) => {
      const v = s.values[hx];
      if (typeof v === "number") tipRows.push({ swatch: s.color, dash: !!s.dash, value: money(v, unit), label: s.label });
    });
    if (band && typeof band.lo[hx] === "number" && typeof band.hi[hx] === "number") {
      tipRows.push({ swatch: band.color, value: `${money(band.lo[hx], unit)} to ${money(band.hi[hx], unit)}`, label: band.label });
    }
    tipRule = rules.find((r) => r.index === hx);
  }
  const tipX = hx !== null ? sx(hx) : 0;
  const flip = tipX > W - 230;

  return (
    <div ref={ref} className="tr-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        role="img"
        aria-label={`${ariaLabel} Use the left and right arrow keys to read the values one period at a time.`}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerDown={onMove}
        onPointerLeave={() => setHover(null)}
        onKeyDown={onKey}
        onBlur={() => setHover(null)}
      >
        <g aria-hidden="true">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={left + plotW} y1={f(sy(t))} y2={f(sy(t))} stroke={t === 0 ? TOKENS.field : TOKENS.grid} strokeWidth={1} />
              <text x={left - 8} y={f(sy(t) + 4)} textAnchor="end" style={{ fontSize: 12, fill: TOKENS.muted, fontVariantNumeric: "tabular-nums" }}>
                {tickText(t)}
              </text>
            </g>
          ))}
          <line x1={left} x2={left + plotW} y1={plotBottom} y2={plotBottom} stroke={TOKENS.field} strokeWidth={1} />
          {xTicks.map((i) => (
            <g key={i}>
              <line x1={f(sx(i))} x2={f(sx(i))} y1={plotBottom} y2={plotBottom + 4} stroke={TOKENS.field} />
              <text x={f(sx(i))} y={H - 6} textAnchor="middle" style={{ fontSize: 12, fill: TOKENS.muted, fontVariantNumeric: "tabular-nums" }}>
                {yearOf(periods[i])}
              </text>
            </g>
          ))}

          {hasZone && (
            <g pointerEvents="none">
              <rect x={f(sx(zoneFrom as number))} y={top} width={f(left + plotW - sx(zoneFrom as number))} height={f(plotH)} fill={TOKENS.ink} fillOpacity={0.035} />
              <line x1={f(sx(zoneFrom as number))} x2={f(sx(zoneFrom as number))} y1={top} y2={plotBottom} stroke={TOKENS.ink} strokeOpacity={0.3} strokeDasharray="3 3" />
              <text x={f(sx(zoneFrom as number) + 6)} y={f(plotBottom - 6)} style={{ fontSize: 11.5, fill: TOKENS.muted }}>
                Forecast
              </text>
            </g>
          )}

          {bandPath && band && <path d={bandPath} fill={band.color} fillOpacity={0.16} stroke="none" />}

          {placed.map(({ r, x }) => (
            <line key={`rule-${r.n}`} x1={f(x)} x2={f(x)} y1={top} y2={plotBottom} stroke={TOKENS.ink} strokeOpacity={0.55} strokeWidth={1} strokeDasharray="4 3" />
          ))}

          {series.map((s) => (
            <path key={s.key} d={pathOf(s.values)} fill="none" stroke={s.color} strokeWidth={s.width} strokeDasharray={s.dash} strokeLinejoin="round" strokeLinecap="round" />
          ))}

          {placed.map(({ r, x, row }) => (
            <g key={`disc-${r.n}`}>
              <circle cx={f(x)} cy={f(top + 10 + row * 22)} r={9} fill={TOKENS.ink} stroke="#FFFFFF" strokeWidth={2} />
              <text x={f(x)} y={f(top + 14 + row * 22)} textAnchor="middle" style={{ fontSize: 11, fontWeight: 700, fill: "#FFFFFF" }}>
                {r.n}
              </text>
            </g>
          ))}

          {ends.map((e, k) => {
            const ax = sx(e.i);
            const ay = sy(e.s.values[e.i] as number);
            const y = labelYs[k];
            const y0 = y - ((e.lines.length - 1) * lineH) / 2 + labelSize * 0.35;
            return (
              <g key={`end-${e.s.key}`}>
                {Math.abs(y - ay) > 3 && <path d={`M${f(ax + 4)} ${f(ay)} L${f(labelX - 4)} ${f(y)}`} stroke={e.s.color} strokeWidth={1} fill="none" />}
                <text x={f(labelX)} y={f(y0)} style={{ fontSize: labelSize, fontWeight: 700, fill: e.s.text }}>
                  {e.lines.map((line, j) => {
                    const [words, value] = line.split("\u0000");
                    return (
                      <tspan key={j} x={f(labelX)} dy={j === 0 ? 0 : lineH}>
                        {words}
                        {value !== undefined && (
                          <tspan style={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{`${words ? " " : ""}${value}`}</tspan>
                        )}
                      </tspan>
                    );
                  })}
                </text>
              </g>
            );
          })}

          {hx !== null && (
            <g pointerEvents="none">
              <line x1={f(sx(hx))} x2={f(sx(hx))} y1={top} y2={plotBottom} stroke={TOKENS.ink} strokeOpacity={0.4} strokeWidth={1} />
              {series.map((s) => {
                const v = s.values[hx];
                return typeof v === "number" ? <circle key={`hv-${s.key}`} cx={f(sx(hx))} cy={f(sy(v))} r={4} fill={s.color} stroke="#FFFFFF" strokeWidth={1.5} /> : null;
              })}
            </g>
          )}
        </g>
      </svg>

      <div role="status" aria-live="polite" style={hx === null ? { position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" } : {
        ...READOUT_STYLE,
        top: Math.max(0, Math.min(H - 120, (tipY ?? top + 20) - 12)),
        // On a phone the readout spans the chart; on a wide screen it follows the crosshair.
        left: narrow ? 0 : flip ? undefined : Math.min(tipX + 14, W - 200),
        right: narrow ? undefined : flip ? Math.max(0, W - tipX + 14) : undefined,
        maxWidth: narrow ? W : 320,
        minWidth: 170,
      }}>
        {hx !== null && (
          <>
            <div style={{ fontWeight: 700, marginBottom: 2 }}>{periodLabel(periods[hx], true)}</div>
            {tipRows.map((r, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}>
                <span aria-hidden="true" style={{
                  display: "inline-block", width: 14, height: 0, flex: "none",
                  borderTop: `${r.label === band?.label ? 8 : 3}px ${r.dash ? "dashed" : "solid"} ${r.swatch}`,
                  opacity: r.label === band?.label ? 0.35 : 1,
                }} />
                <span style={{ fontWeight: 700 }}>{r.value}</span>
                <span style={{ color: TOKENS.muted }}>{r.label}</span>
              </div>
            ))}
            {tipRule && (
              <div style={{ marginTop: 4, paddingTop: 4, borderTop: `1px solid ${TOKENS.rule}` }}>
                <span style={{ fontWeight: 700 }}>Change point {tipRule.n}</span>{" "}
                <span style={{ color: TOKENS.muted }}>{tipRule.detail}</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
