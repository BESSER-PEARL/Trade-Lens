// Flow map: arcs between Luxembourg and its largest partners, width = value. Imports come
// in (steel), exports go out (sky); with both, the two arcs of a partner bend to opposite
// sides so they never sit on top of each other. A partner beyond the frame (the United States,
// China on the Europe map) is not drawn at a false spot inside it: its arc ends at an arrow on
// the edge it lies beyond (west, east...), with its name; arrows on one edge are spread apart so
// no two names overlap. Agents' thread marks draw
// one route through Luxembourg (bought from in_code, sold to out_code) in their pen, with
// the storyline number; highlight marks outline countries (WorldMapBase).
//
// The neighbours' arcs are short: the butterfly and the table are the honest ranking.

import React, { useMemo } from "react";
import { fmtEur, fmtPct } from "../format";
import { MAP_EMPTY, SKY_RAMP, STEEL_RAMP, TOKENS } from "../palette";
import type { PenMark } from "../palette";
import { SvgBadge } from "../parts";
import { prefersReducedMotion } from "../useMeasure";
import { centroidOf, toIso } from "./geo";
import type { Country, Extent } from "./geo";
import { WorldMapBase } from "./WorldMapBase";
import type { MapContext } from "./WorldMapBase";

export interface FlowDatum {
  code: string;
  name: string;
  value: number;
  share?: number;
}

export interface ThreadMark {
  id: string;
  in_code?: string;
  out_code?: string;
  label: string;
  color: string;
  n: number;
  showNumber: boolean;
  opacity: number;
}

export interface FlowMapProps {
  imports: FlowDatum[];
  exports: FlowDatum[];
  show?: "imports" | "exports" | "both";
  title: string;
  origin?: string;
  extent?: Extent;
  maxHeight?: number;
  /** Arcs drawn per direction, by value. */
  maxArcs?: number;
  threads?: ThreadMark[];
  highlights?: PenMark[];
  /** Click or Enter on a partner (its Eurostat code). */
  onSelect?: (code: string, name: string) => void;
}

type Pt = [number, number];

type Edge = "left" | "right" | "top" | "bottom";

/** Cuts the segment a->b at the frame (with a margin); returns the point, whether it was cut, and the edge. */
export function clipToFrame(a: Pt, b: Pt, w: number, h: number, m = 14): { p: Pt; cut: boolean; edge?: Edge } {
  if (b[0] >= m && b[0] <= w - m && b[1] >= m && b[1] <= h - m) return { p: b, cut: false };
  let t = Infinity;
  let edge: Edge = "right";
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const hit = (tt: number, e: Edge) => {
    if (tt < t) {
      t = tt;
      edge = e;
    }
  };
  if (dx > 0) hit((w - m - a[0]) / dx, "right");
  if (dx < 0) hit((m - a[0]) / dx, "left");
  if (dy > 0) hit((h - m - a[1]) / dy, "bottom");
  if (dy < 0) hit((m - a[1]) / dy, "top");
  t = Math.max(0, Math.min(1, t));
  return { p: [a[0] + dx * t, a[1] + dy * t], cut: true, edge };
}

/** A partner beyond the frame: an arrow on the edge it lies beyond, with its name. */
export interface EdgeMarker {
  code: string;
  name: string;
  edge: Edge;
  /** Where the arcs end, just inside the edge (the arrow's base). */
  p: Pt;
}

const EDGE_INSET = 12;

/** Rough width of an 11 px semibold label. */
const nameWidth = (s: string) => s.length * 6.2 + 8;

/** Spreads positions apart (input order kept) so neighbours i, j are at least gap(i, j) apart, inside [lo, hi]. */
function spread(xs: number[], gapOf: (a: number, b: number) => number, lo: number, hi: number): number[] {
  const idx = xs.map((x, i) => ({ x, i })).sort((a, b) => a.x - b.x);
  const pos = idx.map((d) => d.x);
  for (let k = 0; k < 6; k++) {
    for (let j = 1; j < pos.length; j++) {
      const g = gapOf(idx[j - 1].i, idx[j].i);
      if (pos[j] - pos[j - 1] < g) pos[j] = pos[j - 1] + g;
    }
    if (pos.length && pos[pos.length - 1] > hi) pos[pos.length - 1] = hi;
    for (let j = pos.length - 2; j >= 0; j--) {
      const g = gapOf(idx[j].i, idx[j + 1].i);
      if (pos[j + 1] - pos[j] < g) pos[j] = pos[j + 1] - g;
    }
    if (pos.length && pos[0] < lo) pos[0] = lo;
  }
  const out: number[] = new Array(xs.length);
  idx.forEach((d, j) => (out[d.i] = pos[j]));
  return out;
}

/**
 * Edge markers for the partners beyond the frame: each on the edge where its straight line from
 * the origin leaves the frame (the United States west, China and Japan east), spread along it.
 */
export function edgeMarkers(origin: Pt, partners: Array<{ code: string; name: string; p: Pt }>, w: number, h: number): Map<string, EdgeMarker> {
  const out = new Map<string, EdgeMarker>();
  const byEdge: Record<Edge, EdgeMarker[]> = { left: [], right: [], top: [], bottom: [] };
  partners.forEach(({ code, name, p }) => {
    if (out.has(code)) return;
    const c = clipToFrame(origin, p, w, h, EDGE_INSET);
    if (!c.cut || !c.edge) return;
    const mk: EdgeMarker = { code, name, edge: c.edge, p: c.p };
    out.set(code, mk);
    byEdge[c.edge].push(mk);
  });
  (["left", "right"] as Edge[]).forEach((e) => {
    const ms = byEdge[e];
    // The name sits above its arrow: two lines of room each.
    const ys = spread(ms.map((m) => m.p[1]), () => 28, EDGE_INSET + 18, h - EDGE_INSET - 6);
    ms.forEach((m, i) => (m.p = [e === "left" ? EDGE_INSET : w - EDGE_INSET, ys[i]]));
  });
  (["top", "bottom"] as Edge[]).forEach((e) => {
    const ms = byEdge[e];
    const xs = spread(ms.map((m) => m.p[0]), (a, b) => (nameWidth(ms[a].name) + nameWidth(ms[b].name)) / 2 + 14, EDGE_INSET + 30, w - EDGE_INSET - 30);
    ms.forEach((m, i) => (m.p = [xs[i], e === "top" ? EDGE_INSET : h - EDGE_INSET]));
  });
  return out;
}

/** A quadratic curve from a to b bending to one side (side = +1 or -1), and its midpoint. */
function curve(a: Pt, b: Pt, side: number, k = 0.22): { d: string; mid: Pt } {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  const c: Pt = [(a[0] + b[0]) / 2 + nx * len * k * side, (a[1] + b[1]) / 2 + ny * len * k * side];
  const mid: Pt = [0.25 * a[0] + 0.5 * c[0] + 0.25 * b[0], 0.25 * a[1] + 0.5 * c[1] + 0.25 * b[1]];
  return { d: `M${a[0].toFixed(1)},${a[1].toFixed(1)} Q${c[0].toFixed(1)},${c[1].toFixed(1)} ${b[0].toFixed(1)},${b[1].toFixed(1)}`, mid };
}

/** The arcs a flow map draws: the largest `maxArcs` partners of each side shown that have a place on the map. */
export function drawnFlows(flows: FlowDatum[], on: boolean, maxArcs: number): FlowDatum[] {
  return on ? flows.slice().sort((a, b) => b.value - a.value).slice(0, maxArcs).filter((f) => centroidOf(f.code)) : [];
}

/** The widest arc's value: the flows shown only, so an exports map never scales by the imports. */
export function widestFlow(imports: FlowDatum[], exports: FlowDatum[], show: "imports" | "exports" | "both", maxArcs: number): number {
  return Math.max(0, ...drawnFlows(imports, show !== "exports", maxArcs).map((f) => f.value), ...drawnFlows(exports, show !== "imports", maxArcs).map((f) => f.value));
}

/** An arrow on the frame's edge pointing out, towards a partner beyond the map, with its name. */
const EdgeArrow: React.FC<{ m: EdgeMarker; w: number; h: number }> = ({ m, w, h }) => {
  const [x, y] = m.p;
  const s = 6;
  // The arrow's tip sits on the edge; its base is where the arcs end.
  const tri =
    m.edge === "right"
      ? `${w - 2},${y} ${x},${y - s} ${x},${y + s}`
      : m.edge === "left"
        ? `2,${y} ${x},${y - s} ${x},${y + s}`
        : m.edge === "top"
          ? `${x},2 ${x - s},${y} ${x + s},${y}`
          : `${x},${h - 2} ${x - s},${y} ${x + s},${y}`;
  const side = m.edge === "left" || m.edge === "right";
  const anchor = m.edge === "right" ? "end" : m.edge === "left" ? "start" : x > w / 2 ? "end" : "start";
  const tx = side ? (m.edge === "right" ? w - 3 : 3) : anchor === "end" ? x - s - 4 : x + s + 4;
  const ty = side ? y - 10 : m.edge === "top" ? y + 4 : y + 2;
  return (
    <g>
      <polygon points={tri} fill={TOKENS.ink2} stroke={TOKENS.surface} strokeWidth={1} strokeLinejoin="round" />
      <text x={tx} y={ty} textAnchor={anchor} fontSize={11} fontWeight={600} fill={TOKENS.ink2} stroke={TOKENS.surface} strokeWidth={3} paintOrder="stroke">
        {m.name}
      </text>
    </g>
  );
};

export const FlowMap: React.FC<FlowMapProps> = ({
  imports,
  exports,
  show = "both",
  title,
  origin = "LU",
  extent = "europe",
  maxHeight,
  maxArcs = 10,
  threads = [],
  highlights = [],
  onSelect,
}) => {
  const animate = !prefersReducedMotion();
  const showIn = show !== "exports";
  const showOut = show !== "imports";
  const inDrawn = useMemo(() => drawnFlows(imports, showIn, maxArcs), [imports, showIn, maxArcs]);
  const outDrawn = useMemo(() => drawnFlows(exports, showOut, maxArcs), [exports, showOut, maxArcs]);
  const vmax = Math.max(1, ...inDrawn.map((f) => f.value), ...outDrawn.map((f) => f.value));
  const widthOf = (v: number) => 1.25 + 9 * Math.sqrt(Math.max(0, v) / vmax);

  const inBy = useMemo(() => new Map(imports.map((f) => [toIso(f.code), f] as [string, FlowDatum])), [imports]);
  const outBy = useMemo(() => new Map(exports.map((f) => [toIso(f.code), f] as [string, FlowDatum])), [exports]);
  const has = (c: Country) => !!c.code && ((showIn && inBy.has(c.code)) || (showOut && outBy.has(c.code)));
  const fill = (c: Country) => (has(c) ? (show === "imports" ? STEEL_RAMP[1] : show === "exports" ? SKY_RAMP[1] : TOKENS.rule) : MAP_EMPTY);
  const nameOf = (c: Country) => outBy.get(c.code || "")?.name || inBy.get(c.code || "")?.name || c.name;
  const label = (c: Country) => {
    const i = inBy.get(c.code || "");
    const o = outBy.get(c.code || "");
    const parts: string[] = [nameOf(c)];
    if (showIn && i) parts.push(`bought ${fmtEur(i.value)}`);
    if (showOut && o) parts.push(`sold ${fmtEur(o.value)}`);
    return parts.join(", ");
  };
  const tip = (c: Country) => {
    const i = inBy.get(c.code || "");
    const o = outBy.get(c.code || "");
    const rows = [];
    if (showIn && i) rows.push({ value: fmtEur(i.value), label: `bought${i.share !== undefined ? `, ${fmtPct(i.share)} of imports` : ""}`, swatch: TOKENS.steel });
    if (showOut && o) rows.push({ value: fmtEur(o.value), label: `sold${o.share !== undefined ? `, ${fmtPct(o.share)} of exports` : ""}`, swatch: TOKENS.sky });
    return rows;
  };
  const order = useMemo(() => {
    const total: Record<string, number> = {};
    if (showIn) imports.forEach((f) => (total[f.code] = (total[f.code] || 0) + f.value));
    if (showOut) exports.forEach((f) => (total[f.code] = (total[f.code] || 0) + f.value));
    return Object.keys(total).sort((a, b) => total[b] - total[a]);
  }, [imports, exports, showIn, showOut]);

  const overlay = (ctx: MapContext) => {
    const o = ctx.project(origin);
    if (!o) return null;
    // Partners beyond the frame first: their arrows on the edge, spread so no two names overlap.
    const beyond: Array<{ code: string; name: string; p: Pt }> = [];
    const seen = (code: string, name: string) => {
      const p = ctx.project(code);
      if (p) beyond.push({ code: toIso(code), name, p });
    };
    inDrawn.forEach((f) => seen(f.code, f.name));
    outDrawn.forEach((f) => seen(f.code, f.name));
    threads.forEach((t) => {
      if (t.in_code) seen(t.in_code, inBy.get(toIso(t.in_code))?.name || outBy.get(toIso(t.in_code))?.name || t.in_code);
      if (t.out_code) seen(t.out_code, outBy.get(toIso(t.out_code))?.name || inBy.get(toIso(t.out_code))?.name || t.out_code);
    });
    const markers = edgeMarkers(o, beyond, ctx.width, ctx.height);
    const endOf = (code: string): Pt | null => markers.get(toIso(code))?.p || ctx.project(code);

    const arcs: Array<{ key: string; code: string; d: string; w: number; colour: string; end: Pt; cut: boolean }> = [];
    const push = (f: FlowDatum, dir: "in" | "out") => {
      const end = endOf(f.code);
      if (!end) return;
      // Exports bend one way, imports the other, seen from Luxembourg.
      const { d } = dir === "out" ? curve(o, end, 1) : curve(end, o, 1);
      arcs.push({ key: `${dir}${f.code}`, code: toIso(f.code), d, w: widthOf(f.value), colour: dir === "out" ? TOKENS.sky : TOKENS.steel, end, cut: markers.has(toIso(f.code)) });
    };
    // Widest first, so thin arcs stay visible on top.
    inDrawn.forEach((f) => push(f, "in"));
    outDrawn.forEach((f) => push(f, "out"));
    arcs.sort((a, b) => b.w - a.w);

    const threadDrawn = threads.map((t) => {
      const legs: Array<{ d: string; mid: Pt; len: number }> = [];
      const a = t.in_code ? endOf(t.in_code) : null;
      const b = t.out_code ? endOf(t.out_code) : null;
      if (a) {
        const c = curve(a, o, -1, 0.3);
        legs.push({ ...c, len: Math.hypot(a[0] - o[0], a[1] - o[1]) });
      }
      if (b) {
        const c = curve(o, b, -1, 0.3);
        legs.push({ ...c, len: Math.hypot(b[0] - o[0], b[1] - o[1]) });
      }
      const badge = legs.length ? legs.slice().sort((x, y) => y.len - x.len)[0].mid : null;
      return { t, legs, badge };
    });
    const edgeList = Array.from(markers.values());

    return (
      <g aria-hidden="true" pointerEvents="none">
        <style>{`@keyframes fm-draw { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }`}</style>
        {arcs.map((a) => (
          <path key={a.key} d={a.d} fill="none" stroke={a.colour} strokeOpacity={0.62} strokeWidth={a.w} strokeLinecap="round" />
        ))}
        {arcs
          .filter((a) => !a.cut)
          .map((a) => (
            <circle key={`e${a.key}`} cx={a.end[0]} cy={a.end[1]} r={2.5} fill={a.colour} stroke={TOKENS.surface} strokeWidth={1} />
          ))}
        {edgeList.map((m) => (
          <EdgeArrow key={`n${m.code}`} m={m} w={ctx.width} h={ctx.height} />
        ))}
        {threadDrawn.map(({ t, legs, badge }) => (
          <g key={t.id} opacity={t.opacity}>
            {legs.map((l, i) => (
              <g key={i}>
                <path d={l.d} fill="none" stroke={TOKENS.surface} strokeWidth={7} strokeLinecap="round" strokeOpacity={0.9} />
                <path
                  d={l.d}
                  fill="none"
                  stroke={t.color}
                  strokeWidth={3.5}
                  strokeLinecap="round"
                  pathLength={1}
                  style={animate ? { strokeDasharray: 1, strokeDashoffset: 1, animation: `fm-draw 1.2s cubic-bezier(.6,0,.2,1) ${0.2 + i * 0.9}s both` } : undefined}
                />
              </g>
            ))}
            {t.showNumber && badge && <SvgBadge x={badge[0]} y={badge[1] - 4} n={t.n} color={t.color} r={11} />}
          </g>
        ))}
      </g>
    );
  };

  return (
    <WorldMapBase
      title={title}
      extent={extent}
      maxHeight={maxHeight}
      fill={fill}
      active={(c) => has(c) && c.code !== toIso(origin)}
      label={label}
      name={nameOf}
      tip={tip}
      focusCode={origin}
      order={order}
      highlights={highlights}
      onSelect={
        onSelect
          ? (iso) => {
              const d = outBy.get(iso) || inBy.get(iso);
              if (d) onSelect(d.code, d.name);
            }
          : undefined
      }
    >
      {overlay}
    </WorldMapBase>
  );
};
