// SPDX-License-Identifier: MIT
// The Trends page draws with the app's design tokens (view/tokens.ts, read-only here): paper
// ground, ink text, sky for exports and steel for imports. Agent pens are not used: every mark
// on this page comes from the page's own analysis, so change points and the trend are in ink.
import type React from "react";
import { FONT_UI, NUM_STYLE, TOKENS } from "../../view/tokens";

export { FIGURE_STYLE, FONT_UI, NUM_STYLE, TOKENS } from "../../view/tokens";

/** Error text, as on the Overview page. */
export const ERROR_INK = "#8E1B12";

/** The data colour of the selected flow: sky for exports, steel for imports, ink otherwise. */
export function flowTone(flow?: string): { fill: string; text: string; name: string } {
  const f = (flow || "").toUpperCase();
  if (f.startsWith("EXP")) return { fill: TOKENS.sky, text: TOKENS.skyText, name: "Exports" };
  if (f.startsWith("IMP")) return { fill: TOKENS.steel, text: TOKENS.steelText, name: "Imports" };
  return { fill: TOKENS.ink, text: TOKENS.ink, name: "Value" };
}

export const DIRECTION_LABEL: Record<string, string> = {
  increasing: "Rising",
  decreasing: "Falling",
  stable: "Stable",
  volatile: "Volatile",
};

export const SHAPE_LABEL: Record<string, string> = {
  steady: "steady",
  accelerating: "accelerating",
  decelerating: "slowing",
  reversing: "reversing",
};

/** The readout box of a chart: white, a hairline ring, a soft shadow. */
export const READOUT_STYLE: React.CSSProperties = {
  position: "absolute",
  pointerEvents: "none",
  background: TOKENS.surface,
  color: TOKENS.ink,
  fontFamily: FONT_UI,
  fontSize: 13,
  lineHeight: 1.4,
  padding: "8px 10px",
  borderRadius: 8,
  boxShadow: `0 0 0 1px ${TOKENS.rule}, 0 6px 18px rgba(20, 32, 46, 0.12)`,
  zIndex: 3,
  ...NUM_STYLE,
};
