// Whose agent output is this? The page on the backend is one shared spec, so every browser receives
// the charts and notes that agents drew for anyone. This browser keeps the ones its own questions
// drew (localStorage) and the view store hides the rest: nothing is deleted, the other browser keeps
// its history, and a browser that has never asked starts from the base page.

import { useSyncExternalStore } from "react";
import { askedRecently } from "./panelPrompts";
import { isAgentOwner } from "./tokens";
import type { Panel, ViewSpec } from "./types";

const KEY = "lux.owned";

interface Owned {
  panels: string[];
  marks: string[];
}

// The page opens on "my dashboard": the overview chart and what this browser's questions drew. The rest of the
// base page (the figures, the flow map, products, peers) is the "full overview", one button away; which one is
// shown is this session's own choice, and the backend, shared by everyone, is not touched.
const OVERVIEW_KEY = "lux.overview";
let fullOverview = (() => {
  try {
    return sessionStorage.getItem(OVERVIEW_KEY) === "1";
  } catch {
    return false;
  }
})();

let owned: Owned | null = null;
let version = 0;
let listeners: Array<() => void> = [];
// What the last spec held, to tell what the latest change added.
let seenPanels: Map<string, string> | null = null;
let seenMarks: Set<string> | null = null;

function read(): Owned | null {
  try {
    const o = JSON.parse(localStorage.getItem(KEY) || "null");
    return o && Array.isArray(o.panels) && Array.isArray(o.marks) ? o : null;
  } catch {
    return null;
  }
}

function write(next: Owned) {
  owned = next;
  version += 1;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* blocked storage: the ownership lasts for this visit */
  }
  listeners.forEach((l) => l());
}

const sig = (p: Panel) => JSON.stringify([p.type, p.title, p.queries, p.options]);

/**
 * Called with every spec the backend sends, before the page sees it. The first one is the page as it
 * was (not ours, unless this browser used the app before ownership existed: then all of it is). After
 * that, what a change adds or re-draws while one of our questions is pending is ours.
 */
export function claim(spec: ViewSpec) {
  if (!owned) owned = read();
  const agentPanels = spec.panels.filter((p) => isAgentOwner(p.agent));
  const agentMarks = (spec.marks || []).filter((m) => m.agent !== "user");
  if (!seenPanels || !seenMarks) {
    seenPanels = new Map(spec.panels.map((p) => [p.id, sig(p)] as const));
    seenMarks = new Set((spec.marks || []).map((m) => m.id));
    if (!owned) {
      // A browser with a history of questions from before this existed owns what it can see.
      const veteran = !!localStorage.getItem("lux.panelPrompts");
      write(veteran ? { panels: agentPanels.map((p) => p.id), marks: agentMarks.map((m) => m.id) } : { panels: [], marks: [] });
    }
    return;
  }
  const freshPanels = agentPanels.filter((p) => seenPanels!.get(p.id) !== sig(p));
  const freshMarks = agentMarks.filter((m) => !seenMarks!.has(m.id));
  seenPanels = new Map(spec.panels.map((p) => [p.id, sig(p)] as const));
  seenMarks = new Set((spec.marks || []).map((m) => m.id));
  const live = new Set(spec.panels.map((p) => p.id));
  const liveMarks = new Set((spec.marks || []).map((m) => m.id));
  const mine = askedRecently();
  const cur = owned || { panels: [], marks: [] };
  const panels = new Set(cur.panels.filter((id) => live.has(id)));
  const marks = new Set(cur.marks.filter((id) => liveMarks.has(id)));
  if (mine) {
    freshPanels.forEach((p) => panels.add(p.id));
    freshMarks.forEach((m) => marks.add(m.id));
  }
  if (panels.size !== cur.panels.length || marks.size !== cur.marks.length || (mine && (freshPanels.length || freshMarks.length))) {
    write({ panels: Array.from(panels), marks: Array.from(marks) });
  }
}

/** This session shows the full base page, not just its dashboard. */
export function isFullOverview(): boolean {
  return fullOverview;
}

export function showFullOverview(on: boolean) {
  fullOverview = on;
  try {
    if (on) sessionStorage.setItem(OVERVIEW_KEY, "1");
    else sessionStorage.removeItem(OVERVIEW_KEY);
  } catch {
    /* blocked storage: lasts until the tab reloads */
  }
  write(owned || read() || { panels: [], marks: [] }); // only to tell the page
}

/** A fresh start in this browser: no charts, notes or questions of earlier visits (every tab shares them). */
export function forgetThisBrowser() {
  owned = { panels: [], marks: [] };
  seenPanels = null;
  seenMarks = null;
  try {
    localStorage.removeItem(KEY);
    localStorage.removeItem("lux.panelPrompts");
    localStorage.removeItem("lux.panelReplies");
    localStorage.removeItem("lux.groupNotes");
    sessionStorage.removeItem("ask-the-data:conversation");
  } catch {
    /* blocked storage: nothing was kept */
  }
}

/** Remove this browser's charts and notes from view; what is kept (pinned) stays. */
export function clearMine(spec: ViewSpec | null) {
  const pinned = new Set((spec?.panels || []).filter((p) => p.pinned).map((p) => p.id));
  const mine = new Set(owned?.panels || read()?.panels || []);
  write({ panels: Array.from(pinned).filter((id) => mine.has(id)), marks: [] });
}

/** The spec as this browser sees it: the base page, the readers' own panels, and what its questions drew. */
export function visibleSpec(spec: ViewSpec | null, fallback: boolean): ViewSpec | null {
  if (!spec || fallback) return spec;
  if (!owned) owned = read();
  const mine = new Set(owned?.panels || []);
  const myMarks = new Set(owned?.marks || []);
  const panels = spec.panels.filter((p) => (isAgentOwner(p.agent) ? mine.has(p.id) : fullOverview || !!p.pinned));
  const shown = new Set(panels.map((p) => p.id));
  const marks = (spec.marks || []).filter((m) => (m.agent === "user" || myMarks.has(m.id)) && shown.has(m.target));
  if (panels.length === spec.panels.length && marks.length === (spec.marks || []).length) return spec;
  const rows = spec.layout.rows.map((r) => ({ ...r, panels: r.panels.filter((id) => shown.has(id)) })).filter((r) => r.panels.length > 0);
  return { ...spec, panels, marks, layout: { ...spec.layout, rows } };
}

/** Re-render when the ownership changes. */
export function useOwnershipVersion(): number {
  return useSyncExternalStore(
    (l) => {
      listeners.push(l);
      return () => {
        listeners = listeners.filter((x) => x !== l);
      };
    },
    () => version,
  );
}
