// Fetching /insights queries: a module-level stale-while-revalidate cache
// keyed by the relative URL, shared by every panel, mark and fact on the page.
// A response with loaded:false is "not loaded", never zero.

import { useEffect, useReducer } from "react";
import { apiHref, queryUrl } from "./api";
import type { Params } from "./types";

export interface QueryError {
  status: number;
  message: string;
  url: string;
}

export interface QueryState<T = any> {
  /** Relative URL, the cache key (also what provenance.sources shows). */
  url: string;
  data?: T;
  error?: QueryError;
  /** The backend answered loaded:false: the data is not there yet. */
  notLoaded: boolean;
  loading: boolean;
  /** True when data, no error and loaded. */
  ready: boolean;
}

interface Entry {
  data?: any;
  error?: QueryError;
  notLoaded: boolean;
  at: number;
  inflight?: Promise<void>;
}

/** Older than this, a cached answer is shown and fetched again in the background. */
const STALE_MS = 5 * 60 * 1000;
/** A failed query is tried again after this, a few times (the backend may be warming up). */
const RETRY_MS = 15 * 1000;
const MAX_RETRIES = 3;
const cache = new Map<string, Entry>();
const listeners = new Set<() => void>();
const retries = new Map<string, number>();

function scheduleRetry(url: string) {
  const n = retries.get(url) || 0;
  if (n >= MAX_RETRIES) return;
  retries.set(url, n + 1);
  window.setTimeout(() => load(url), RETRY_MS * (n + 1));
}

function notify() {
  listeners.forEach((l) => l());
}

function messageOf(body: any, fallback: string): string {
  if (!body) return fallback;
  if (typeof body === "string") return body;
  const d = body.detail ?? body.message ?? body.error;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x: any) => x?.msg || JSON.stringify(x)).join("; ");
  return fallback;
}

function load(url: string): Promise<void> {
  const prev = cache.get(url);
  if (prev?.inflight) return prev.inflight;
  const entry: Entry = prev ? { ...prev } : { notLoaded: false, at: 0 };
  const run = (async () => {
    try {
      const res = await fetch(apiHref(url), { headers: { Accept: "application/json" } });
      let body: any = null;
      try {
        body = await res.json();
      } catch {
        body = null;
      }
      if (!res.ok) {
        cache.set(url, {
          notLoaded: false,
          at: Date.now(),
          error: { status: res.status, message: messageOf(body, res.statusText || `HTTP ${res.status}`), url },
        });
        scheduleRetry(url);
      } else {
        const notLoaded = !!body && typeof body === "object" && !Array.isArray(body) && body.loaded === false;
        cache.set(url, { data: body, notLoaded, at: Date.now() });
        if (notLoaded) scheduleRetry(url);
        else retries.delete(url);
      }
    } catch (e: any) {
      // Network failure: keep stale data if there is some.
      const old = cache.get(url);
      cache.set(url, {
        data: old?.data,
        notLoaded: old?.notLoaded ?? false,
        at: Date.now(),
        error: old?.data ? undefined : { status: 0, message: "The backend could not be reached", url },
      });
      scheduleRetry(url);
    }
    notify();
  })();
  entry.inflight = run;
  cache.set(url, entry);
  return run;
}

function ensure(url: string) {
  const e = cache.get(url);
  if (!e) {
    load(url);
    return;
  }
  if (!e.inflight && Date.now() - e.at > STALE_MS) load(url);
}

function snapshot(url: string): QueryState {
  const e = cache.get(url);
  const hasAnswer = !!e && (e.data !== undefined || !!e.error);
  const loading = !hasAnswer;
  return {
    url,
    data: e?.data,
    error: e?.error,
    notLoaded: !!e?.notLoaded,
    loading,
    ready: !!e && e.data !== undefined && !e.error && !e.notLoaded,
  };
}

/** Fetches (or reads from the cache) a relative URL, outside React. */
export async function fetchQuery<T = any>(url: string): Promise<QueryState<T>> {
  ensure(url);
  const e = cache.get(url);
  if (e?.inflight) await e.inflight;
  return snapshot(url);
}

/** Drops the cache, for a full refresh. */
export function invalidateQueries() {
  cache.clear();
  notify();
}

/** Several relative URLs at once; null entries are skipped and come back as idle states. */
export function useUrls(urls: Array<string | null | undefined>): QueryState[] {
  const [, force] = useReducer((x: number) => x + 1, 0);
  const key = urls.map((u) => u || "").join("\n");
  useEffect(() => {
    listeners.add(force);
    urls.forEach((u) => {
      if (u) ensure(u);
    });
    return () => {
      listeners.delete(force);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return urls.map((u) =>
    u ? snapshot(u) : { url: "", notLoaded: false, loading: false, ready: false },
  );
}

/** One query {endpoint, params}; pass null to skip. */
export function useQuery<T = any>(q: { endpoint: string; params?: Params } | null | undefined): QueryState<T> {
  return useUrls([q ? queryUrl(q) : null])[0] as QueryState<T>;
}

/** Several queries, in order. */
export function useQueries(qs: Array<{ endpoint: string; params?: Params } | null | undefined>): QueryState[] {
  return useUrls(qs.map((q) => (q ? queryUrl(q) : null)));
}

/** The first problem among states, for a panel's error card: error first, then not loaded, then loading. */
export function firstProblem(states: QueryState[]): { kind: "error" | "not_loaded" | "loading"; state: QueryState } | null {
  const err = states.find((s) => s.error);
  if (err) return { kind: "error", state: err };
  const nl = states.find((s) => s.notLoaded);
  if (nl) return { kind: "not_loaded", state: nl };
  const ld = states.find((s) => s.loading);
  if (ld) return { kind: "loading", state: ld };
  return null;
}
