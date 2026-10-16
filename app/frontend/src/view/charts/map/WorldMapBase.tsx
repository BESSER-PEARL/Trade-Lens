// The map every map panel is built on: one geometry, one projection, responsive to its
// container, with hover and keyboard access to the countries that carry data. It knows nothing
// about values: Choropleth and FlowMap pass the fill, labels, an overlay and a key. Countries with
// data form one roving tab stop; arrow keys move through them in the order the panel gives (rank
// order). Agents' highlight marks are outlined here, in their pen, with their number.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GeoPath, GeoProjection } from "d3-geo";
import { ChartTip, elementPoint, localPoint } from "../ChartTip";
import type { TipRow } from "../ChartTip";
import { FONT_UI, MAP_EMPTY, MAP_STROKE, TOKENS } from "../palette";
import type { PenMark } from "../palette";
import { SvgBadge } from "../parts";
import { useMeasure } from "../useMeasure";
import { centroidOf, fitProjection, toIso, worldGeometry } from "./geo";
import type { Country, Extent, WorldGeometry } from "./geo";

export interface MapContext {
  projection: GeoProjection;
  path: GeoPath;
  width: number;
  height: number;
  world: WorldGeometry;
  /** Screen position of a partner code's centroid, null when it has none. */
  project: (code: string) => [number, number] | null;
}

export interface WorldMapBaseProps {
  /** What the map shows, for assistive technology ("Map of export shares by partner, 2025"). */
  title: string;
  extent?: Extent;
  /** Caps the drawn height (the width then shrinks to keep the projection). */
  maxHeight?: number;
  fill?: (c: Country) => string;
  /** Countries that carry data: hoverable and focusable. */
  active?: (c: Country) => boolean;
  /** Accessible name of a country, with its value ("Germany, €3.99 bn, 25%"). */
  label?: (c: Country) => string;
  /** Display name of a country (the readout's title); defaults to the geometry's name. */
  name?: (c: Country) => string;
  /** Readout rows of a country. */
  tip?: (c: Country) => TipRow[];
  /** The reporter (Luxembourg): drawn in ink, never active. */
  focusCode?: string;
  /** Keyboard order, as partner codes (rank order). Defaults to active countries by name. */
  order?: string[];
  /** Agents' highlight marks: their codes outlined in the pen. */
  highlights?: PenMark[];
  /** Drawn above the countries, in map coordinates. */
  children?: (ctx: MapContext) => React.ReactNode;
  /** Click or Enter on a country with data: the page's own highlight (agent "user"). */
  onSelect?: (code: string) => void;
}

const svgStyle: React.CSSProperties = { display: "block", width: "100%", height: "auto", overflow: "hidden", borderRadius: 4, background: "#FFFFFF" };

export const WorldMapBase: React.FC<WorldMapBaseProps> = ({
  title,
  extent = "world",
  maxHeight = 520,
  fill,
  active,
  label,
  name,
  tip,
  focusCode = "LU",
  order,
  highlights = [],
  children,
  onSelect,
}) => {
  const { ref, width: measured } = useMeasure<HTMLDivElement>(720);
  const world = worldGeometry();
  const fitted = useMemo(() => {
    const first = fitProjection(measured, extent);
    if (first.height <= maxHeight) return { ...first, width: measured };
    const w = Math.max(240, Math.floor((measured * maxHeight) / first.height));
    return { ...fitProjection(w, extent), width: w };
  }, [measured, extent, maxHeight]);
  const { projection, path, height, width } = fitted;

  const ctx: MapContext = useMemo(
    () => ({
      projection,
      path,
      width,
      height,
      world,
      project: (code: string) => {
        const c = centroidOf(code);
        if (!c) return null;
        const p = projection(c);
        return p ? [p[0], p[1]] : null;
      },
    }),
    [projection, path, width, height, world],
  );

  const ds = useMemo(() => world.countries.map((c) => ({ c, d: path(c.feature) || "" })), [world, path]);
  const focusIso = toIso(focusCode || "");
  const isActive = useCallback((c: Country) => !!(active && c.code && c.code !== focusIso && active(c)), [active, focusIso]);
  const keyOrder = useMemo(() => {
    if (order && order.length) {
      return order.map(toIso).filter((code, i, all) => all.indexOf(code) === i && world.byCode.has(code) && isActive(world.byCode.get(code)!));
    }
    return world.countries
      .filter(isActive)
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((c) => c.code!);
  }, [order, world, isActive]);

  const [tabStop, setTabStop] = useState<string | null>(null);
  useEffect(() => {
    if (!tabStop || keyOrder.indexOf(tabStop) < 0) setTabStop(keyOrder[0] || null);
  }, [keyOrder, tabStop]);

  const wrap = useRef<HTMLDivElement | null>(null);
  const nodes = useRef(new Map<string, SVGPathElement>());
  const [hover, setHover] = useState<{ code: string; x: number; y: number } | null>(null);

  const showAt = (c: Country, x: number, y: number) => {
    if (c.code) setHover({ code: c.code, x, y });
  };
  const onKey = (e: React.KeyboardEvent<SVGSVGElement>) => {
    if (!keyOrder.length) return;
    const i = Math.max(0, keyOrder.indexOf(tabStop || keyOrder[0]));
    let next = i;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % keyOrder.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + keyOrder.length) % keyOrder.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = keyOrder.length - 1;
    else if (e.key === "Escape") {
      setHover(null);
      return;
    } else if ((e.key === "Enter" || e.key === " ") && onSelect && tabStop) {
      e.preventDefault();
      onSelect(tabStop);
      return;
    } else return;
    e.preventDefault();
    const code = keyOrder[next];
    setTabStop(code);
    nodes.current.get(code)?.focus();
  };

  const hovered = hover ? world.byCode.get(hover.code) : undefined;
  const tipRows: TipRow[] = hovered && tip ? tip(hovered) : [];

  // Highlight marks: outline each code's country in the pen; the number at the first code.
  const marked = highlights.map((m) => {
    const shapes = m.codes
      .map((code) => world.byCode.get(toIso(code)))
      .filter((c): c is Country => !!c)
      .map((c) => path(c.feature) || "");
    const dots = m.codes.filter((code) => !world.byCode.get(toIso(code))).map((code) => ctx.project(code)).filter((p): p is [number, number] => !!p);
    const anchor = m.codes.length ? ctx.project(m.codes[0]) : null;
    return { m, shapes, dots, anchor };
  });

  const lu = ctx.project(focusCode || "LU");

  return (
    <div ref={ref} style={{ width: "100%", fontFamily: FONT_UI }}>
      <div ref={wrap} style={{ position: "relative", width, maxWidth: "100%" }}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          style={svgStyle}
          role="group"
          aria-label={title}
          onKeyDown={onKey}
          onMouseLeave={() => setHover(null)}
          // A country without data (or the sea) under the pointer: no readout of the last one.
          onMouseMove={(e) => {
            if (hover && !(e.target as Element).getAttribute?.("data-active")) setHover(null);
          }}
        >
          <g>
            {ds.map(({ c, d }) => {
              const on = isActive(c);
              const isFocus = !!c.code && c.code === focusIso;
              const f = isFocus ? TOKENS.ink : fill ? fill(c) : MAP_EMPTY;
              return (
                <path
                  key={c.id}
                  ref={(el) => {
                    if (el && c.code) nodes.current.set(c.code, el);
                  }}
                  d={d}
                  fill={f}
                  stroke={hover && hover.code === c.code ? TOKENS.ink : MAP_STROKE}
                  strokeWidth={hover && hover.code === c.code ? 1.25 : 0.5}
                  strokeLinejoin="round"
                  style={{ outline: "none", cursor: on && onSelect ? "pointer" : undefined }}
                  tabIndex={on && c.code === tabStop ? 0 : on ? -1 : undefined}
                  role={on ? (onSelect ? "button" : "img") : undefined}
                  data-active={on ? "1" : undefined}
                  onClick={on && onSelect && c.code ? () => onSelect(c.code as string) : undefined}
                  aria-label={on ? (label ? label(c) : c.name) : undefined}
                  aria-hidden={on ? undefined : true}
                  onMouseMove={
                    on
                      ? (e) => {
                          const p = localPoint(e, wrap.current);
                          showAt(c, p.x, p.y);
                        }
                      : undefined
                  }
                  onFocus={
                    on
                      ? (e) => {
                          if (c.code) setTabStop(c.code);
                          const p = elementPoint(e.currentTarget, wrap.current);
                          showAt(c, p.x, p.y);
                        }
                      : undefined
                  }
                  onBlur={on ? () => setHover(null) : undefined}
                />
              );
            })}
          </g>
          {children && children(ctx)}
          {lu && (
            <g aria-hidden="true" pointerEvents="none">
              <circle cx={lu[0]} cy={lu[1]} r={4.5} fill={TOKENS.ink} stroke={TOKENS.surface} strokeWidth={2} />
            </g>
          )}
          {marked.map(({ m, shapes, dots, anchor }) => (
            <g key={m.id} aria-hidden="true" pointerEvents="none" opacity={m.opacity}>
              {shapes.map((d, i) => (
                <g key={i}>
                  <path d={d} fill="none" stroke={TOKENS.surface} strokeWidth={4} strokeLinejoin="round" />
                  <path d={d} fill="none" stroke={m.color} strokeWidth={2} strokeLinejoin="round" />
                </g>
              ))}
              {dots.map((p, i) => (
                <circle key={`d${i}`} cx={p[0]} cy={p[1]} r={7} fill="none" stroke={m.color} strokeWidth={2} />
              ))}
              {m.showNumber && anchor && <SvgBadge x={anchor[0] + 14} y={anchor[1] - 14} n={m.n} color={m.color} />}
            </g>
          ))}
        </svg>
        <ChartTip
          x={hover?.x ?? 0}
          y={hover?.y ?? 0}
          width={width}
          visible={!!hovered && tipRows.length > 0}
          title={hovered ? (name ? name(hovered) : hovered.name) : undefined}
          rows={tipRows}
        />
      </div>
    </div>
  );
};
