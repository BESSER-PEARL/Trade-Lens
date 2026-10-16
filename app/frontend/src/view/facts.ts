// {fact} placeholders: how figures get into text. A fact reads one of the
// panel's queries ({query, path}), makes its own call ({endpoint, params, path})
// or derives a value from other facts ({op, of}). No number ever comes from an
// LLM: every figure here is read from an /insights response.

import { queryUrl } from "./api";
import { fillUrl, substituteContext } from "./context";
import { QueryState, useUrls } from "./useQuery";
import { usePageContext } from "./ViewStore";
import type { ApiFact, Fact, FactFormat, Facts, OpFact, Query, QueryFact } from "./types";

/** "=$period", "=%24previous", ... in a relative URL. */
const PLACEHOLDER_IN_URL = /=(?:\$|%24)(?:period|previous|product)(?:&|$)/;

const SEGMENT = /^(?:\[([A-Za-z_][A-Za-z0-9_]*)=([^\].]+)\]|(#length|#position)|(-?\d+)|([A-Za-z_][A-Za-z0-9_]*))$/;

/** The value at a fact path; throws when it is not there (same rules as resolve_path in the backend). */
export function resolvePath(data: any, path: string): any {
  let cur = data;
  let position: number | null = null;
  path.split(".").forEach((seg) => {
    const m = SEGMENT.exec(seg);
    if (!m) throw new Error(`bad path segment ${seg}`);
    const [, field, want, special, index, key] = m;
    if (field) {
      if (!Array.isArray(cur)) throw new Error(`${seg}: not a list`);
      const i = cur.findIndex((item: any) => {
        const got = item && typeof item === "object" ? item[field] : undefined;
        return (typeof got === "boolean" ? String(got).toLowerCase() : String(got)) === want;
      });
      if (i < 0) throw new Error(`no item with ${field}=${want}`);
      cur = cur[i];
      position = i + 1;
      return;
    }
    if (special === "#length") {
      if (cur == null || typeof cur.length !== "number") throw new Error("#length of a non-list");
      cur = cur.length;
    } else if (special === "#position") {
      if (position === null) throw new Error("#position must follow [field=value]");
      cur = position;
    } else if (index !== undefined) {
      const n = parseInt(index, 10);
      if (!Array.isArray(cur) || n < -cur.length || n >= cur.length) throw new Error(`${seg}: no such item`);
      cur = cur[n < 0 ? cur.length + n : n];
    } else {
      if (cur == null || typeof cur !== "object" || !(key in cur)) throw new Error(`no key ${key}`);
      cur = cur[key];
    }
    position = null;
  });
  return cur;
}

/** Path lookup that returns undefined instead of throwing. */
export function tryPath(data: any, path: string): any {
  try {
    return resolvePath(data, path);
  } catch {
    return undefined;
  }
}

// ---------------------------------------------------------------- formats

const MINUS = "−";

function sig3(x: number): string {
  // Three significant figures, trailing zeros dropped: 25.1, 8.84, 613, 42.
  const a = Math.abs(x);
  if (a >= 1000) return Math.round(a).toLocaleString("en-US"); // €1,563 bn, as the agent writes it
  const digits = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return trimZeros(a.toFixed(digits));
}

function trimZeros(s: string): string {
  return s.indexOf(".") >= 0 ? s.replace(/\.?0+$/, "") : s;
}

/** A no-break space between a figure and its unit: "€898 m" never breaks across two lines. */
export const NBSP = " ";

/** The same for figures an agent wrote out in words: "€898 m", "1.5 bn" and "12 %" keep their unit on the line. */
export function keepFigures(text: string): string {
  return String(text).replace(/(\d) (bn|m|k|billion|million|%)(?![\p{L}\d])/gu, `$1${NBSP}$2`);
}

/**
 * The first `maxSentences` sentences of a reply, for the chat log: the full answer stays in
 * "More about this chart". A sentence boundary is a `.`, `!` or `?` followed by a space and a
 * capital letter or a "[n]" mark, so a decimal like "1.2 bn" is never split.
 */
export function shortenReply(text: string, maxSentences = 2): { shown: string; truncated: boolean } {
  const sentences = String(text)
    .trim()
    .split(/(?<=[.!?])\s+(?=[A-ZÀ-Ü\[])/);
  if (sentences.length <= maxSentences) return { shown: text, truncated: false };
  return { shown: sentences.slice(0, maxSentences).join(" "), truncated: true };
}

/** €25.1 bn, €613 m, €42 k; with the sign only when asked (or when negative). */
export function formatEur(v: number, signed = false): string {
  const a = Math.abs(v);
  let body: string;
  if (a >= 1e9) body = `€${sig3(a / 1e9)}${NBSP}bn`;
  else if (a >= 1e6) body = `€${sig3(a / 1e6)}${NBSP}m`;
  else if (a >= 1e3) body = `€${sig3(a / 1e3)}${NBSP}k`;
  else body = `€${Math.round(a).toLocaleString("en-US")}`;
  if (v < 0) return MINUS + body;
  return signed && v > 0 ? "+" + body : body;
}

function signedPrefix(v: number, signed: boolean): string {
  if (v < 0) return MINUS;
  return signed && v > 0 ? "+" : "";
}

/** A percentage already in percent: 5.1 -> 5.1%. */
export function formatPercent(v: number, signed = false): string {
  const a = Math.abs(v);
  const s = a >= 100 ? a.toFixed(0) : trimZeros(a.toFixed(1));
  return `${signedPrefix(v, signed)}${s}%`;
}

export function formatNumber(v: number): string {
  const a = Math.abs(v);
  const s =
    a >= 1e9
      ? `${sig3(a / 1e9)}${NBSP}bn`
      : a >= 1e6
        ? `${sig3(a / 1e6)}${NBSP}m`
        : a >= 100
          ? Math.round(a).toLocaleString("en-US")
          : trimZeros(a.toFixed(a >= 10 ? 1 : 2));
  return (v < 0 ? MINUS : "") + s;
}

/** A quantity: 12,400; 1.23 m; 2.1 bn (counts and tonnes are not money: no euro sign). */
export function formatQty(v: number): string {
  const a = Math.abs(v);
  const s =
    a >= 1e9
      ? `${sig3(a / 1e9)}${NBSP}bn`
      : a >= 1e6
        ? `${sig3(a / 1e6)}${NBSP}m`
        : a >= 100
          ? Math.round(a).toLocaleString("en-US")
          : trimZeros(a.toFixed(a >= 10 ? 1 : 2));
  return (v < 0 ? MINUS : "") + s;
}

/** "qty:cars" -> "cars"; undefined for any other format. */
export function qtyUnit(format?: string): string | undefined {
  const m = /^qty:(.+)$/.exec(String(format || ""));
  return m ? m[1] : undefined;
}

export function ordinal(n: number): string {
  const i = Math.round(n);
  const mod100 = i % 100;
  const suffix = mod100 >= 11 && mod100 <= 13 ? "th" : ["th", "st", "nd", "rd"][i % 10] || "th";
  return `${i}${suffix}`;
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** 2025 -> 2025; 2025-03 -> March 2025. */
export function formatPeriod(p: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(String(p));
  if (!m) return String(p);
  return `${MONTHS[parseInt(m[2], 10) - 1] || m[2]} ${m[1]}`;
}

/** Formats a fact value; non-numbers are shown as they are. */
export function formatValue(value: any, format?: FactFormat | string): string {
  if (value === null || value === undefined) return "";
  if (format === "text" || format === "raw") return String(value);
  if (format === "period") return formatPeriod(String(value));
  if (format === "month") {
    const n = typeof value === "number" ? value : MONTHS.findIndex((m) => m.slice(0, 3).toLowerCase() === String(value).slice(0, 3).toLowerCase()) + 1;
    return MONTHS[Math.round(n) - 1] || String(value);
  }
  const v = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(v)) return String(value);
  const unit = qtyUnit(format);
  if (unit) return `${formatQty(v)}${NBSP}${unit}`;
  switch (format) {
    case "qty":
      return formatQty(v);
    case "tonnes":
      return `${formatQty(v)}${NBSP}t`;
    case "eur":
    case "compact":
      return formatEur(v);
    case "eur_signed":
      return formatEur(v, true);
    // A fraction, to one decimal as the agent's replies write it (0.665 -> 66.5%, 0.093 -> 9.3%, 0.86 -> 86%).
    case "pct":
      return formatPercent(v * 100);
    case "pct_signed":
      return formatPercent(v * 100, true);
    case "percent":
      return formatPercent(v);
    case "percent_signed":
      return formatPercent(v, true);
    case "int":
      return Math.round(v).toLocaleString("en-US");
    case "ordinal":
      return ordinal(v);
    case "number":
    default:
      return formatNumber(v);
  }
}

/** Short axis label of a value in a chart format. */
export function formatAxis(v: number, format?: string): string {
  if (format === "eur" || format === "eur_signed" || format === "compact") {
    const a = Math.abs(v);
    if (a >= 1e9) return `${trimZeros((v / 1e9).toFixed(a >= 1e10 ? 0 : 1))}`;
    if (a >= 1e6) return `${trimZeros((v / 1e6).toFixed(0))}${NBSP}m`;
    return formatNumber(v);
  }
  if (format === "tonnes") return `${formatQty(v)}${NBSP}t`;
  if (format === "qty" || qtyUnit(format)) return formatQty(v);
  if (format === "percent" || format === "percent_signed") return `${trimZeros(v.toFixed(1))}%`;
  if (format === "pct" || format === "pct_signed") return `${trimZeros((v * 100).toFixed(1))}%`;
  return formatNumber(v);
}

// ---------------------------------------------------------------- resolution

export function isOpFact(f: Fact): f is OpFact {
  return (f as OpFact).op !== undefined;
}
export function isQueryFact(f: Fact): f is QueryFact {
  return (f as QueryFact).query !== undefined && (f as OpFact).op === undefined;
}
export function isApiFact(f: Fact): f is ApiFact {
  return !isOpFact(f) && !isQueryFact(f);
}

export interface FactValue {
  /** Raw value read from the API (or derived). */
  value?: any;
  /** Formatted text, ready to show. */
  text?: string;
  status: "ok" | "loading" | "missing" | "not_loaded" | "error";
  /** Why a value is missing, for the tooltip. */
  reason?: string;
  /** Where the figure comes from: the query it is read from (and the path in its answer), or the
   * operation the page did on other figures. For the "where does this figure come from" tooltip. */
  source?: { url?: string; path?: string; op?: string; of?: string[] };
}

export interface ResolvedFacts {
  values: Record<string, FactValue>;
  /** Every relative URL the facts read, for provenance. */
  urls: string[];
  /** True while any fact is still loading. */
  loading: boolean;
}

function fromState(state: QueryState | undefined, path: string, format?: string): FactValue {
  if (!state || state.loading) return { status: "loading" };
  if (state.error) return { status: "error", reason: `${state.error.message} (${state.url})` };
  if (state.notLoaded) return { status: "not_loaded", reason: `Not loaded: ${state.url}` };
  try {
    const value = resolvePath(state.data, path);
    return { value, text: formatValue(value, format), status: "ok", source: { url: state.url, path } };
  } catch (e: any) {
    return { status: "missing", reason: `${path}: ${e?.message || "not found"} (${state.url})` };
  }
}

function applyOp(op: OpFact["op"], a: number, b?: number): number | undefined {
  switch (op) {
    case "diff":
      return b === undefined ? undefined : a - b;
    case "sum":
      return b === undefined ? a : a + b;
    case "ratio":
      return b === undefined || b === 0 ? undefined : a / b;
    case "growth":
      return b === undefined || b === 0 ? undefined : a / b - 1;
    case "abs":
      return Math.abs(a);
    default:
      return undefined;
  }
}

/**
 * Resolves a set of facts. `own` maps query keys (and "self" for a mark) to
 * their states; inline facts are fetched here.
 */
export function useFacts(facts: Facts | undefined, ownStates: Record<string, QueryState>): ResolvedFacts {
  const ctx = usePageContext();
  const names = facts ? Object.keys(facts) : [];
  // Inline facts follow the page: "$period", "$previous" and "$product" are filled from spec.context.
  const inlineUrls = names.map((n) => {
    const f = facts![n];
    return isApiFact(f) ? queryUrl({ endpoint: f.endpoint, params: substituteContext(f.params, ctx) }) : null;
  });
  // A query state fetched with its placeholders still in it (a mark's own query, say) is fetched
  // again with them filled, so a fact never reads the 400 of a literal "$period".
  const ownKeys = Object.keys(ownStates);
  const repairUrls = ownKeys.map((k) => {
    const u = ownStates[k]?.url;
    return u && PLACEHOLDER_IN_URL.test(u) ? fillUrl(u, ctx) : null;
  });
  const repaired = useUrls(repairUrls);
  const own: Record<string, QueryState> = {};
  ownKeys.forEach((k, i) => (own[k] = repairUrls[i] ? repaired[i] : ownStates[k]));
  const inlineStates = useUrls(inlineUrls);
  const values: Record<string, FactValue> = {};
  const urls: string[] = [];

  const resolve = (name: string, depth: number): FactValue => {
    if (values[name]) return values[name];
    const f = facts?.[name];
    if (!f) return { status: "missing", reason: `unknown fact ${name}` };
    if (depth > 10) return { status: "error", reason: "fact cycle" };
    let out: FactValue;
    if (isOpFact(f)) {
      const parts = f.of.map((n) => resolve(n, depth + 1));
      const bad = parts.find((p) => p.status !== "ok");
      if (bad) out = { status: bad.status, reason: bad.reason };
      else {
        const nums = parts.map((p) => Number(p.value));
        const v = applyOp(f.op, nums[0], nums[1]);
        out =
          v === undefined || !Number.isFinite(v)
            ? { status: "missing", reason: `${f.op} of ${f.of.join(", ")} is undefined` }
            : { value: v, text: formatValue(v, f.format), status: "ok", source: { op: f.op, of: f.of } };
      }
    } else if (isQueryFact(f)) {
      const st = own[f.query];
      if (!st) out = { status: "missing", reason: `no query ${f.query}` };
      else {
        if (st.url) urls.push(st.url);
        out = fromState(st, f.path, f.format);
      }
    } else {
      const i = names.indexOf(name);
      const st = inlineStates[i];
      if (st?.url) urls.push(st.url);
      out = fromState(st, f.path, f.format);
    }
    values[name] = out;
    return out;
  };

  names.forEach((n) => resolve(n, 0));
  const loading = names.some((n) => values[n].status === "loading");
  return { values, urls: urls.filter((u, i) => urls.indexOf(u) === i), loading };
}

export interface TextPart {
  text: string;
  fact?: string;
  value?: FactValue;
}

/** Splits a template into literal text and {fact} parts. */
export function splitTemplate(template: string, values: Record<string, FactValue>): TextPart[] {
  const parts: TextPart[] = [];
  const re = /\{([a-z][a-z0-9_]*)\}/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(template))) {
    if (m.index > last) parts.push({ text: template.slice(last, m.index) });
    const name = m[1];
    const v = values[name] || { status: "missing" as const, reason: `unknown fact ${name}` };
    parts.push({ text: v.status === "ok" ? v.text || "" : "", fact: name, value: v });
    last = re.lastIndex;
  }
  if (last < template.length) parts.push({ text: template.slice(last) });
  return parts;
}

/** Plain-text version of a template, for aria labels and titles; unresolved facts become an ellipsis. */
export function fillTemplate(template: string, values: Record<string, FactValue>): string {
  return splitTemplate(template, values)
    .map((p) => (p.fact ? (p.value?.status === "ok" ? p.text : "…") : p.text))
    .join("");
}

/** Query states of a panel keyed by query key. */
export function keyed(queries: Query[], states: QueryState[]): Record<string, QueryState> {
  const out: Record<string, QueryState> = {};
  queries.forEach((q, i) => {
    if (q.key) out[q.key] = states[i];
  });
  return out;
}
