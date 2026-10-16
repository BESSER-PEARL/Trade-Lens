// Renders spec.layout: one row per layout row, one to three panels per row,
// ruled sections rather than cards. `filter` renders a subset and `rows` a
// given run of layout rows, so the page can place the baseline panels in its
// own layout and put the panels agents add where spec.layout says they go.

import React from "react";
import { Panel, PanelProps } from "./Panel";
import { FONT_UI, TOKENS } from "./tokens";
import { useViewStore } from "./ViewStore";
import { panelDomId } from "./panelPrompts";
import type { LayoutRow } from "./types";

export interface DynamicCanvasProps {
  /** Which panels to draw; rows left empty are skipped. */
  filter?: (panelId: string) => boolean;
  /** The layout rows to draw (default: all of spec.layout). */
  rows?: LayoutRow[];
  className?: string;
  /** Props passed to every Panel (notes, heading...). */
  panelProps?: Omit<PanelProps, "id">;
  /** Rule above each row (default true). */
  ruled?: boolean;
  /** Gap between rows in px (default 44). */
  gap?: number;
}

export const DynamicCanvas: React.FC<DynamicCanvasProps> = ({ filter, rows: only, className, panelProps, ruled = true, gap = 44 }) => {
  const { spec } = useViewStore();
  if (!spec) return null;
  const byId: Record<string, (typeof spec.panels)[number]> = {};
  spec.panels.forEach((p) => (byId[p.id] = p));
  const rows = (only || spec.layout.rows)
    .map((r) => ({ ...r, panels: r.panels.filter((id) => byId[id] && (!filter || filter(id))) }))
    .filter((r) => r.panels.length > 0);
  if (rows.length === 0) return null;

  return (
    <div className={className} style={{ display: "flex", flexDirection: "column", gap, fontFamily: FONT_UI }}>
      {rows.map((r) => {
        // Charts never sit side by side (each gets the full width to be read); only small figure cards share a row.
        const many = r.panels.length > 1 && r.panels.every((id) => byId[id]?.type === "kpi");
        return (
          <div
            key={r.panels.join("|")}
            role={r.label ? "group" : undefined}
            aria-label={r.label}
            className="cx-agent-row"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 12,
              paddingTop: ruled ? 28 : 0,
              borderTop: ruled ? `2px solid ${TOKENS.ink}` : undefined,
              minWidth: 0,
            }}
          >
            {r.label && <span style={{ fontSize: 14, fontWeight: 600, color: TOKENS.muted }}>{r.label}</span>}
            <div
              style={
                many
                  ? { display: "grid", gridTemplateColumns: `repeat(auto-fit, minmax(${r.panels.length === 3 ? 250 : 320}px, 1fr))`, gap: 32 }
                  : { display: "flex", flexDirection: "column", gap: 40, minWidth: 0 }
              }
            >
              {/* A panel pinned to another period than the page's carries its "about 2025" tag itself (Panel). */}
              {r.panels.map((id) => (
                <div key={id} id={panelDomId(id)} className="cx-agent-panel" style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
                  <Panel id={id} {...panelProps} />
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
};
