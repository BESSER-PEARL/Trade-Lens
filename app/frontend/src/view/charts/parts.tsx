// Pieces every chart shares: the "Show as table" toggle with its plain table, the ramp legend,
// a data swatch, the key of the agents' marks and the numbered circle inside an SVG.

import React from "react";
import { FONT_UI, TOKENS } from "./palette";
import type { PenMark } from "./palette";

const visuallyHidden: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};

/** The plain table behind a chart, for screen readers, copy-paste and colour-blind readers. */
export const DataTable: React.FC<{ columns: string[]; rows: (string | number)[][]; caption?: string }> = ({ columns, rows, caption }) => (
  <table style={{ borderCollapse: "collapse", fontSize: 13, fontVariantNumeric: "tabular-nums", minWidth: 280 }}>
    {caption && <caption style={{ textAlign: "left", color: TOKENS.muted, padding: "0 0 4px" }}>{caption}</caption>}
    <thead>
      <tr>
        {columns.map((c, i) => (
          <th
            key={`${c}-${i}`}
            scope="col"
            style={{ textAlign: i === 0 ? "left" : "right", padding: "4px 12px 4px 0", borderBottom: `1px solid ${TOKENS.rule}`, color: TOKENS.muted, fontWeight: 600, whiteSpace: "nowrap" }}
          >
            {c}
          </th>
        ))}
      </tr>
    </thead>
    <tbody>
      {rows.map((r, i) => (
        <tr key={i}>
          {r.map((v, j) =>
            j === 0 ? (
              <th key={j} scope="row" style={{ textAlign: "left", fontWeight: 400, padding: "3px 12px 3px 0", borderBottom: `1px solid ${TOKENS.ruleSoft}` }}>
                {v}
              </th>
            ) : (
              <td key={j} style={{ textAlign: "right", padding: "3px 12px 3px 0", borderBottom: `1px solid ${TOKENS.ruleSoft}`, whiteSpace: "nowrap" }}>
                {v}
              </td>
            ),
          )}
        </tr>
      ))}
    </tbody>
  </table>
);

/** "Show as table" under a chart. */
export const TableToggle: React.FC<{ children?: React.ReactNode }> = ({ children }) => (
  <details style={{ fontSize: 13, color: TOKENS.ink2, fontFamily: FONT_UI }}>
    <summary style={{ cursor: "pointer", color: TOKENS.skyText, fontWeight: 600, minHeight: 32, display: "inline-flex", alignItems: "center" }}>
      Show as table
    </summary>
    <div style={{ marginTop: 6, overflowX: "auto", maxHeight: 420, overflowY: "auto" }}>{children}</div>
  </details>
);

/** A legend row of swatches with a label at each step. */
export const RampLegend: React.FC<{ steps: string[]; labels: string[]; note?: string; title?: string; stepWidth?: number }> = ({
  steps,
  labels,
  note,
  title,
  stepWidth,
}) => {
  // Wide enough for the longest label, so neighbours never overlap.
  const w = stepWidth ?? Math.max(40, 8 + 6.4 * Math.max.apply(null, labels.map((l) => (l || "").length).concat([0])));
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px 14px", fontSize: 12.5, color: TOKENS.muted, fontFamily: FONT_UI }}>
      {title && <span style={{ fontWeight: 600, color: TOKENS.ink2 }}>{title}</span>}
      <span aria-hidden="true" style={{ display: "flex", alignItems: "flex-end", flex: "0 1 auto", width: steps.length * w + 24, minWidth: 0, maxWidth: "100%", paddingRight: 24, boxSizing: "border-box" }}>
        {steps.map((s, i) => (
          // Steps shrink on a phone; the labels may run past their step, never off the page.
          <span key={i} style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", flex: "1 1 0", minWidth: 18 }}>
            <span style={{ display: "block", width: "calc(100% - 2px)", height: 10, background: s, borderRadius: i === 0 ? "3px 0 0 3px" : i === steps.length - 1 ? "0 3px 3px 0" : 0 }} />
            <span style={{ fontSize: 11, marginTop: 3, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{labels[i] || " "}</span>
          </span>
        ))}
      </span>
      <span style={visuallyHidden}>
        Colour scale from {labels[0]} to {labels.filter((l) => !!l).slice(-1)[0]}.
      </span>
      {note && <span>{note}</span>}
    </div>
  );
};

/** One entry of a chart's key: a swatch and its words. */
export const Swatch: React.FC<{ color: string; label: React.ReactNode; shape?: "bar" | "line" | "dot" | "outline" | "hatch" }> = ({ color, label, shape = "bar" }) => {
  const style: React.CSSProperties =
    shape === "line"
      ? { width: 18, height: 3, borderRadius: 2, background: color }
      : shape === "dot"
        ? { width: 10, height: 10, borderRadius: "50%", background: color }
        : shape === "outline"
          ? { width: 12, height: 12, borderRadius: 3, border: `2px solid ${color}`, boxSizing: "border-box" }
          : shape === "hatch"
            ? { width: 14, height: 10, borderRadius: 2, boxSizing: "border-box", border: `1.5px solid ${color}`, background: `repeating-linear-gradient(45deg, ${color} 0 1px, transparent 1px 4px)` }
            : { width: 14, height: 10, borderRadius: 2, background: color };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span aria-hidden="true" style={{ flex: "none", ...style }} />
      {label}
    </span>
  );
};

/** The row under a chart: data swatches first, then the agents' marks in their ink. */
export const ChartKey: React.FC<{ children?: React.ReactNode; marks?: PenMark[] }> = ({ children, marks = [] }) => {
  const labelled = marks.filter((m) => m.showNumber || m.label);
  if (!children && !labelled.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px", alignItems: "center", fontSize: 12.5, color: TOKENS.muted, fontFamily: FONT_UI }}>
      {children}
      {labelled.map((m) => (
        <span key={m.id} style={{ display: "inline-flex", alignItems: "center", gap: 6, color: m.ink, fontWeight: 600, opacity: m.opacity }}>
          {m.showNumber ? <HtmlNumber n={m.n} color={m.color} /> : <span aria-hidden="true" style={{ width: 12, height: 12, borderRadius: 3, border: `2px solid ${m.color}` }} />}
          {m.label || "Highlighted"}
        </span>
      ))}
    </div>
  );
};

/** The numbered circle in HTML (a mark's storyline number). */
export const HtmlNumber: React.FC<{ n: number | string; color: string; size?: number }> = ({ n, color, size = 18 }) => (
  <span
    aria-hidden="true"
    style={{
      flex: "none",
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: size,
      height: size,
      borderRadius: "50%",
      background: color,
      color: "#FFFFFF",
      fontSize: Math.round(size * 0.6),
      fontWeight: 700,
      lineHeight: 1,
      fontVariantNumeric: "tabular-nums",
    }}
  >
    {n}
  </span>
);

/** The numbered circle inside an SVG chart, in the agent's pen. */
export const SvgBadge: React.FC<{ x: number; y: number; n: number | string; color: string; r?: number; opacity?: number }> = ({ x, y, n, color, r = 10, opacity = 1 }) => (
  <g aria-hidden="true" pointerEvents="none" opacity={opacity}>
    <circle cx={x} cy={y} r={r} fill={color} stroke="#FFFFFF" strokeWidth={2} />
    <text x={x} y={y + r * 0.4} textAnchor="middle" fontFamily={FONT_UI} fontSize={r * 1.2} fontWeight={700} fill="#FFFFFF">
      {n}
    </text>
  </g>
);

/** Visually hidden text for assistive technology. */
export const SrOnly: React.FC<{ children?: React.ReactNode }> = ({ children }) => <span style={visuallyHidden}>{children}</span>;

/** A unique id for SVG defs (patterns), so two charts on one page never share one by accident. */
export function useSvgId(prefix: string): string {
  const id = React.useId();
  return `${prefix}-${id.replace(/[^a-zA-Z0-9_-]/g, "")}`;
}
