// Marks: what agents draw on panels, each in its agent's pen with its number in
// a filled circle. Marks that share a `thread` value form one storyline, shown
// as one numbered note. The number is the storyline's story_n (contract 1.6): the
// backend gives it once and it never changes, so a reply's "[3]" always points to
// the same storyline, or to nothing once it is gone.

import React from "react";
import { FactText, NumberCircle, SourceList, UnsupportedCard } from "./common";
import { formatPeriod, useFacts } from "./facts";
import { CHAPTER_SHORT } from "./charts/format";
import { countryName } from "./methods";
import { queryUrl } from "./api";
import { penFor, FONT_AGENT, FONT_UI, TOKENS } from "./tokens";
import { AiHypothesis, CAUSE_FINDER } from "./AiHypothesis";
import { useQuery } from "./useQuery";
import { aboutTag, markOffContext, markPeriod, substituteQuery, useMarkContext, useViewStore } from "./ViewStore";
import type { AgentInfo, Mark, ViewSpec } from "./types";

export const MARK_KINDS = ["thread", "trend", "event", "forecast", "annotation", "highlight", "trend_break"];

/** Which mark kinds each panel type draws (MARK_ON in the backend validator). */
export const MARK_ON: Record<string, string[]> = {
  thread: ["flow", "flowmap"],
  trend: ["line"],
  event: ["line", "compare"],
  forecast: ["line"],
  annotation: ["line", "bar", "waterfall", "compare"],
  highlight: ["bar", "flow", "table", "line", "choropleth", "flowmap", "treemap", "butterfly", "bump", "scatter", "compare"],
  // On a heatmap only month_year and product_year draw it (the validator checks the kind).
  trend_break: ["line", "heatmap"],
};

export interface Storyline {
  /** The thread value, or "#<mark id>" for a mark on its own. */
  key: string;
  /** Number shown on the page: story_n, stable (a spec from before 1.6: the place in order of first n). */
  number: number;
  /** Its marks in order of n. */
  marks: Mark[];
  /** Agent of the first mark. */
  agent: string;
  /** "kept" when every mark is kept. */
  status: "proposed" | "kept";
}

export interface Storylines {
  list: Storyline[];
  /** Storyline number of each mark id. */
  numberOf: Record<string, number>;
  /** Storyline of each mark id. */
  of: Record<string, Storyline>;
}

export function storylineKey(m: Mark): string {
  return m.thread ? m.thread : `#${m.id}`;
}

/** Groups marks into storylines, each with its stable number, in order of number. */
export function storylines(marks: Mark[]): Storylines {
  const groups: Record<string, Mark[]> = {};
  const keys: string[] = [];
  marks
    .slice()
    .sort((a, b) => a.n - b.n)
    .forEach((m) => {
      const k = storylineKey(m);
      if (!groups[k]) {
        groups[k] = [];
        keys.push(k);
      }
      groups[k].push(m);
    });
  const list: Storyline[] = keys
    .map((k, i) => ({
      key: k,
      number: groups[k].find((m) => typeof m.story_n === "number")?.story_n ?? i + 1,
      marks: groups[k],
      agent: groups[k][0].agent,
      status: (groups[k].every((m) => m.status === "kept") ? "kept" : "proposed") as Storyline["status"],
    }))
    .sort((a, b) => a.number - b.number);
  const numberOf: Record<string, number> = {};
  const of: Record<string, Storyline> = {};
  list.forEach((s) => s.marks.forEach((m) => {
    numberOf[m.id] = s.number;
    of[m.id] = s;
  }));
  return { list, numberOf, of };
}

/** Storylines of the spec on screen. */
export function useStorylines(): Storylines {
  const { spec } = useViewStore();
  return React.useMemo(() => storylines(spec?.marks || []), [spec]);
}

/** Marks drawn on one panel, in order of n. */
export function marksOn(spec: ViewSpec | null, panelId: string): Mark[] {
  return (spec?.marks || []).filter((m) => m.target === panelId).sort((a, b) => a.n - b.n);
}

/**
 * Within one panel, the first mark of each storyline carries the number circle,
 * so a storyline of four events shows one "2", not four.
 */
export function numberCarriers(marks: Mark[], sl: Storylines): Set<string> {
  const seen: Record<number, boolean> = {};
  const out = new Set<string>();
  marks.forEach((m) => {
    const n = sl.numberOf[m.id];
    if (n !== undefined && !seen[n]) {
      seen[n] = true;
      out.add(m.id);
    }
  });
  return out;
}

/** Everything a panel needs to draw a mark: the mark, its pen and number. */
export interface DrawnMark {
  mark: Mark;
  color: string;
  ink: string;
  agentTitle: string;
  number: number;
  /** Draw the number circle on this one. */
  showNumber: boolean;
}

export function drawnMarks(marks: Mark[], sl: Storylines, agents: AgentInfo[]): DrawnMark[] {
  const carriers = numberCarriers(marks, sl);
  return marks.map((m) => {
    const pen = penFor(m.agent, agents);
    return {
      mark: m,
      color: pen.color,
      ink: pen.ink,
      agentTitle: pen.title,
      number: sl.numberOf[m.id] ?? m.n,
      showNumber: carriers.has(m.id),
    };
  });
}

/** Is this kind drawn by this panel type? Unknown kinds or wrong targets get a grey card. */
export function supportsMark(panelType: string, kind: string): boolean {
  return (MARK_ON[kind] || []).indexOf(panelType) >= 0;
}

/** Year (or year + month) as a number on a time axis: 2018-06 -> 2018.42. */
export function periodToX(p: string | number | undefined): number | undefined {
  if (p === undefined || p === null) return undefined;
  const m = /^(\d{4})(?:-(\d{2}))?$/.exec(String(p));
  if (!m) {
    const n = Number(p);
    return Number.isFinite(n) ? n : undefined;
  }
  const y = parseInt(m[1], 10);
  return m[2] ? y + (parseInt(m[2], 10) - 1) / 12 : y;
}

// ---------------------------------------------------------------- notes

/** A mark's note with its facts resolved ("self" reads the mark's own query). */
export const MarkText: React.FC<{ mark: Mark }> = ({ mark }) => {
  // The mark's own query may follow the page ($period, $previous, $product): its chart's context (1.5).
  const ctx = useMarkContext(mark);
  const self = useQuery(mark.query ? substituteQuery(mark.query, ctx) : null);
  const facts = useFacts(mark.facts, mark.query ? { self } : {});
  return <FactText template={mark.note} facts={facts} />;
};

/** The API calls behind a mark: its provenance sources, its query and its inline facts. */
export function markSources(mark: Mark): string[] {
  const urls: string[] = [];
  (mark.provenance?.sources || []).forEach((s) => urls.push(s.url));
  if (mark.query) urls.push(queryUrl(mark.query));
  Object.keys(mark.facts || {}).forEach((k) => {
    const f: any = (mark.facts as any)[k];
    if (f && f.endpoint) urls.push(queryUrl(f));
  });
  return urls.filter((u, i) => urls.indexOf(u) === i);
}

/** "bought from China, sold to France": what a thread shows. Trade statistics do not link what is bought to
 * what is sold again, so a thread is never "through" Luxembourg. */
export function threadWords(mark: Mark): string {
  const p = mark.params || {};
  const label = typeof p.label === "string" ? p.label.trim() : "";
  if (/\bbought\b|\bsold\b/i.test(label)) return label;
  const pair = p.in_code && p.out_code ? `bought from ${countryName(String(p.in_code))}, sold to ${countryName(String(p.out_code))}` : "";
  return [label, pair].filter(Boolean).join(": ") || "what was bought and sold";
}

/** What an annotation is about, in words: its label, a month or year, a partner or the goods; never a bare code. */
export function noteSubject(mark: Mark): string {
  const p = mark.params || {};
  const label = typeof p.label === "string" ? p.label.trim() : "";
  const x = String(p.x ?? "");
  const lower = (s: string) => (/^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);
  if (/^\d{4}(-\d{2})?$/.test(x)) return label ? `${formatPeriod(x)}, ${lower(label)}` : formatPeriod(x);
  if (/^[A-Z]{2}$/.test(x)) return countryName(x);
  if (label) return lower(label);
  if (CHAPTER_SHORT[x]) return lower(CHAPTER_SHORT[x]);
  return "one bar";
}

/** What the mark drew, in a few words: "Drawn: a thread on the crossroads: bought from China, sold to France". */
export function describeMark(mark: Mark): string {
  const p = mark.params || {};
  switch (mark.kind) {
    case "thread":
      return `Drawn: a thread on the crossroads: ${threadWords(mark)}`;
    case "event":
      return `Drawn: ${p.label || "an event"} on the timeline`;
    case "trend":
      return `Drawn: ${p.label || "a trend line"}`;
    case "forecast":
      return `Drawn: ${p.label || "a forecast band"}`;
    case "annotation":
      return `Drawn: a note on ${noteSubject(mark)}`;
    case "highlight":
      return `Drawn: ${p.label || "a highlight"}`;
    case "trend_break":
      return `Drawn: a break in the trend at ${formatPeriod(String(p.at || ""))}${p.label ? `, ${p.label}` : ""}`;
    default:
      return `Drawn: ${mark.kind}`;
  }
}

/**
 * One mark's note: number, agent, text with facts and the calls behind it.
 * `compact` is the one-line version under a panel.
 */
export const MarkNote: React.FC<{ mark: Mark; number?: number; compact?: boolean; showSources?: boolean; continued?: boolean }> = ({
  mark,
  number,
  compact,
  showSources,
  continued,
}) => {
  const { agents } = useViewStore();
  const ctx = useMarkContext(mark);
  const pen = penFor(mark.agent, agents);
  const known = MARK_KINDS.indexOf(String(mark.kind)) >= 0;
  // A mark about another period than the page shows is dimmed and says which.
  const off = markOffContext(mark, ctx);
  if (compact) {
    return (
      <p style={{ margin: 0, display: "flex", gap: 10, alignItems: "baseline", fontSize: 14, lineHeight: 1.5, color: TOKENS.ink, maxWidth: "62em", opacity: off ? 0.62 : 1 }}>
        {continued ? (
          <span aria-hidden="true" style={{ width: 20, flex: "none" }} />
        ) : (
          <NumberCircle n={number ?? mark.n} color={pen.color} style={{ alignSelf: "flex-start", marginTop: 1 }} />
        )}
        <span>
          {mark.agent === CAUSE_FINDER && !continued && <AiHypothesis style={{ marginRight: 8, verticalAlign: 2 }} />}
          {mark.params?.label && (continued || mark.kind === "event" || mark.kind === "trend_break") && (
            <span style={{ fontFamily: FONT_UI, fontSize: 13.5, fontWeight: 700, color: pen.ink }}>{String(mark.params.label)}. </span>
          )}
          <span style={{ fontFamily: FONT_AGENT, fontSize: 16 }}>
            <MarkText mark={mark} />
          </span>{" "}
          {!continued && (
            <span style={{ color: pen.ink, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>
              {pen.title}
              {mark.status === "kept" ? ", kept" : ""}
              {off ? `, ${aboutTag(markPeriod(mark))}` : ""}
            </span>
          )}
        </span>
      </p>
    );
  }
  return (
    <article style={{ display: "flex", flexDirection: "column", gap: 8, paddingLeft: 14, borderLeft: `3px solid ${pen.color}` }}>
      <span style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: FONT_UI, fontSize: 13, fontWeight: 700, color: pen.ink }}>
        <NumberCircle n={number ?? mark.n} color={pen.color} />
        {pen.title}
        {mark.agent === CAUSE_FINDER && <AiHypothesis />}
      </span>
      {!known && <UnsupportedCard what={`mark kind "${mark.kind}"`} />}
      <p style={{ margin: 0, fontFamily: FONT_AGENT, fontSize: 17, lineHeight: 1.5, color: TOKENS.ink }}>
        <MarkText mark={mark} />
      </p>
      <span style={{ fontFamily: FONT_UI, fontSize: 13, color: pen.ink, fontWeight: 600 }}>{describeMark(mark)}</span>
      {showSources && <SourceList urls={markSources(mark)} />}
    </article>
  );
};

/** The notes of the marks on a panel: one number per storyline, and every note of it under that number
 * (four events are four notes, not the first one alone). */
export const PanelNotes: React.FC<{ drawn: DrawnMark[] }> = ({ drawn }) => {
  const carriers = drawn.filter((d) => d.showNumber);
  if (!carriers.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {carriers.map((c) => {
        const rest = drawn.filter((d) => !d.showNumber && d.number === c.number && d.mark.id !== c.mark.id);
        return (
          <React.Fragment key={c.mark.id}>
            <MarkNote mark={c.mark} number={c.number} compact />
            {rest.map((d) => (
              <MarkNote key={d.mark.id} mark={d.mark} number={d.number} compact continued />
            ))}
          </React.Fragment>
        );
      })}
    </div>
  );
};
