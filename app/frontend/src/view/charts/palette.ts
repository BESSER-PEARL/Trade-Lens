// Colours of the charts on top of the page tokens (../tokens): the two sequential ramps of the
// data colours and the map's empty fill. The data is only ever sky (exports) and steel
// (imports); any other colour on a chart is an agent's pen, passed in by the panel.

import { TOKENS } from "../tokens";

export { TOKENS, FONT_UI, FONT_AGENT, toneColor } from "../tokens";

/** Sequential ramps, one hue each, light to dark (monotone lightness). Index 4 is the token itself. */
export const SKY_RAMP = ["#E6F3FB", "#C4E2F5", "#96CBEC", "#5FAFE0", "#0E8FD0", "#0B72A8", "#084F75"];
export const STEEL_RAMP = ["#EDF0F3", "#D5DBE1", "#B7C0CA", "#98A4B0", "#7A8796", "#5E6B7A", "#414B57"];

/** Countries without data, and the hairline between countries. */
export const MAP_EMPTY = "#E6EAEF";
export const MAP_STROKE = "#FFFFFF";

export function rampFor(tone?: string): string[] {
  return tone === "imports" ? STEEL_RAMP : SKY_RAMP;
}

/** Ink or white for a label set inside a fill, by the fill's luminance. */
export function labelOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return TOKENS.ink;
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return lum > 0.6 ? TOKENS.ink : "#FFFFFF";
}

/** A pen mark drawn on a chart: what the panel passes to a chart component. */
export interface PenMark {
  /** Mark id, a React key. */
  id: string;
  /** Codes the mark points at (highlight codes, an annotation's x...). */
  codes: string[];
  /** The agent's pen. */
  color: string;
  /** The agent's ink (its small text). */
  ink: string;
  /** The storyline number, shown in a filled circle when showNumber. */
  n: number;
  showNumber: boolean;
  label?: string;
  /** 1, or less when the mark is about another period than the page's. */
  opacity: number;
}
