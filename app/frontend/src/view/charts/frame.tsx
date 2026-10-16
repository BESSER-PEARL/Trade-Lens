// The frame around every new chart panel: the chart, its key, the panel's text with its
// facts filled, a quiet note when there is nothing to draw, and "Show as table".

import React from "react";
import { FactText } from "../common";
import type { ResolvedFacts } from "../facts";
import { FONT_AGENT, FONT_UI, TOKENS, isAgentOwner } from "../tokens";
import type { Panel } from "../types";
import { TableToggle } from "./parts";

/** The panel's own text (panel.text) under the chart; in Newsreader when an agent wrote it. */
export const PanelCaption: React.FC<{ panel: Panel; facts: ResolvedFacts }> = ({ panel, facts }) => {
  if (!panel.text) return null;
  const byAgent = isAgentOwner(panel.agent);
  return (
    <p
      style={{
        margin: 0,
        maxWidth: "62em",
        fontFamily: byAgent ? FONT_AGENT : FONT_UI,
        fontSize: byAgent ? 16 : 13,
        lineHeight: 1.5,
        color: byAgent ? TOKENS.ink : TOKENS.muted,
      }}
    >
      <FactText template={panel.text} facts={facts} />
    </p>
  );
};

/** Nothing to draw: the query answered, but with no rows. Never a zero. */
export const EmptyNote: React.FC<{ children?: React.ReactNode }> = ({ children }) => (
  <div
    role="status"
    style={{
      fontFamily: FONT_UI,
      fontSize: 14,
      color: TOKENS.muted,
      padding: "18px 16px",
      border: `1px dashed ${TOKENS.field}`,
      borderRadius: 8,
    }}
  >
    {children || "No rows for this selection."}
  </div>
);

/** The chart with its key, caption and table, in the panel's column. */
export const ChartFrame: React.FC<{
  chart: React.ReactNode;
  chartKey?: React.ReactNode;
  caption?: React.ReactNode;
  table?: React.ReactNode;
  note?: React.ReactNode;
}> = ({ chart, chartKey, caption, table, note }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0, fontFamily: FONT_UI }}>
    {chart}
    {chartKey}
    {note && <span style={{ fontSize: 12.5, color: TOKENS.muted, lineHeight: 1.45 }}>{note}</span>}
    {caption}
    {table && <TableToggle>{table}</TableToggle>}
  </div>
);
