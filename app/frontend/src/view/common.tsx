// Small pieces shared by the panels: status cards, the numbered circle, text
// with {fact} placeholders filled from the API.

import React from "react";
import { FactValue, ResolvedFacts, keepFigures, splitTemplate } from "./facts";
import { FONT_AGENT, FONT_UI, NUM_STYLE, TOKENS } from "./tokens";
import { apiHref } from "./api";
import { aboutTag } from "./context";
import type { QueryState } from "./useQuery";
import { factOrigin, parseUrl } from "./methods";

/** The fill of a pen behind white text: teal (the visualizer) is too light, so it fills with its ink. */
export function penFill(color: string): string {
  return String(color).toLowerCase() === "#0f7b6c" ? "#0B6156" : color;
}

/** A filled circle with a mark's (storyline's) number, in the agent's pen. */
export const NumberCircle: React.FC<{ n: number | string; color: string; size?: number; style?: React.CSSProperties; title?: string }> = ({
  n,
  color,
  size = 20,
  style,
  title,
}) => (
  <span
    title={title}
    aria-label={title || `Note ${n}`}
    style={{
      flex: "none",
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: size,
      height: size,
      borderRadius: "50%",
      background: penFill(color),
      color: "#FFFFFF",
      fontFamily: FONT_UI,
      fontSize: Math.round(size * 0.6),
      fontWeight: 700,
      lineHeight: 1,
      ...NUM_STYLE,
      ...style,
    }}
  >
    {n}
  </span>
);

/** The numbered circle inside an SVG chart. */
export const SvgNumber: React.FC<{ x: number; y: number; n: number | string; color: string; r?: number }> = ({ x, y, n, color, r = 10 }) => (
  <g aria-hidden="true">
    <circle cx={x} cy={y} r={r} fill={penFill(color)} stroke="#FFFFFF" strokeWidth={2} />
    <text
      x={x}
      y={y + r * 0.42}
      textAnchor="middle"
      style={{ fontFamily: FONT_UI, fontSize: r * 1.2, fontWeight: 700, fill: "#FFFFFF" }}
    >
      {n}
    </text>
  </g>
);

const cardBase: React.CSSProperties = {
  fontFamily: FONT_UI,
  fontSize: 14,
  lineHeight: 1.5,
  borderRadius: 8,
  padding: "14px 16px",
  display: "flex",
  flexDirection: "column",
  gap: 4,
  minWidth: 0,
};

/** Grey card for what the renderer cannot draw: an unknown panel type or mark kind. */
export const UnsupportedCard: React.FC<{ what: string; detail?: string }> = ({ what, detail }) => (
  <div role="note" style={{ ...cardBase, background: "#EEF1F4", color: TOKENS.muted, border: `1px dashed ${TOKENS.field}` }}>
    <strong style={{ color: TOKENS.ink2, fontWeight: 600 }}>Unsupported {what}</strong>
    {detail && <span style={{ fontSize: 13 }}>{detail}</span>}
  </div>
);

/** An /insights call that failed: the message and the URL, never a zero. */
export const ErrorCard: React.FC<{ state?: QueryState; title?: string; message?: string }> = ({ state, title, message }) => (
  <div role="alert" style={{ ...cardBase, background: "#FFFFFF", color: TOKENS.ink2, border: `1px solid ${TOKENS.rule}` }}>
    <strong style={{ color: TOKENS.ink, fontWeight: 600 }}>{title || "This panel could not be drawn"}</strong>
    <span>{message || state?.error?.message}</span>
    {state?.url && (
      <code style={{ fontSize: 12, color: TOKENS.muted, wordBreak: "break-all" }}>
        {state.error?.status ? `${state.error.status} ` : ""}GET {state.url}
      </code>
    )}
  </div>
);

/** loaded:false from the API: the data is not there, which is not the same as zero. */
export const NotLoadedCard: React.FC<{ state?: QueryState }> = ({ state }) => (
  <div role="status" style={{ ...cardBase, background: "#FFFFFF", color: TOKENS.muted, border: `1px solid ${TOKENS.rule}` }}>
    <strong style={{ color: TOKENS.ink2, fontWeight: 600 }}>Not loaded</strong>
    <span>The backend has no data for this query yet.</span>
    {state?.url && <code style={{ fontSize: 12, wordBreak: "break-all" }}>GET {state.url}</code>}
  </div>
);

/** A quiet placeholder of the panel's height while its queries load. */
export const LoadingBlock: React.FC<{ height?: number | string }> = ({ height = 120 }) => (
  <div
    aria-busy="true"
    aria-label="Loading"
    style={{
      height,
      borderRadius: 6,
      background: `linear-gradient(90deg, ${TOKENS.ruleSoft} 0%, #EEF1F4 50%, ${TOKENS.ruleSoft} 100%)`,
      opacity: 0.7,
    }}
  />
);

/** Problem card for a panel's query states, or null when they are all ready. */
export function problemCard(problem: { kind: "error" | "not_loaded" | "loading"; state: QueryState } | null, height?: number | string) {
  if (!problem) return null;
  if (problem.kind === "error") return <ErrorCard state={problem.state} />;
  if (problem.kind === "not_loaded") return <NotLoadedCard state={problem.state} />;
  return <LoadingBlock height={height} />;
}

const OP_WORDS: Record<string, string> = { diff: "the difference of", sum: "the sum of", ratio: "the ratio of", growth: "the growth from", abs: "the size of" };

/** Where a figure comes from, in words: the calculation it is read from, or what the page did with two of them. */
export function originOf(v: FactValue | undefined): string | undefined {
  const s = v?.source;
  if (!s) return undefined;
  if (s.op) return `Worked out by the page: ${OP_WORDS[s.op] || s.op} ${(s.of || []).map((n) => n.replace(/_/g, " ")).join(" and ")}, each read from a calculation. Not typed by the assistant.`;
  if (s.url) {
    const q = parseUrl(s.url);
    return factOrigin(q.endpoint, q.params);
  }
  return undefined;
}

function factTitle(v: FactValue | undefined, name: string): string {
  if (!v) return name;
  if (v.status === "ok") return originOf(v) || name;
  return v.reason || v.status;
}

/** One {fact}: its value, or a visible gap that says why (never a zero). */
export const FactSpan: React.FC<{ name: string; value?: FactValue; emphasis?: boolean; text: string }> = ({ name, value, emphasis, text }) => {
  if (value?.status === "ok") {
    // A figure never breaks across lines; a text fact (a method's name, say) wraps like prose.
    const numeric = typeof value.value === "number";
    return (
      <span data-fact={name} title={originOf(value)} style={{ ...NUM_STYLE, fontWeight: emphasis ? 700 : undefined, whiteSpace: numeric ? "nowrap" : undefined }}>
        {text}
      </span>
    );
  }
  const label = value?.status === "loading" ? "…" : value?.status === "not_loaded" ? "not loaded" : "n/a";
  return (
    <span
      data-fact={name}
      title={factTitle(value, name)}
      style={{ color: TOKENS.muted, borderBottom: `1px dotted ${TOKENS.field}`, whiteSpace: "nowrap" }}
    >
      {label}
    </span>
  );
};

/** A template with its {fact} placeholders filled. */
export const FactText: React.FC<{ template?: string; facts: ResolvedFacts; emphasis?: boolean }> = ({ template, facts, emphasis }) => {
  if (!template) return null;
  const parts = splitTemplate(template, facts.values);
  return (
    <>
      {parts.map((p, i) =>
        p.fact ? <FactSpan key={i} name={p.fact} value={p.value} emphasis={emphasis} text={p.text} /> : <React.Fragment key={i}>{keepFigures(p.text)}</React.Fragment>,
      )}
    </>
  );
};

/** The list of API calls behind a panel or mark, as links. */
export const SourceList: React.FC<{ urls: string[]; style?: React.CSSProperties }> = ({ urls, style }) => (
  <code style={{ display: "block", fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-all", color: TOKENS.ink2, ...style }}>
    {urls.map((u, i) => (
      <span key={i} style={{ display: "block" }}>
        {u.startsWith("/") ? (
          <a href={apiHref(u)} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>
            GET {u}
          </a>
        ) : (
          u
        )}
      </span>
    ))}
  </code>
);

/**
 * The period an agent's panel is pinned to, when none of the literal periods its queries name
 * (period, previous, end, or a start..end range) covers the period the page shows: "2024" for a panel
 * about 2023 and 2024 on a page showing 2020; undefined for a panel about 2024 and 2025 on a page
 * showing 2025, for a series from 1999 to June 2026 on a page showing 2020, and for a panel whose data
 * follows the page ("$period"). The data queries decide; the facts only when the queries name no period,
 * so a caption fact that follows the page never hides that the chart itself is about another year.
 */
export function offPeriodOf(queries: Array<{ params?: Record<string, any> }>, facts: Record<string, any> | undefined, ctxPeriod: string): string | undefined {
  const factQueries: Array<{ params?: Record<string, any> }> = [];
  Object.keys(facts || {}).forEach((k) => {
    const f = (facts as any)[k];
    if (f && f.endpoint) factQueries.push(f);
  });
  const year = String(ctxPeriod).slice(0, 4);
  const pinnedOf = (qs: Array<{ params?: Record<string, any> }>): { found: string[]; follows: boolean; covers: boolean } => {
    const found: string[] = [];
    let follows = false;
    let covers = false;
    qs.forEach((q) => {
      const p = q.params || {};
      ["period", "previous", "start", "end"].forEach((name) => {
        const v = p[name];
        if (v === undefined || v === null) return;
        if (/^\$/.test(String(v))) follows = true;
        else if (name !== "start") found.push(String(v));
      });
      // A literal range that runs over the year shown is about that year too.
      const lo = p.start !== undefined && !/^\$/.test(String(p.start)) ? String(p.start).slice(0, 4) : undefined;
      const hi = p.end !== undefined && !/^\$/.test(String(p.end)) ? String(p.end).slice(0, 4) : undefined;
      if ((lo || hi) && (!lo || lo <= year) && (!hi || year <= hi)) covers = true;
    });
    return { found, follows, covers };
  };
  let pinned = pinnedOf(queries);
  if (!pinned.follows && !pinned.found.length && !pinned.covers) pinned = pinnedOf(factQueries);
  if (pinned.follows || pinned.covers || !pinned.found.length) return undefined;
  if (pinned.found.some((p) => p === ctxPeriod || p.slice(0, 4) === year)) return undefined;
  return pinned.found.sort().slice(-1)[0];
}

/** The width of an element in px, kept up to date (ResizeObserver); `fallback` before the first layout. */
export function useWidth<T extends HTMLElement = HTMLDivElement>(fallback = 720): { ref: (el: T | null) => void; width: number } {
  // A callback ref: the element may appear only after the panel's data has loaded.
  const [el, ref] = React.useState<T | null>(null);
  const [width, setWidth] = React.useState(fallback);
  React.useLayoutEffect(() => {
    if (!el) return;
    const read = () => {
      const w = Math.round(el.getBoundingClientRect().width);
      if (w > 0) setWidth((old) => (old === w ? old : w));
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return { ref, width };
}

/** Approximate width in px of a string of UI text (Archivo) at a font size, for label placement. */
export function textWidth(s: string, size = 13, bold = false): number {
  return String(s).length * size * (bold ? 0.56 : 0.52);
}

/**
 * Spreads label centres apart so no two are closer than `gap`, keeping them as
 * near their anchors as possible and inside [min, max]. Returns new positions
 * in the input order.
 */
export function spreadLabels(anchors: number[], gap: number, min = -Infinity, max = Infinity): number[] {
  const idx = anchors.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y);
  const pos = idx.map((d) => d.y);
  for (let k = 0; k < 4; k++) {
    for (let j = 1; j < pos.length; j++) if (pos[j] - pos[j - 1] < gap) pos[j] = pos[j - 1] + gap;
    if (pos.length && pos[pos.length - 1] > max) pos[pos.length - 1] = max;
    for (let j = pos.length - 2; j >= 0; j--) if (pos[j + 1] - pos[j] < gap) pos[j] = pos[j + 1] - gap;
    if (pos.length && pos[0] < min) pos[0] = min;
  }
  const out: number[] = new Array(anchors.length);
  idx.forEach((d, j) => (out[d.i] = pos[j]));
  return out;
}

export const AGENT_TEXT_STYLE: React.CSSProperties = {
  fontFamily: FONT_AGENT,
  color: TOKENS.ink,
};

/** Panel title and subtitle, in the quiet ruled style of the page. */
/**
 * Sentence case that keeps codes and acronyms upper case: "Cn8 lines of 851713" -> "CN8 lines of
 * 851713" (a label capitalised by an older agent), "hs 85" -> "HS 85", "Rca" -> "RCA".
 */
export function codeCase(text: string | undefined): string | undefined {
  if (!text) return text;
  return text.replace(/\b(cn8|cn|hs[246]?|rca|eu27|eu|stl|mase|bleu|sdmx|api)\b/gi, (w) => w.toUpperCase());
}

/**
 * A chart's heading: the title, the subtitle and the header controls (`aside`), and under them `meta`, the
 * chart's own quiet line (contract 1.5: its year and goods, ChartContextMenu).
 */
export const PanelHeading: React.FC<{ title?: string; subtitle?: string; level?: 2 | 3; aside?: React.ReactNode; meta?: React.ReactNode }> = ({ title, subtitle, level = 3, aside, meta }) => {
  if (!title && !subtitle && !aside && !meta) return null;
  const H = (level === 2 ? "h2" : "h3") as "h2" | "h3";
  // The title on the left, the chart's controls fixed at the top right (always in the same place), the subtitle
  // and the quiet year/goods line under the title.
  return (
    <div className="cx-heading">
      <div className="cx-heading-text">
        {title && (
          <H style={{ margin: 0, fontFamily: FONT_UI, fontSize: level === 2 ? 26 : 19, fontWeight: 700, letterSpacing: level === 2 ? "-0.01em" : undefined, color: TOKENS.ink, lineHeight: 1.2 }}>
            {codeCase(title)}
          </H>
        )}
        {subtitle && <p className="cx-heading-sub">{codeCase(subtitle)}</p>}
      </div>
      {aside && <div className="cx-heading-aside">{aside}</div>}
      {meta && <div className="cx-heading-meta">{meta}</div>}
    </div>
  );
};

/** The small "about 2025" tag of something drawn about another period than the page shows. */
export const AboutTag: React.FC<{ period: string; style?: React.CSSProperties }> = ({ period, style }) => (
  <span
    className="cx-about"
    title={`Written about ${period}; the page shows another period`}
    style={{
      display: "inline-block",
      fontFamily: FONT_UI,
      fontSize: 12.5,
      fontWeight: 600,
      lineHeight: 1.3,
      color: TOKENS.ink2,
      background: "#E9EDF1",
      borderRadius: 4,
      padding: "1px 6px",
      verticalAlign: "middle",
      whiteSpace: "nowrap",
      ...style,
    }}
  >
    {aboutTag(period)}
  </span>
);
