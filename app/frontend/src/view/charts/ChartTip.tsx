// The hover and keyboard-focus readout of every chart. The chart wrapper is position: relative;
// the tip sits in the wrapper's pixel space and flips when it would leave the right edge.
// Values lead, labels follow.

import React from "react";
import { FONT_UI, TOKENS } from "./palette";

export interface TipRow {
  /** The strong element. */
  value: string;
  /** The secondary element. */
  label?: string;
  /** A short stroke of the series colour before the row. */
  swatch?: string;
}

export interface ChartTipProps {
  x: number;
  y: number;
  width: number;
  title?: string;
  rows: TipRow[];
  visible: boolean;
}

export const ChartTip: React.FC<ChartTipProps> = ({ x, y, width, title, rows, visible }) => {
  if (!visible) return null;
  const flip = x > width - 200;
  return (
    <div
      style={{
        position: "absolute",
        left: flip ? undefined : x + 12,
        right: flip ? Math.max(0, width - x + 12) : undefined,
        top: Math.max(0, y - 10),
        pointerEvents: "none",
        background: TOKENS.surface,
        color: TOKENS.ink,
        fontFamily: FONT_UI,
        fontSize: 13,
        lineHeight: 1.35,
        padding: "8px 10px",
        borderRadius: 8,
        boxShadow: `0 0 0 1px ${TOKENS.rule}, 0 6px 18px rgba(20, 32, 46, 0.12)`,
        maxWidth: 260,
        zIndex: 3,
      }}
    >
      {title && <div style={{ fontWeight: 700, marginBottom: rows.length ? 4 : 0 }}>{title}</div>}
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          {r.swatch && <span aria-hidden="true" style={{ display: "inline-block", width: 12, height: 3, borderRadius: 2, background: r.swatch, flex: "none" }} />}
          <span style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{r.value}</span>
          {r.label && <span style={{ color: TOKENS.muted }}>{r.label}</span>}
        </div>
      ))}
    </div>
  );
};

/** Pointer position relative to a wrapper element. */
export function localPoint(e: { clientX: number; clientY: number }, el: HTMLElement | null): { x: number; y: number } {
  if (!el) return { x: e.clientX, y: e.clientY };
  const r = el.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

/** Centre of an SVG element relative to the wrapper, for keyboard focus. */
export function elementPoint(target: Element | null, el: HTMLElement | null): { x: number; y: number } {
  if (!target || !el) return { x: 0, y: 0 };
  const a = target.getBoundingClientRect();
  const b = el.getBoundingClientRect();
  return { x: a.left - b.left + a.width / 2, y: a.top - b.top + a.height / 2 };
}
