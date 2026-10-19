// Backend URL and query URLs. queryUrl matches query_url() in
// app/backend/view/validate.py, so the page and provenance.sources agree.

import type { Params, Query } from "./types";

export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined) || "http://localhost:8000";

/** True when VITE_VIEW_MOCK=1: the spec comes from public/mock/view.json, with no backend /view. */
export const VIEW_MOCK: boolean = String(import.meta.env.VITE_VIEW_MOCK || "") === "1";

function encode(v: string): string {
  return encodeURIComponent(v).replace(/%20/g, "+").replace(/%2C/gi, ",");
}

/** /insights/ranking?by=partner&period=2025&flow=IMPORT (booleans lower-cased, commas kept). */
export function queryUrl(q: { endpoint: string; params?: Params }): string {
  const params = q.params || {};
  const parts = Object.keys(params).map((k) => {
    const v = params[k];
    const s = typeof v === "boolean" ? String(v).toLowerCase() : String(v);
    return `${encode(k)}=${encode(s)}`;
  });
  return q.endpoint + (parts.length ? `?${parts.join("&")}` : "");
}

/** Absolute URL of a relative backend path. */
export function apiHref(relative: string): string {
  if (/^https?:\/\//i.test(relative)) return relative;
  return API_URL.replace(/\/+$/, "") + relative;
}

const SID_KEY = "ask-the-data:session";
let memorySid: string | null = null;

function newSid(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/**
 * This tab's session id. The tab has a page of its own on the backend (?sid= on every /view call) and its own
 * conversation with the agent (?sid= on the agent's socket, which draws on that page); a reload comes back with
 * it. Without session storage it lasts until the tab reloads.
 */
export function tabSession(): string {
  try {
    let sid = sessionStorage.getItem(SID_KEY);
    if (!sid) {
      sid = newSid();
      sessionStorage.setItem(SID_KEY, sid);
    }
    return sid;
  } catch {
    if (!memorySid) memorySid = newSid();
    return memorySid;
  }
}

/** ``url`` with this tab's session id (?sid=...). */
export function withTabSession(url: string): string {
  return `${url}${url.includes("?") ? "&" : "?"}sid=${encodeURIComponent(tabSession())}`;
}

/**
 * The agent's socket URL for this tab: ?sid= (the demo player keeps a reply for a tab that reloads) and BAF's
 * session_id=page-<sid>, which makes the agent draw on this tab's page (lux_insights_agent.page_session).
 */
export function agentSocketHref(url: string): string {
  return `${withTabSession(url)}&session_id=${encodeURIComponent(`page-${tabSession()}`)}`;
}

/** Absolute URL of a /view path, on this tab's own page. */
export function viewHref(relative: string): string {
  return withTabSession(apiHref(relative));
}

/** The same query with some params replaced (undefined removes one). */
export function withParams(q: Query, patch: Record<string, string | number | boolean | undefined>): Query {
  const params: Params = { ...q.params };
  Object.keys(patch).forEach((k) => {
    const v = patch[k];
    if (v === undefined) delete params[k];
    else params[k] = v;
  });
  return { ...q, params };
}
