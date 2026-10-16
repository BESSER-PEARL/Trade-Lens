// Which question drew which chart. The agent's panels carry no question in the spec, so the page keeps
// it: "Ask the data" notes each question it sends, and a panel that appears (or is re-drawn) soon after
// is filed under it. Kept in localStorage so the history survives a reload. Display only: the spec is
// never changed. Charts drawn for one question form a group; a note the reader writes belongs to the group.

import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { isAgentOwner } from "./tokens";
import type { ViewSpec } from "./types";

const KEY = "lux.panelPrompts";
/** A chart drawn within this long of a question is that question's answer. */
const WINDOW_MS = 3 * 60 * 1000;

export interface PanelPrompt {
  prompt: string;
  at: number;
}

/** What an agent said in answer to a question: the text behind the chart it drew. */
export interface Reply {
  agent: string;
  text: string;
}

// The question being answered survives a reload of this tab (sessionStorage): the agent's reply and charts still
// arrive after it (the demo player holds them for the reloaded page), and they belong to that question.
const PENDING_KEY = "lux.pendingPrompt";
let pending: PanelPrompt | null = (() => {
  try {
    const p = JSON.parse(sessionStorage.getItem(PENDING_KEY) || "null");
    return p && typeof p.prompt === "string" && typeof p.at === "number" ? p : null;
  } catch {
    return null;
  }
})();
let listeners: Array<() => void> = [];

function load(): Record<string, PanelPrompt> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}") || {};
  } catch {
    return {};
  }
}

let store: Record<string, PanelPrompt> = load();

const REPLIES_KEY = "lux.panelReplies";
/** The agents' answers, by the time their question was asked. */
let replies: Record<string, Reply[]> = (() => {
  try {
    return JSON.parse(localStorage.getItem(REPLIES_KEY) || "{}") || {};
  } catch {
    return {};
  }
})();

const NOTES_KEY = "lux.groupNotes";
/** The reader's own notes on a group of charts, by the time their question was asked. */
let notes: Record<string, string> = (() => {
  try {
    return JSON.parse(localStorage.getItem(NOTES_KEY) || "{}") || {};
  } catch {
    return {};
  }
})();

function persist() {
  // An answer or a note nobody draws on any more is dropped with its charts.
  const live = new Set(Object.values(store).map((p) => String(p.at)));
  replies = Object.fromEntries(Object.entries(replies).filter(([at]) => live.has(at) || (pending && String(pending.at) === at)));
  notes = Object.fromEntries(Object.entries(notes).filter(([at]) => live.has(at)));
  try {
    localStorage.setItem(KEY, JSON.stringify(store));
    localStorage.setItem(REPLIES_KEY, JSON.stringify(replies));
    localStorage.setItem(NOTES_KEY, JSON.stringify(notes));
  } catch {
    /* storage full or blocked: the history lasts for this visit only */
  }
  snapshot = { prompts: store, replies, notes };
  listeners.forEach((l) => l());
}

let snapshot = { prompts: store, replies, notes };

/** The reader's note on a group of charts (empty text removes it). */
export function setNote(at: number, text: string) {
  const key = String(at);
  const next = { ...notes };
  if (text.trim()) next[key] = text;
  else delete next[key];
  notes = next;
  persist();
}

function save(next: Record<string, PanelPrompt>) {
  store = next;
  persist();
}

/** An agent answered the pending question in words: kept for the charts it drew. */
export function noteReply(agent: string, text: string) {
  if (!pending || Date.now() - pending.at >= WINDOW_MS || !text.trim()) return;
  const key = String(pending.at);
  replies = { ...replies, [key]: [...(replies[key] || []).filter((r) => r.agent !== agent || r.text !== text), { agent, text }] };
  persist();
}

/** One of this browser's questions is awaiting its answer. */
export function askedRecently(): boolean {
  return !!pending && Date.now() - pending.at < WINDOW_MS;
}

// A finished notebook shown at once (the demo player): every chart arrives in one change, so the player names the
// charts of each question and they are filed under it, not under whichever question was asked last.
let claims: Record<string, PanelPrompt> = {};

/** "Ask the data" sends a question: the charts that appear next belong to it (or, named, these `panels`). */
export function notePrompt(prompt: string, back = 0, panels?: string[]) {
  // `back` ms earlier: the steps of a finished dashboard, asked together, are listed first question first
  pending = { prompt, at: Date.now() - back };
  if (panels?.length) {
    const asked = pending;
    claims = { ...claims, ...Object.fromEntries(panels.map((id) => [id, asked])) };
  }
  try {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  } catch {
    /* blocked storage: the question is remembered until the tab reloads */
  }
}

function subscribe(l: () => void) {
  listeners.push(l);
  return () => {
    listeners = listeners.filter((x) => x !== l);
  };
}

/** The question that drew this panel, if the page saw it asked. */
export function usePanelPrompt(id: string): (PanelPrompt & { replies: Reply[] }) | undefined {
  const s = useSyncExternalStore(subscribe, () => snapshot);
  const p = s.prompts[id];
  return p ? { ...p, replies: s.replies[String(p.at)] || [] } : undefined;
}

/** The charts drawn for one question, with what the agents said and the reader's note. */
export interface PromptGroupData {
  at: number;
  prompt: string;
  panelIds: string[];
  replies: Reply[];
  note: string;
}

/**
 * The page's agent charts (ids, in page order) that the page saw asked for, grouped by question, the newest
 * question first; and the ids of those charts, which the page then draws in their group and nowhere else.
 */
export function usePromptGroups(ids: string[]): { groups: PromptGroupData[]; prompted: Set<string> } {
  const s = useSyncExternalStore(subscribe, () => snapshot);
  const key = ids.join("|");
  return useMemo(() => {
    const by = new Map<number, PromptGroupData>();
    const prompted = new Set<string>();
    ids.forEach((id) => {
      const p = s.prompts[id];
      if (!p) return;
      prompted.add(id);
      const g = by.get(p.at) || { at: p.at, prompt: p.prompt, panelIds: [], replies: s.replies[String(p.at)] || [], note: s.notes[String(p.at)] || "" };
      g.panelIds.push(id);
      by.set(p.at, g);
    });
    return { groups: Array.from(by.values()).sort((a, b) => b.at - a.at), prompted };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s, key]);
}

const sig = (p: { type: string; title: string; queries: unknown; options?: unknown }) =>
  JSON.stringify([p.type, p.title, p.queries, p.options]);

export const panelDomId = (id: string) => `panel-${id}`;

/**
 * Watches the spec: a panel an agent adds, or re-draws, is filed under the pending question and the page
 * is brought to it (the newest one, when several arrive together). What was on the page at first load is
 * not new.
 */
export function useNewPanels(spec: ViewSpec | null, fallback: boolean, onNew?: (id: string) => void) {
  const seen = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    if (!spec || fallback) return;
    const now = new Map(spec.panels.map((p) => [p.id, sig(p)] as const));
    if (!seen.current) {
      seen.current = now;
      return;
    }
    const fresh = spec.panels.filter((p) => isAgentOwner(p.agent) && seen.current!.get(p.id) !== now.get(p.id));
    seen.current = now;
    // Panels that left the page take their question with them.
    const kept = Object.fromEntries(Object.entries(store).filter(([id]) => now.has(id)));
    const asked = pending && Date.now() - pending.at < WINDOW_MS ? pending : null;
    let claimed: PanelPrompt | null = null;
    let first = "";
    fresh.forEach((p) => {
      const claim = claims[p.id];
      if (claim) {
        kept[p.id] = claim;
        delete claims[p.id];
        if (!claimed || claim.at > claimed.at) [claimed, first] = [claim, p.id];
      } else if (asked) kept[p.id] = asked;
    });
    if (Object.keys(kept).length !== Object.keys(store).length || ((asked || claimed) && fresh.length)) save(kept);
    if (!fresh.length) return;
    // A notebook shown at once opens on its first question; otherwise the newest chart is brought into view.
    const last = first || fresh[fresh.length - 1].id;
    onNew?.(last);
    // After the layout has placed it.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        // The group the chart belongs to, so the question that drew it is in view with it.
        const chart = document.getElementById(panelDomId(last));
        const el = (chart?.closest(".cx-group") as HTMLElement | null) || chart;
        if (!el) return;
        el.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
        el.classList.remove("is-new");
        void el.offsetWidth; // restart the highlight
        el.classList.add("is-new");
      }),
    );
  }, [spec, fallback, onNew]);
}
