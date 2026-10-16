// "How this was made": the card every chart, text and note can open, in plain words. Five rows (the
// method, the data, what was calculated and where it shows, who drew it, what the assistant did and did
// not do) and a folded "For statisticians" level with the exact queries and the technical method.
//
// The message: the assistant only chooses among fixed statistical methods and writes the words; the
// methods compute every figure from the official data, and the page refuses figures typed in by the
// assistant. The texts come from the method catalogue (methods.ts); nothing here is a figure.
//
// The card opens inline, like "Show as table": one card per panel, toggled from the panel's header
// (MadeToggle in PanelControls) and drawn by Panel under the chart. The open state is a tiny store keyed
// by panel id, so a header the page draws itself (the crossroads, the trend) opens the card the panel
// draws.

import React, { useSyncExternalStore } from "react";
import { DASHBOARD_AGENT } from "./branding";
import { apiHref, queryUrl } from "./api";
import { CHAPTER_SHORT } from "./charts/format";
import { useShowCodes } from "./codes";
import { calculatedOf, dataLine, markCalculated, markQueries, methodOf, type MethodText } from "./methods";
import { usePanelPrompt } from "./panelPrompts";
import { asShown, useAdjustedShown } from "./seasonal";
import { aReader, personaOfPanel } from "./personas";
import { isAgentOwner, penFor } from "./tokens";
import { useUrls } from "./useQuery";
import { substituteContext, useMarkContext, usePageContext, useSpecContext, useViewStore, withPanelContext } from "./ViewStore";
import type { Mark, Panel, Params, Query } from "./types";

// ------------------------------------------------------------------ open state

let openKeys = new Set<string>();
let listeners: Array<() => void> = [];

function subscribe(l: () => void) {
  listeners.push(l);
  return () => {
    listeners = listeners.filter((x) => x !== l);
  };
}

/** Whether the card of `key` (a panel id) is open, and a toggle. */
export function useMadeOpen(key: string): [boolean, (open?: boolean) => void] {
  const snap = useSyncExternalStore(subscribe, () => openKeys);
  const isOpen = snap.has(key);
  const set = React.useCallback(
    (open?: boolean) => {
      const next = new Set(openKeys);
      const want = open === undefined ? !next.has(key) : open;
      if (want) next.add(key);
      else next.delete(key);
      openKeys = next;
      listeners.forEach((l) => l());
    },
    [key],
  );
  return [isOpen, set];
}

export const madeId = (key: string) => `made-${key}`;

// Exporting to PDF opens every chart's card, the statisticians' fold included, for the printout, and puts
// each back as the reader had it afterwards.
let printing = false;
let openBeforePrint: Set<string> | null = null;

function usePrinting(): boolean {
  return useSyncExternalStore(subscribe, () => printing);
}

export function openMadeForPrint(keys: string[]) {
  if (!openBeforePrint) openBeforePrint = new Set(openKeys);
  openKeys = new Set([...Array.from(openKeys), ...keys]);
  printing = true;
  listeners.forEach((l) => l());
}

export function restoreMadeAfterPrint() {
  if (openBeforePrint) openKeys = openBeforePrint;
  openBeforePrint = null;
  printing = false;
  listeners.forEach((l) => l());
}

/** The quiet header button that opens and closes the card, next to "Keep this chart". */
export const MadeToggle: React.FC<{ id: string; label?: string; className?: string }> = ({ id, label = "How this was made", className = "cx-pctl-btn" }) => {
  const [open, set] = useMadeOpen(id);
  return (
    <button
      type="button"
      className={`${className}${open ? " cx-pctl-btn--on" : ""} cx-made-toggle`}
      aria-expanded={open}
      aria-controls={madeId(id)}
      title="The method, the data and who drew it; the exact query for statisticians"
      onClick={() => set()}
    >
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false" style={{ flex: "none" }}>
        <circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M8 7.2v4.1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="8" cy="4.9" r="0.95" fill="currentColor" />
      </svg>
      <span className="cx-made-toggle-label">{label}</span>
    </button>
  );
};

// ------------------------------------------------------------------ names

const LEVEL_OF_LENGTH: Record<number, number> = { 2: 1, 4: 2, 6: 3, 8: 4 };

function unshout(s: string): string {
  const t = String(s || "").split(";")[0].trim();
  if (t && t === t.toUpperCase()) return t.charAt(0) + t.slice(1).toLowerCase();
  return t.length > 60 ? t.slice(0, 58).replace(/[ ,]+\S*$/, "") + "…" : t;
}

/** Names of the product codes the queries name, from /product/search (chapters from the short list). */
function useProductNames(codes: string[]): (code: string) => string {
  const showCodes = useShowCodes();
  const need = codes.filter((c) => /^\d{2,8}$/.test(c) && !(c.length === 2 && CHAPTER_SHORT[c]));
  const urls = need.map((c) => `/product/search/?code=${c}&level=${LEVEL_OF_LENGTH[c.length] || 1}`);
  const states = useUrls(urls);
  return (code: string) => {
    let name = code.length === 2 ? CHAPTER_SHORT[code] : undefined;
    const i = need.indexOf(code);
    if (!name && i >= 0) {
      const hit = Array.isArray(states[i]?.data) ? (states[i].data as any[]).find((r) => r && String(r.code) === code) : undefined;
      if (hit?.label) name = unshout(hit.label);
    }
    const kind = code.length === 2 ? "HS" : code.length === 8 ? "CN8" : "HS";
    if (!name) return `${kind} ${code}`;
    return showCodes ? `${name.charAt(0).toLowerCase() + name.slice(1)} (${kind} ${code})` : name.charAt(0).toLowerCase() + name.slice(1);
  };
}

function productCodes(queries: Array<{ params?: Params }>): string[] {
  const out: string[] = [];
  queries.forEach((q) =>
    ["product", "parent"].forEach((k) => {
      const v = q.params?.[k];
      if (v !== undefined) String(v).split(",").forEach((c) => out.indexOf(c) < 0 && c !== "TOTAL" && out.push(c));
    }),
  );
  return out;
}

// ------------------------------------------------------------------ methods of a set of queries

/** The distinct methods behind some queries, each with the queries that used it (params merged for the words). */
function methodsOf(queries: Array<{ endpoint: string; params?: Params }>): Array<{ method: MethodText; queries: Array<{ endpoint: string; params?: Params }> }> {
  const groups: Array<{ name: string; queries: Array<{ endpoint: string; params?: Params }> }> = [];
  queries.forEach((q) => {
    const name = methodOf(q).name;
    const g = groups.find((x) => x.name === name);
    if (g) g.queries.push(q);
    else groups.push({ name, queries: [q] });
  });
  return groups.map((g) => {
    // One sentence for the whole group: params that differ (flow IMPORT and EXPORT) are joined.
    const merged: Params = {};
    g.queries.forEach((q) =>
      Object.keys(q.params || {}).forEach((k) => {
        const v = String((q.params as any)[k]);
        if (merged[k] === undefined) merged[k] = (q.params as any)[k];
        else if (String(merged[k]).split(",").indexOf(v) < 0) merged[k] = `${merged[k]},${v}`;
      }),
    );
    // Periods stay single (the first query's) so "2025" never reads "2025,2024".
    ["period", "previous", "start", "end", "base"].forEach((k) => {
      const first = g.queries.find((q) => q.params?.[k] !== undefined);
      if (first) merged[k] = (first.params as any)[k];
    });
    return { method: methodOf({ endpoint: g.queries[0].endpoint, params: merged }), queries: g.queries };
  });
}

function dedupe<T extends { endpoint: string; params?: Params }>(qs: T[]): T[] {
  const seen = new Set<string>();
  return qs.filter((q) => {
    const u = queryUrl(q);
    if (seen.has(u)) return false;
    seen.add(u);
    return true;
  });
}

// ------------------------------------------------------------------ the card

export interface DrawnBy {
  /** An agent's registry name, "user", or undefined for the page itself. */
  agent?: string;
  question?: string;
  at?: string;
  /** A panel the page added for the reader picked ("a journalist"): agent "user", but not a click of the person's. */
  forReader?: string;
}

function clock(iso?: string): string | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return undefined;
  const today = new Date();
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? `at ${time}` : `on ${d.toLocaleDateString("en-GB", { day: "numeric", month: "long" })} at ${time}`;
}

const ParamsLine: React.FC<{ params?: Params }> = ({ params }) => {
  const keys = Object.keys(params || {});
  if (!keys.length) return <span className="cx-made-params">no parameters</span>;
  return (
    <span className="cx-made-params">
      {keys.map((k, i) => (
        <React.Fragment key={k}>
          {i > 0 && ", "}
          <span className="cx-made-pk">{k}</span> {String((params as any)[k])}
        </React.Fragment>
      ))}
    </span>
  );
};

export interface MadeCardProps {
  /** The key of the open state and of the element id (a panel id). */
  id: string;
  title?: string;
  /** The queries behind the figures, with the page context already filled in. */
  queries: Array<{ endpoint: string; params?: Params; label?: string }>;
  calculated: string;
  drawnBy: DrawnBy;
  /** What the assistant did, when an agent drew it. */
  assistantDid?: string;
  /** Extra lines in the statisticians' fold (page version, run). */
  footnote?: string;
  onClose?: () => void;
  /** Label of the method row when there are several ("Methods"). */
  className?: string;
}

export const MadeCard: React.FC<MadeCardProps> = ({ id, title = "How this was made", queries, calculated, drawnBy, assistantDid, footnote, onClose, className }) => {
  const { agents } = useViewStore();
  const qs = dedupe(queries);
  const names = useProductNames(productCodes(qs));
  const groups = methodsOf(qs);
  const data = dataLine(qs, names);
  const byAgent = isAgentOwner(drawnBy.agent);
  const pen = penFor(drawnBy.agent, agents);
  const when = clock(drawnBy.at);
  const allNone = groups.length > 0 && groups.every((g) => g.method.noCalculation);
  const forecast = qs.some((q) => q.endpoint === "/insights/forecast");
  const headId = `${madeId(id)}-head`;
  const printed = usePrinting();

  return (
    <section id={madeId(id)} className={`cx-made${className ? " " + className : ""}`} aria-labelledby={headId}>
      <div className="cx-made-title">
        <h4 id={headId}>{title}</h4>
        {onClose && (
          <button type="button" className="cx-made-close" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      <dl className="cx-made-rows">
        <dt>{groups.length > 1 ? "Methods" : "Method"}</dt>
        <dd>
          {!allNone && groups.length > 0 && <span className="cx-badge cx-badge--calc">Calculated</span>}
          {groups.length === 0 && <span>No calculation: the words alone, with no figure.</span>}
          {groups.map((g, i) => (
            <span key={g.method.key + i} className="cx-made-method">
              <b>{g.method.name}.</b> {g.method.plain}
            </span>
          ))}
        </dd>
        <dt>Data</dt>
        <dd>{data.parts.join("; ")}.</dd>
        <dt>Calculated</dt>
        <dd>{calculated}</dd>
        <dt>Drawn by</dt>
        <dd>
          {byAgent ? (
            <>
              <span className="cx-made-sq" style={{ background: pen.color }} aria-hidden="true" />
              <b style={{ color: pen.ink }}>The {pen.title.toLowerCase()}</b>
              {drawnBy.question ? <>, for the question “{drawnBy.question}”</> : ""}
              {when ? `, ${when}` : ""}.
            </>
          ) : drawnBy.agent === "user" && drawnBy.forReader ? (
            <>
              <b>The page</b>, for this reader ({drawnBy.forReader}). No AI involved.
            </>
          ) : drawnBy.agent === "user" ? (
            <>
              <b>You</b>, with a click{when ? `, ${when}` : ""}. No AI involved.
            </>
          ) : (
            <>
              <span className="cx-badge cx-badge--none">No AI involved</span>
              The page draws this at start; it follows the Year and Goods controls.
            </>
          )}
        </dd>
        {byAgent && (
          <>
            <dt>The {DASHBOARD_AGENT}</dt>
            <dd>
              <span className="cx-badge cx-badge--ai" style={{ color: pen.ink, background: `${pen.color}1A` }}>
                Explained by the {DASHBOARD_AGENT}
              </span>
              {assistantDid || "Chose this method and this chart, and wrote the title and the words."} It did not calculate any figure: every figure here is computed by the
              {groups.length > 1 ? " methods" : " method"} above{forecast ? ", and it did not choose the forecasting method (a test on the past did)" : ""}. The page refuses any figure the
              {DASHBOARD_AGENT} types in.
            </dd>
          </>
        )}
      </dl>
      {qs.length > 0 && (
        <details className="cx-made-detail" open={printed || undefined}>
          <summary>For statisticians</summary>
          <div className="cx-made-detail-body">
            {groups.map((g, i) => (
              <div key={g.method.key + i} className="cx-made-group">
                <p className="cx-made-tech">
                  <b>{g.method.name}.</b> {g.method.detail}
                </p>
                <ul className="cx-made-queries">
                  {g.queries.map((q) => {
                    const url = queryUrl(q);
                    return (
                      <li key={url} className="cx-made-query">
                        <code>GET {q.endpoint}</code> <ParamsLine params={q.params} />{" "}
                        <a href={apiHref(url)} target="_blank" rel="noreferrer">
                          the answer
                        </a>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
            <p className="cx-made-tech cx-made-guard">
              Before the page saved this, the backend ran every query above and checked every figure placeholder against its answer; a figure typed into the text is
              refused (app/backend/view/validate.py).{footnote ? ` ${footnote}` : ""}
            </p>
          </div>
        </details>
      )}
    </section>
  );
};

// ------------------------------------------------------------------ wrappers

function filled<Q extends { endpoint: string; params?: Params }>(qs: Q[], ctx: ReturnType<typeof usePageContext>): Q[] {
  return qs.map((q) => ({ ...q, params: substituteContext(q.params || {}, ctx) }));
}

/** The queries of a panel and of its inline facts. */
function panelQueries(panel: Panel): Query[] {
  const out: Query[] = [...(panel.queries || [])];
  Object.keys(panel.facts || {}).forEach((k) => {
    const f: any = (panel.facts as any)[k];
    if (f && f.endpoint) out.push({ endpoint: f.endpoint, params: f.params || {} });
  });
  return out;
}

/** The card of one panel, in plain words, with no toggle of its own: used wherever something else
 * already gates its visibility (the panel's own toggle in PanelMadeCard, or "More about this chart"
 * in PromptGroup). */
export const MadeCardInline: React.FC<{ panel: Panel; onClose?: () => void }> = ({ panel: given, onClose }) => {
  const ctx = usePageContext();
  const { spec } = useViewStore();
  const prompt = usePanelPrompt(given.id);
  // The line on screen: seasonally adjusted when the reader switched it so (LinePanel), the method named accordingly.
  const adjusted = useAdjustedShown(given.id);
  const panel = React.useMemo(() => (adjusted && given.type === "line" ? { ...given, queries: asShown(given.queries || [], true) } : given), [adjusted, given]);
  const author = panel.provenance?.agent || panel.agent;
  const byAgent = isAgentOwner(author);
  const reader = personaOfPanel(panel);
  return (
    <MadeCard
      id={panel.id}
      queries={filled(panelQueries(panel), ctx)}
      calculated={calculatedOf(panel) + (spec?.marks.some((m) => m.target === panel.id) ? " The numbered notes on it read their figures from the same calculations." : "")}
      drawnBy={{
        agent: byAgent ? author : panel.agent === "user" ? "user" : undefined,
        question: prompt?.prompt,
        at: panel.provenance?.created_at,
        forReader: reader ? aReader(reader.label) : undefined,
      }}
      assistantDid={
        panel.type === "text"
          ? "Chose which figures to read and wrote the sentence around them, with a blank for each figure."
          : "Chose this method and this chart, and wrote the title."
      }
      footnote={spec ? `Page version ${spec.version}${panel.provenance?.run_id ? `, run ${panel.provenance.run_id}` : ""}.` : undefined}
      onClose={onClose}
    />
  );
};

/** The card of one panel, open while its toggle is. When the panel's question has an agent answer
 * (and so a "More about this chart" to hold this card instead), the toggle itself is hidden
 * (PanelControls) and this never opens. */
export const PanelMadeCard: React.FC<{ panel: Panel }> = ({ panel }) => {
  const [open, set] = useMadeOpen(panel.id);
  if (!open) return null;
  return <MadeCardInline panel={panel} onClose={() => set(false)} />;
};

/** One card for several panels that share a header (the three figures). */
export const PanelsMadeCard: React.FC<{ id: string; panels: Panel[]; title?: string; calculated: string }> = ({ id, panels, title, calculated }) => {
  const [open, set] = useMadeOpen(id);
  const page = useSpecContext();
  if (!open || !panels.length) return null;
  // each panel's queries with its own year and goods (contract 1.5)
  const queries = panels.reduce((acc: Query[], p) => acc.concat(filled(panelQueries(p), withPanelContext(page, p))), []);
  const author = panels.map((p) => p.provenance?.agent || p.agent).find((a) => isAgentOwner(a));
  return <MadeCard id={id} title={title} queries={queries} calculated={calculated} drawnBy={{ agent: author }} onClose={() => set(false)} />;
};

/** The card of a storyline's marks (the note in the Ask panel). */
export const MarksMadeCard: React.FC<{ id: string; marks: Mark[]; onClose?: () => void }> = ({ id, marks, onClose }) => {
  // the context of the chart the marks are on (contract 1.5)
  const ctx = useMarkContext(marks[0]);
  const { spec } = useViewStore();
  const first = marks[0];
  // The question: the one filed for a chart the same run drew (marks carry none themselves).
  const run = first?.provenance?.run_id;
  const sibling = run ? spec?.panels.find((p) => p.provenance?.run_id === run) : undefined;
  const prompt = usePanelPrompt(sibling?.id || "");
  if (!first) return null;
  // A highlight or a note with no query of its own reads the chart it points at: that chart's method.
  let own = marks.reduce((acc: Query[], m) => acc.concat(markQueries(m)), []);
  if (!own.length) {
    const target = spec?.panels.find((p) => p.id === first.target);
    if (target) own = panelQueries(target);
  }
  const queries = filled(own, ctx);
  const kinds = marks.map((m) => m.kind).filter((k, i, a) => a.indexOf(k) === i);
  const calculated = kinds.map((k) => markCalculated(marks.find((m) => m.kind === k)!)).join(" ");
  const did =
    first.kind === "highlight" || first.kind === "annotation"
      ? "Chose what to point at and wrote the note."
      : first.kind === "event"
        ? "Chose which events to place and wrote the note."
        : first.kind === "forecast"
          ? "Chose to forecast this series and wrote the note."
          : "Chose to run this method, what to draw and wrote the note.";
  return (
    <MadeCard
      id={id}
      queries={queries}
      calculated={calculated}
      drawnBy={{ agent: first.agent === "user" ? "user" : first.agent, question: prompt?.prompt, at: first.provenance?.created_at }}
      assistantDid={did}
      footnote={run ? `Run ${run}.` : undefined}
      onClose={onClose}
      className="cx-made--note"
    />
  );
};

