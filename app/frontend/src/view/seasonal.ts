// "As recorded / Seasonally adjusted" on a monthly line: a display choice of the reader, not part of the
// page's state (no op, nothing saved), shared by the chart that draws it (LinePanel) and the card that says
// how it was made (MadeCard), so the card names the method of the line on screen. A tiny store keyed by
// panel id, like the open state of "How this was made".

import { useSyncExternalStore } from "react";
import type { Query } from "./types";

let adjustedKeys = new Set<string>();
// The panels whose adjusted values are on screen (switched on, fitted, not refused as too short).
let shownKeys = new Set<string>();
let listeners: Array<() => void> = [];

function subscribe(l: () => void) {
  listeners.push(l);
  return () => {
    listeners = listeners.filter((x) => x !== l);
  };
}

/** Whether panel `id` shows the seasonally adjusted values, and a setter. */
export function useSeasonallyAdjusted(id: string): [boolean, (on: boolean) => void] {
  const snap = useSyncExternalStore(subscribe, () => adjustedKeys);
  const set = (on: boolean) => {
    if (on === adjustedKeys.has(id)) return;
    const next = new Set(adjustedKeys);
    if (on) next.add(id);
    else next.delete(id);
    adjustedKeys = next;
    listeners.forEach((l) => l());
  };
  return [snap.has(id), set];
}

/** LinePanel reports whether panel `id` draws the adjusted values (not when refused as too short). */
export function setAdjustedShown(id: string, on: boolean) {
  if (on === shownKeys.has(id)) return;
  const next = new Set(shownKeys);
  if (on) next.add(id);
  else next.delete(id);
  shownKeys = next;
  listeners.forEach((l) => l());
}

/** Whether panel `id` draws the seasonally adjusted values right now. */
export function useAdjustedShown(id: string): boolean {
  return useSyncExternalStore(subscribe, () => shownKeys).has(id);
}

export const SERIES_ENDPOINT = "/insights/series";

/** A monthly /insights/series query can be seasonally adjusted (the backend says so when it is too short). */
export function adjustable(q: { endpoint: string; params?: Record<string, any> } | null | undefined): boolean {
  return !!q && q.endpoint === SERIES_ENDPOINT && String(q.params?.freq || "") === "M";
}

/** The same query asking for the adjusted values too, or null when it cannot be adjusted. */
export function adjustedQuery<Q extends Query>(q: Q): Q | null {
  return adjustable(q) ? { ...q, params: { ...(q.params || {}), adjust: "true" } } : null;
}

/** The queries as drawn: the adjustable ones with adjust=true when the reader switched to adjusted. */
export function asShown<Q extends Query>(qs: Q[], on: boolean): Q[] {
  return on ? qs.map((q) => adjustedQuery(q) || q) : qs;
}

/** The legend's words (plain: no method names; the exact method is in "For statisticians"). */
export const ADJUSTED_WORDS = "Seasonally adjusted: the usual month-to-month pattern taken out";
export const RECORDED_WORDS = "As recorded";
