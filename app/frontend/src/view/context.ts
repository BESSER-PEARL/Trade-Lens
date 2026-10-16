// The page context of contract 1.1: the period and the goods the page is
// about (spec.context, changed with set_context by the Year and Goods controls
// or by an agent). A query param may hold a placeholder that the page fills
// from it before fetching, with the same rules as fill_params() in
// app/backend/view/validate.py:
//
//   $period    -> context.period            (period, previous, start, end, base)
//   $previous  -> the same period a year earlier (2025-03 -> 2024-03)
//   $product   -> context.product           (product, parent)
//   $start     -> context.start, the From year of a range (start); the period
//                 when there is none, when it is after the period, or for a month (1.4)
//
// Every panel substitutes before it calls useQueries: substituteQueries(panel.queries, ctx).

import type { Facts, Mark, Params, Query, ViewSpec } from "./types";

export interface PageContext {
  /** A year (2025) or a month (2025-03). */
  period: string;
  /** "TOTAL" (all goods) or an HS code. */
  product: string;
  /** An emphasis only; BOTH when absent. */
  flow?: "IMPORT" | "EXPORT" | "BOTH";
  /** 1.4: the From year of a range the page's totals sum over, up to period (the To year). */
  start?: string;
  /** 1.4: who the page is drawn for (a persona profile id). Never changes a figure. */
  persona?: string;
}

export const DEFAULT_CONTEXT: PageContext = { period: "2025", product: "TOTAL" };

/** The context of a spec, with the defaults where it says nothing (context_of in the backend). */
export function contextOf(spec: ViewSpec | null | undefined): PageContext {
  const c = ((spec as any)?.context || {}) as Partial<PageContext>;
  return {
    ...DEFAULT_CONTEXT,
    ...(typeof c.period === "string" && c.period ? { period: c.period } : {}),
    ...(typeof c.product === "string" && c.product ? { product: c.product } : {}),
    ...(c.flow ? { flow: c.flow } : {}),
    ...(typeof c.start === "string" && c.start ? { start: c.start } : {}),
    ...(typeof c.persona === "string" && c.persona ? { persona: c.persona } : {}),
  };
}

// ------------------------------------------------------------------ a panel's own context (contract 1.5)

/** A panel's own year, range or goods (panel.context, set with set_panel_context): it wins over the page's. */
export interface PanelOwnContext {
  period?: string;
  start?: string;
  product?: string;
}

/** The panel's own context, cleaned; undefined when it follows the page. */
export function ownContextOf(panel: { context?: unknown } | null | undefined): PanelOwnContext | undefined {
  const c = (panel as any)?.context;
  if (!c || typeof c !== "object") return undefined;
  const out: PanelOwnContext = {};
  (["period", "start", "product"] as const).forEach((k) => {
    if (typeof c[k] === "string" && c[k]) out[k] = c[k];
  });
  return Object.keys(out).length ? out : undefined;
}

/**
 * The context a panel's queries, facts and marks are filled from: the page's, with the panel's own on
 * top (panel_context in validate.py). A period of its own without a start of its own is a single year.
 */
export function withPanelContext(page: PageContext, panel: { context?: unknown } | null | undefined): PageContext {
  const own = ownContextOf(panel);
  if (!own) return page;
  const base: PageContext = { ...page };
  if (own.period && !own.start) delete base.start;
  return { ...base, ...own };
}

/** The context of one panel of a spec. */
export function panelContextOf(spec: ViewSpec | null | undefined, panel: { context?: unknown } | null | undefined): PageContext {
  return withPanelContext(contextOf(spec), panel);
}

/** The placeholders a panel follows: those in its queries and inline facts. */
export function placeholdersOf(panel: { queries?: Array<{ params?: Params }>; facts?: Facts } | null | undefined): Set<string> {
  const found = new Set<string>();
  const scan = (params?: Params) =>
    Object.keys(params || {}).forEach((k) => {
      const v = String((params as any)[k]);
      if (/^\$(period|previous|start|product)$/.test(v)) found.add(v);
    });
  (panel?.queries || []).forEach((q) => scan(q.params));
  Object.keys(panel?.facts || {}).forEach((k) => {
    const f: any = (panel!.facts as any)[k];
    if (f && f.endpoint) scan(f.params);
  });
  return found;
}

/** What $start stands for: context.start when it is a year no later than a yearly period, else the period (range_start in validate.py). */
export function rangeStart(ctx: PageContext): string {
  const p = String(ctx.period);
  const s = ctx.start;
  return typeof s === "string" && /^\d{4}$/.test(s) && /^\d{4}$/.test(p) && s <= p ? s : p;
}

/** The same period a year earlier: 2025 -> 2024, 2025-03 -> 2024-03. */
export function previousPeriod(period: string): string {
  const m = /^(\d{4})(.*)$/.exec(String(period));
  if (!m) return String(period);
  return `${String(parseInt(m[1], 10) - 1).padStart(4, "0")}${m[2]}`;
}

/** Params with $period, $previous and $product filled from the context. */
export function substituteContext<P extends Record<string, any> | undefined>(params: P, ctx: PageContext): P {
  if (!params) return params;
  const values: Record<string, string> = {
    $period: ctx.period,
    $previous: previousPeriod(ctx.period),
    $start: rangeStart(ctx),
    $product: ctx.product,
  };
  let changed = false;
  const out: Record<string, any> = {};
  Object.keys(params).forEach((k) => {
    const v = (params as any)[k];
    if (typeof v === "string" && Object.prototype.hasOwnProperty.call(values, v)) {
      out[k] = values[v];
      changed = true;
    } else out[k] = v;
  });
  return (changed ? out : params) as P;
}

/** A query with its placeholders filled (the same object when it has none, so caches and memos hold). */
export function substituteQuery<Q extends { endpoint: string; params?: Params } | null | undefined>(q: Q, ctx: PageContext): Q {
  if (!q) return q;
  const params = substituteContext(q.params, ctx);
  return (params === q.params ? q : { ...q, params }) as Q;
}

/** Every query of a panel with its placeholders filled. */
export function substituteQueries(qs: Query[], ctx: PageContext): Query[] {
  return qs.map((q) => substituteQuery(q, ctx));
}

/** Inline facts ({endpoint, params, path}) with their placeholders filled. */
export function substituteFacts(facts: Facts | undefined, ctx: PageContext): Facts | undefined {
  if (!facts) return facts;
  const out: Facts = {};
  Object.keys(facts).forEach((k) => {
    const f: any = facts[k];
    out[k] = f && f.endpoint && f.params ? { ...f, params: substituteContext(f.params, ctx) } : f;
  });
  return out;
}

/** True when any param of these queries follows the context. */
export function usesPlaceholders(qs: Array<{ params?: Params } | null | undefined>): boolean {
  return qs.some((q) => !!q && Object.keys(q.params || {}).some((k) => /^\$(period|previous|start|product)$/.test(String((q.params as any)[k]))));
}

/** A relative URL (a provenance source) with its placeholders filled, written plain or URL-encoded. */
export function fillUrl(url: string, ctx: PageContext): string {
  return url
    .replace(/(=)(?:\$|%24)previous(?=&|$)/g, `$1${previousPeriod(ctx.period)}`)
    .replace(/(=)(?:\$|%24)period(?=&|$)/g, `$1${ctx.period}`)
    .replace(/(=)(?:\$|%24)start(?=&|$)/g, `$1${rangeStart(ctx)}`)
    .replace(/(=)(?:\$|%24)product(?=&|$)/g, `$1${encodeURIComponent(ctx.product)}`);
}

/** Does a mark's (or text's) period match the page's? A year matches its months, and a month its year. */
export function periodMatches(period: string | undefined | null, ctxPeriod: string): boolean {
  if (!period) return true;
  const a = String(period);
  const b = String(ctxPeriod);
  if (a === b) return true;
  if (a.length === 4 && b.startsWith(a + "-")) return true;
  if (b.length === 4 && a.startsWith(b + "-")) return true;
  return false;
}

/** The period a mark is about (contract 1.1 mark.period), if it says. */
export function markPeriod(mark: Mark | undefined | null): string | undefined {
  const p = (mark as any)?.period;
  return typeof p === "string" || typeof p === "number" ? String(p) : undefined;
}

/** True when the mark is about another period than the page shows: draw it dimmed, with an "about ..." tag. */
export function markOffContext(mark: Mark | undefined | null, ctx: PageContext): boolean {
  // A forecast is about the years after the data by nature: it is never "about another year".
  if (mark && mark.kind === "forecast") return false;
  return !periodMatches(markPeriod(mark), ctx.period);
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "about 2025", "about March 2020": the tag on a mark drawn about another period. */
export function aboutTag(period: string | undefined): string {
  if (!period) return "";
  const m = /^(\d{4})-(\d{2})$/.exec(period);
  return `about ${m ? `${MONTHS[parseInt(m[2], 10) - 1]} ${m[1]}` : period}`;
}

/** Opacity of a mark drawn about another period. */
export const OFF_CONTEXT_OPACITY = 0.32;

/** The literal period a panel's own queries (or inline facts) are about, when it does not follow the page. */
export function literalPeriodOf(queries: Array<{ params?: Params }>, facts?: Facts): string | undefined {
  const all: Array<{ params?: Params }> = [...queries];
  Object.keys(facts || {}).forEach((k) => {
    const f: any = (facts as any)[k];
    if (f && f.endpoint) all.push(f);
  });
  for (const q of all) {
    const p = q.params?.period;
    if (p !== undefined && !/^\$/.test(String(p))) return String(p);
  }
  for (const q of all) {
    const e = q.params?.end;
    if (e !== undefined && !/^\$/.test(String(e))) return String(e);
  }
  return undefined;
}
