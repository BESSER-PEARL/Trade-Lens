// Design tokens of the Crossroads page, for code that cannot use the CSS
// variables in index.css (SVG attributes, computed colours). Keep the two in step.
// The data is only ever sky (exports) and steel (imports); any other colour
// on the page was drawn by an agent.

import type { CSSProperties } from "react";
import type { AgentInfo, RegisteredAgent, Tone } from "./types";

export const TOKENS = {
  paper: "#F5F7F9",
  surface: "#FFFFFF",
  ink: "#14202E",
  ink2: "#3B4858",
  muted: "#5E6B7A",
  sky: "#0E8FD0",
  skyText: "#0A6FA3",
  steel: "#7A8796",
  steelText: "#5E6B7A",
  rule: "#DCE2E8",
  ruleSoft: "#E6EAEF",
  grid: "#E1E6EC",
  field: "#C5CED8",
  track: "#E1E6EC",
  dotEmpty: "#AEB8C4",
} as const;

export const FONT_UI = "'Archivo', system-ui, -apple-system, 'Segoe UI', sans-serif";
export const FONT_AGENT = "'Newsreader', Georgia, 'Times New Roman', serif";

/** Big figures: Archivo condensed, heavy, tabular numerals. */
export const FIGURE_STYLE: CSSProperties = {
  fontFamily: FONT_UI,
  fontStretch: "68%",
  fontWeight: 800,
  fontVariantNumeric: "tabular-nums lining-nums",
  lineHeight: 0.9,
  letterSpacing: "-0.02em",
};

export const NUM_STYLE: CSSProperties = {
  fontVariantNumeric: "tabular-nums lining-nums",
};

/** Data colour of a tone: the fill, and the darker variant for small text. */
export function toneColor(tone?: Tone | string): { fill: string; text: string } {
  if (tone === "exports") return { fill: TOKENS.sky, text: TOKENS.skyText };
  if (tone === "imports") return { fill: TOKENS.steel, text: TOKENS.steelText };
  return { fill: TOKENS.ink, text: TOKENS.ink };
}

/** The four agents, used when GET /agents is empty or unreachable. */
export const AGENT_COLORS: Record<string, { title: string; color: string; ink: string; can: string }> = {
  trend_finder: {
    title: "Trend finder",
    color: "#E2142D",
    ink: "#B5101F",
    can: "Finds what changed and drills down to the product line",
  },
  cause_finder: {
    title: "Cause finder",
    color: "#6D3FC0",
    ink: "#5A2FA8",
    can: "Lines changes up with events and trade agreements",
  },
  forecaster: {
    title: "Forecaster",
    color: "#C17F12",
    ink: "#8A5A12",
    can: "Extends a series with an uncertainty band",
  },
  visualizer: {
    title: "Visualizer",
    color: "#0F7B6C",
    ink: "#0B6156",
    can: "Puts any partner, product or year on the page",
  },
};

export const AGENT_ORDER = ["visualizer", "trend_finder", "cause_finder", "forecaster"];

/** Darker shade of a hex colour, for the ink of an agent registered without one. */
export function darken(hex: string, amount = 0.2): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const ch = (shift: number) => Math.max(0, Math.round(((n >> shift) & 255) * (1 - amount)));
  return "#" + [ch(16), ch(8), ch(0)].map((v) => v.toString(16).padStart(2, "0")).join("");
}

function titleOf(name: string): string {
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Merges the registry with the fallback pens; registry agents first in the demo order. */
export function agentInfos(registry: RegisteredAgent[]): AgentInfo[] {
  const byName: Record<string, AgentInfo> = {};
  const order: string[] = [];
  const put = (info: AgentInfo) => {
    if (!byName[info.name]) order.push(info.name);
    byName[info.name] = info;
  };
  registry.forEach((a) => {
    const fallback = AGENT_COLORS[a.name];
    const color = a.color || fallback?.color || TOKENS.ink;
    put({
      name: a.name,
      title: a.title || fallback?.title || titleOf(a.name),
      color,
      ink: fallback && fallback.color.toLowerCase() === color.toLowerCase() ? fallback.ink : darken(color),
      can: a.capabilities?.[0]?.description || fallback?.can || "",
      examples: a.examples || [],
      online: true,
    });
  });
  if (registry.length === 0) {
    AGENT_ORDER.forEach((name) => {
      const f = AGENT_COLORS[name];
      put({ name, title: f.title, color: f.color, ink: f.ink, can: f.can, examples: [], online: false });
    });
  }
  const rank = (name: string) => {
    const i = AGENT_ORDER.indexOf(name);
    return i < 0 ? AGENT_ORDER.length : i;
  };
  return order.sort((a, b) => rank(a) - rank(b)).map((n) => byName[n]);
}

/** True when the owner is an agent: neither the page's baseline nor the user's own controls. */
export function isAgentOwner(agent: string | undefined): boolean {
  return !!agent && agent !== "baseline" && agent !== "user";
}

/** The pen of any agent name: registry first, then the demo pens, then ink. */
export function penFor(agent: string | undefined, agents?: AgentInfo[]): { color: string; ink: string; title: string } {
  const name = agent || "";
  const hit = agents?.find((a) => a.name === name);
  if (hit) return { color: hit.color, ink: hit.ink, title: hit.title };
  const f = AGENT_COLORS[name];
  if (f) return { color: f.color, ink: f.ink, title: f.title };
  if (name === "user") return { color: TOKENS.ink, ink: TOKENS.ink, title: "You" };
  return { color: TOKENS.ink, ink: TOKENS.ink, title: titleOf(name || "agent") };
}
