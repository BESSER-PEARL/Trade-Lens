// Scatter: one bubble per code, two measures against each other (specialisation against
// growth, say), the size a third. Reference lines split the plane into four quadrants, named
// when the pair is known. The nearest bubble to the pointer answers (d3-delaunay), so small
// bubbles are as easy to reach as big ones; the keyboard walks them from left to right.
// Labels: the codes asked for, the agents' marks and the outliers, without collisions.

import React, { useMemo } from "react";
import { Delaunay } from "d3-delaunay";
import { ChartTip } from "./ChartTip";
import type { TipRow } from "./ChartTip";
import { niceTicks, truncate } from "./format";
import { FONT_UI, TOKENS, toneColor } from "./palette";
import type { PenMark } from "./palette";
import { SvgBadge } from "./parts";
import { marksFor } from "./panelData";
import { useMeasure } from "./useMeasure";
import { useRoving } from "./useRoving";

export interface ScatterPoint {
  code: string;
  name: string;
  x: number;
  y: number;
  size?: number;
}

export interface Axis {
  label: string;
  /** Tick labels. */
  format: (v: number) => string;
  /** The readout and the aria labels; the tick format when absent. */
  full?: (v: number) => string;
  log?: boolean;
  reference?: number;
}

export interface ScatterProps {
  points: ScatterPoint[];
  title: string;
  x: Axis;
  y: Axis;
  size?: { label: string; format: (v: number) => string };
  tone?: string;
  /** Codes to label whatever their place. */
  labels?: string[];
  marks?: PenMark[];
  /** Names of the four quadrants: top left, top right, bottom left, bottom right. */
  quadrants?: [string, string, string, string];
  /** Extra readout rows of a point. */
  extra?: (p: ScatterPoint) => TipRow[];
  /** Click or Enter on a bubble: the page's own highlight (agent "user"). */
  onSelect?: (p: ScatterPoint) => void;
}

function logTicks(lo: number, hi: number): number[] {
  const out: number[] = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    [1, 2, 5].forEach((m) => {
      const v = m * Math.pow(10, e);
      if (v >= lo && v <= hi) out.push(v);
    });
  }
  return out.length > 7 ? out.filter((v) => Math.abs(Math.log10(v) - Math.round(Math.log10(v))) < 1e-9) : out;
}

function domainOf(vals: number[], log: boolean, ref?: number): [number, number] {
  const xs = vals.concat(ref !== undefined && (!log || ref > 0) ? [ref] : []);
  let lo = Math.min.apply(null, xs);
  let hi = Math.max.apply(null, xs);
  if (log) {
    lo = lo / 1.25;
    hi = hi * 1.25;
    return [lo, hi];
  }
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.06;
  return [lo - pad, hi + pad];
}

export const Scatter: React.FC<ScatterProps> = ({ points, title, x, y, size, tone = "exports", labels = [], marks = [], quadrants, extra, onSelect }) => {
  const { ref, width } = useMeasure<HTMLDivElement>(640);
  const colour = toneColor(tone).fill;
  const logX = !!x.log && points.every((p) => p.x > 0);
  const logY = !!y.log && points.every((p) => p.y > 0);
  const h = Math.round(Math.max(280, Math.min(460, width * 0.62)));
  const m = { l: 58, r: 18, t: 34, b: 44 };
  const pw = Math.max(60, width - m.l - m.r);
  const ph = h - m.t - m.b;
  const [x0, x1] = domainOf(points.map((p) => p.x), logX, x.reference);
  const [y0, y1] = domainOf(points.map((p) => p.y), logY, y.reference);
  const sx = (v: number) => m.l + (logX ? (Math.log(v) - Math.log(x0)) / (Math.log(x1) - Math.log(x0)) : (v - x0) / (x1 - x0 || 1)) * pw;
  const sy = (v: number) => m.t + ph - (logY ? (Math.log(v) - Math.log(y0)) / (Math.log(y1) - Math.log(y0)) : (v - y0) / (y1 - y0 || 1)) * ph;
  const maxSize = Math.max(1, ...points.map((p) => Math.abs(p.size || 0)));
  const rMax = width < 520 ? 11 : 16;
  const rOf = (p: ScatterPoint) => (size && p.size !== undefined ? 3.5 + rMax * Math.sqrt(Math.abs(p.size) / maxSize) : 5);
  const xt = logX ? logTicks(x0, x1) : niceTicks(x0, x1, 5);
  const yt = logY ? logTicks(y0, y1) : niceTicks(y0, y1, 5);

  // Left to right, so the keyboard walks the plane in reading order.
  const order = useMemo(() => points.map((p, i) => i).sort((a, b) => points[a].x - points[b].x), [points]);
  const roving = useRoving<SVGCircleElement>(points.length, { onActivate: onSelect ? (i) => points[i] && onSelect(points[i]) : undefined });
  const pos = points.map((p) => [sx(p.x), sy(p.y)] as [number, number]);
  const delaunay = useMemo(() => Delaunay.from(pos), [pos.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(";")]); // eslint-disable-line react-hooks/exhaustive-deps

  // Which points get a label: asked, marked, then the outliers.
  const labelled = useMemo(() => {
    const want: number[] = [];
    const add = (i: number) => {
      if (i >= 0 && want.indexOf(i) < 0) want.push(i);
    };
    points.forEach((p, i) => {
      if (labels.indexOf(p.code) >= 0 || marksFor(marks, p.code).length) add(i);
    });
    const by = (f: (p: ScatterPoint) => number) => points.map((p, i) => i).sort((a, b) => f(points[b]) - f(points[a]));
    by((p) => Math.abs(p.size || 0)).slice(0, 3).forEach(add);
    by((p) => p.y).slice(0, 2).forEach(add);
    by((p) => -p.y).slice(0, 1).forEach(add);
    by((p) => p.x).slice(0, 2).forEach(add);
    return want.slice(0, 10);
  }, [points, labels, marks]);

  type Box = { x0: number; y0: number; x1: number; y1: number };
  const overlaps = (a: Box, b: Box) => !(a.x1 < b.x0 || a.x0 > b.x1 || a.y1 < b.y0 || a.y0 > b.y1);
  // A label box against a bubble: the nearest point of the box to the centre is inside the circle (plus a hair).
  const hitsBubble = (box: Box, skip = -1) =>
    pos.some(([cx, cy], j) => {
      if (j === skip) return false;
      const nx = Math.max(box.x0, Math.min(cx, box.x1));
      const ny = Math.max(box.y0, Math.min(cy, box.y1));
      return Math.hypot(cx - nx, cy - ny) < rOf(points[j]) + 1.5;
    });
  const rxRef = x.reference !== undefined && (!logX || x.reference > 0) ? sx(x.reference) : null;
  const ryRef = y.reference !== undefined && (!logY || y.reference > 0) ? sy(y.reference) : null;
  // The quadrant names go first, in their corners, stepping inwards past any bubble; then the bubbles' own labels
  // avoid them, the bubbles and each other.
  const placed: Box[] = [];
  const quadrantSpots =
    quadrants && rxRef !== null && ryRef !== null
      ? quadrants.map((q, k) => {
          const right = k % 2 === 1;
          const bottom = k >= 2;
          const w = q.length * 6.1;
          const tx = right ? m.l + pw - 6 : m.l + 6;
          let ty = bottom ? m.t + ph - 8 : m.t + 14;
          let box: Box = { x0: right ? tx - w : tx, y0: ty - 11, x1: right ? tx : tx + w, y1: ty + 3 };
          for (let step = 0; step < 6 && hitsBubble(box); step++) {
            ty += bottom ? -16 : 16;
            box = { ...box, y0: ty - 11, y1: ty + 3 };
          }
          placed.push(box);
          return { text: q, tx, ty, anchor: (right ? "end" : "start") as "start" | "end" };
        })
      : [];
  const labelBoxes = labelled
    .map((i) => {
      const p = points[i];
      const [px, py] = pos[i];
      const r = rOf(p);
      const text = truncate(p.name, 26);
      const w = text.length * 6.3;
      const tries: Array<[number, number, "start" | "end"]> = [
        [px + r + 4, py + 4, "start"],
        [px - r - 4, py + 4, "end"],
        [px + r + 2, py - r - 2, "start"],
        [px - r - 2, py + r + 12, "end"],
        [px + r + 2, py + r + 12, "start"],
        [px - r - 2, py - r - 2, "end"],
      ];
      for (const [tx, ty, anchor] of tries) {
        const bx0 = anchor === "start" ? tx : tx - w;
        const box = { x0: bx0, y0: ty - 11, x1: bx0 + w, y1: ty + 3 };
        if (box.x0 < 2 || box.x1 > width - 2 || box.y0 < 0 || box.y1 > m.t + ph) continue;
        if (placed.some((b) => overlaps(box, b)) || hitsBubble(box, i)) continue;
        placed.push(box);
        return { i, tx, ty, anchor, text };
      }
      return null;
    })
    .filter((l): l is { i: number; tx: number; ty: number; anchor: "start" | "end"; text: string } => !!l);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget;
    const r = svg.getBoundingClientRect();
    const k = width / (r.width || width);
    const mx = (e.clientX - r.left) * k;
    const my = (e.clientY - r.top) * k;
    const i = delaunay.find(mx, my);
    if (i < 0 || i >= points.length) return;
    const d = Math.hypot(pos[i][0] - mx, pos[i][1] - my);
    if (d > 44) roving.setHover(null);
    else roving.hoverAt(i, e);
  };

  const hov = roving.hover ? points[roving.hover.i] : undefined;
  const spots: Record<string, string> = {};
  points.forEach((p) => marksFor(marks, p.code).forEach((mk) => {
    if (spots[mk.id] === undefined) spots[mk.id] = p.code;
  }));
  const rx = x.reference !== undefined && (!logX || x.reference > 0) ? sx(x.reference) : null;
  const ry = y.reference !== undefined && (!logY || y.reference > 0) ? sy(y.reference) : null;

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
          onMouseMove={onMove}
          onClick={onSelect ? () => roving.hover && points[roving.hover.i] && onSelect(points[roving.hover.i]) : undefined}
          onMouseLeave={() => roving.setHover(null)}
        >
          <g aria-hidden="true" fontSize={11} fill={TOKENS.muted}>
            {yt.map((t) => (
              <g key={`y${t}`}>
                <line x1={m.l} x2={m.l + pw} y1={sy(t)} y2={sy(t)} stroke={TOKENS.grid} />
                <text x={m.l - 8} y={sy(t) + 4} textAnchor="end" style={{ fontVariantNumeric: "tabular-nums" }}>
                  {y.format(t)}
                </text>
              </g>
            ))}
            {xt.map((t) => (
              <g key={`x${t}`}>
                <line x1={sx(t)} x2={sx(t)} y1={m.t} y2={m.t + ph} stroke={TOKENS.grid} />
                <text x={sx(t)} y={m.t + ph + 16} textAnchor="middle" style={{ fontVariantNumeric: "tabular-nums" }}>
                  {x.format(t)}
                </text>
              </g>
            ))}
            <text x={m.l + pw} y={m.t + ph + 36} textAnchor="end" fontSize={12} fill={TOKENS.ink2} fontWeight={600}>
              {x.label}
            </text>
            <text x={m.l} y={14} textAnchor="start" fontSize={12} fill={TOKENS.ink2} fontWeight={600}>
              {y.label}
            </text>
            {rx !== null && <line x1={rx} x2={rx} y1={m.t} y2={m.t + ph} stroke={TOKENS.ink2} strokeDasharray="4 3" />}
            {ry !== null && <line x1={m.l} x2={m.l + pw} y1={ry} y2={ry} stroke={TOKENS.ink2} strokeDasharray="4 3" />}
            {quadrantSpots.length > 0 && (
              <g fontSize={11.5} fill={TOKENS.muted} fontStyle="normal" stroke={TOKENS.surface} strokeWidth={3} paintOrder="stroke">
                {quadrantSpots.map((q) => (
                  <text key={q.text} x={q.tx} y={q.ty} textAnchor={q.anchor}>
                    {q.text}
                  </text>
                ))}
              </g>
            )}
          </g>
          {order.map((i) => {
            const p = points[i];
            const [px, py] = pos[i];
            const ms = marksFor(marks, p.code);
            const isHover = roving.hover?.i === i;
            const label = `${p.name}: ${x.label} ${(x.full || x.format)(p.x)}, ${y.label} ${(y.full || y.format)(p.y)}${size && p.size !== undefined ? `, ${size.label} ${size.format(p.size)}` : ""}`;
            const r = rOf(p);
            return (
              <g key={p.code}>
                <circle
                  ref={roving.register(i)}
                  {...roving.itemProps(i)}
                  onMouseMove={undefined}
                  onClick={undefined}
                  cx={px}
                  cy={py}
                  r={r}
                  fill={colour}
                  fillOpacity={isHover ? 0.95 : 0.55}
                  stroke={isHover ? TOKENS.ink : TOKENS.surface}
                  strokeWidth={isHover ? 2 : 1}
                  role={onSelect ? "button" : "img"}
                  aria-label={label}
                  style={{ outline: "none", cursor: onSelect ? "pointer" : "default" }}
                />
                {ms.map((mk) => (
                  <circle key={mk.id} cx={px} cy={py} r={r + 3} fill="none" stroke={mk.color} strokeWidth={2.5} opacity={mk.opacity} pointerEvents="none" aria-hidden="true" />
                ))}
                {ms
                  .filter((mk) => mk.showNumber && spots[mk.id] === p.code)
                  .map((mk) => (
                    <SvgBadge key={`b${mk.id}`} x={Math.min(width - 11, px + r * 0.75 + 8)} y={Math.max(11, py - r * 0.75 - 8)} n={mk.n} color={mk.color} r={9} opacity={mk.opacity} />
                  ))}
              </g>
            );
          })}
          <g aria-hidden="true" pointerEvents="none" fontSize={11.5}>
            {labelBoxes.map((l) => {
              const ms = marksFor(marks, points[l.i].code);
              return (
                <text
                  key={`l${l.i}`}
                  x={l.tx}
                  y={l.ty}
                  textAnchor={l.anchor}
                  fill={ms.length ? ms[0].ink : TOKENS.ink2}
                  fontWeight={ms.length ? 700 : 500}
                  stroke={TOKENS.surface}
                  strokeWidth={3}
                  paintOrder="stroke"
                >
                  {l.text}
                </text>
              );
            })}
          </g>
        </svg>
        <ChartTip
          x={roving.hover?.x ?? 0}
          y={roving.hover?.y ?? 0}
          width={width}
          visible={!!hov}
          title={hov?.name}
          rows={
            hov
              ? [
                  { value: (x.full || x.format)(hov.x), label: x.label },
                  { value: (y.full || y.format)(hov.y), label: y.label },
                  ...(size && hov.size !== undefined ? [{ value: size.format(hov.size), label: size.label, swatch: colour }] : []),
                  ...(extra ? extra(hov) : []),
                ]
              : []
          }
        />
      </div>
    </div>
  );
};
