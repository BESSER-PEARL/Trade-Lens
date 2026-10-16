// What every new chart panel does before it draws: read the page context, fill the
// $period / $previous / $product placeholders of its queries and facts, fetch them, resolve
// its facts, and turn the marks on it into pen marks (dimmed when they are about another
// period than the page shows).

import { useMemo } from "react";
import { markOffContext, OFF_CONTEXT_OPACITY, substituteFacts, substituteQueries } from "../context";
import type { PageContext } from "../context";
import { keyed, useFacts } from "../facts";
import type { ResolvedFacts } from "../facts";
import type { DrawnMark } from "../marks";
import { firstProblem, useQueries } from "../useQuery";
import type { QueryState } from "../useQuery";
import { usePageContext } from "../ViewStore";
import type { Panel, Query } from "../types";
import type { PenMark } from "./palette";

export { usePageContext };

export interface PanelData {
  ctx: PageContext;
  /** The panel's queries with their placeholders filled. */
  queries: Query[];
  states: QueryState[];
  /** States by query key. */
  byKey: Record<string, QueryState>;
  facts: ResolvedFacts;
}

/** Fetches a panel's queries and resolves its facts, after filling the placeholders. */
export function usePanelData(panel: Panel): PanelData {
  const ctx = usePageContext();
  const queries = useMemo(() => substituteQueries(panel.queries || [], ctx), [panel.queries, ctx]);
  const facts = useMemo(() => substituteFacts(panel.facts, ctx), [panel.facts, ctx]);
  const states = useQueries(queries);
  const byKey = keyed(queries, states);
  const resolved = useFacts(facts, byKey);
  return { ctx, queries, states, byKey, facts: resolved };
}

/** The first problem among the queries with these keys (the ones the chart draws from). */
export function problemOf(data: PanelData, keys: string[]) {
  return firstProblem(keys.map((k) => data.byKey[k]).filter((s): s is QueryState => !!s));
}

/** The query with this key, after substitution. */
export function queryOf(data: PanelData, key: string): Query | undefined {
  return data.queries.find((q) => q.key === key);
}

/** Pen marks of the given kinds, with the codes each points at. */
export function penMarks(drawn: DrawnMark[], ctx: PageContext, kinds: string[] = ["highlight"]): PenMark[] {
  return drawn
    .filter((d) => kinds.indexOf(String(d.mark.kind)) >= 0)
    .map((d) => {
      const p = d.mark.params || {};
      const codes: string[] = Array.isArray(p.codes) ? p.codes.map(String) : p.x !== undefined ? [String(p.x)] : [];
      return {
        id: d.mark.id,
        codes,
        color: d.color,
        ink: d.ink,
        n: d.number,
        showNumber: d.showNumber,
        label: typeof p.label === "string" ? p.label : undefined,
        opacity: markOffContext(d.mark, ctx) ? OFF_CONTEXT_OPACITY : 1,
      };
    });
}

/** Marks that point at a code (exact, or a parent code when `prefix` is set: 85 covers 8517). */
export function marksFor(marks: PenMark[], code: string, prefix = false): PenMark[] {
  return marks.filter((m) => m.codes.some((c) => c === code || (prefix && /^\d+$/.test(c) && /^\d+$/.test(code) && c.startsWith(code))));
}

/** The first spot (in drawing order) of each mark, so its number circle is drawn once. */
export function firstSpots(marks: PenMark[], codesInOrder: string[], prefix = false): Record<string, string> {
  const out: Record<string, string> = {};
  codesInOrder.forEach((code) => {
    marksFor(marks, code, prefix).forEach((m) => {
      if (out[m.id] === undefined) out[m.id] = code;
    });
  });
  return out;
}

let regionNames: { of: (code: string) => string | undefined } | null | undefined;

/** A partner's display name: the API's label, else the English name of the ISO code. */
export function partnerName(code: string, name?: string | null): string {
  const n = String(name || "").split(" (")[0].trim();
  // A label that is only the code, title-cased ("De"), is no label.
  if (n && n.toUpperCase() !== String(code).toUpperCase()) return n;
  if (regionNames === undefined) {
    try {
      const DN = (Intl as any).DisplayNames;
      regionNames = DN ? new DN(["en"], { type: "region" }) : null;
    } catch {
      regionNames = null;
    }
  }
  const iso = code === "EL" ? "GR" : code === "UK" ? "GB" : code === "XS" ? "RS" : code;
  try {
    const got = regionNames && /^[A-Z]{2}$/.test(iso) ? regionNames.of(iso) : undefined;
    if (got && got !== iso) return got;
  } catch {
    // Unknown region code: fall through.
  }
  return n || code;
}

/** True for a two-letter partner code that is a country, not an aggregate or OTHER. */
export function isCountryCode(code: string): boolean {
  return /^[A-Z]{2}$/.test(String(code)) && code !== "EU" && code !== "EA";
}
