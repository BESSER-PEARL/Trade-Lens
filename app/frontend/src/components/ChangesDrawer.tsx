// "What changed": the last versions of the page from GET /view/history, each with the agent that made it
// (in its pen), what its batch did (the op kinds) and when, with undo and redo. Shown in the Ask panel for a
// reader whose profile asks for the version history (the developer: show contains "version_history").
// Hidden from the Ask panel for now (not rendered anywhere); the history stays at GET /view/history.
// It reads the audit trail the backend already keeps; it never changes the page except through undo/redo,
// which make a new version like any other.
//
// Who the page is drawn for is the reader's choice, not a change to undo: when the version Undo would take
// back is the switch to this reader, the drawer skips it (it undoes the change before it instead) and draws
// the page for the same reader again, so the drawer never undoes itself away.

import React, { useEffect, useState } from "react";
import { apiHref } from "../view/api";
import { useViewStore } from "../view/ViewStore";
import { profileOf } from "../view/personas";
import type { ViewResponse } from "../view/types";
import { AGENT_COLORS } from "../view/tokens";

interface HistoryEntry {
  version: number;
  at: string;
  agent: string;
  action: string;
  message?: string | null;
  n_ops?: number;
  op_kinds?: string[];
  persona?: string;
}

interface History {
  version: number;
  can_undo: boolean;
  can_redo: boolean;
  entries: HistoryEntry[];
}

const SHOWN = 8;
const OWN = { color: "#5E6B7A", ink: "#3C4651" };
const WHO: Record<string, { title: string; color: string; ink: string }> = {
  user: { title: "You", color: "#14202E", ink: "#14202E" },
  baseline: { title: "Baseline", ...OWN },
};

function who(agent: string) {
  return AGENT_COLORS[agent] || WHO[agent] || { title: agent.replace(/_/g, " "), ...OWN };
}

/** What a version did: its ops ("add_mark event, upsert_panel"), else its action ("undo", "reset"). */
function what(e: HistoryEntry): string {
  if (e.op_kinds && e.op_kinds.length) return e.op_kinds.join(", ");
  if (e.action === "ops") return `${e.n_ops ?? "?"} op${e.n_ops === 1 ? "" : "s"}`;
  return e.action;
}

function clock(at: string): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? at : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function readerOf(spec: unknown): string | undefined {
  const p = (spec as any)?.context?.persona;
  return typeof p === "string" && p ? p : undefined;
}

// Open or closed, and the last note, survive the drawer being drawn again (an undo passes through another
// reader for a moment, and the panel draws the drawer only for the developer).
let openAcross = false;
let noteAcross: string | null = null;
const noteListeners = new Set<(v: string | null) => void>();

export const ChangesDrawer: React.FC = () => {
  const store = useViewStore();
  const version = store.spec?.version;
  const [open, setOpenState] = useState(openAcross);
  const setOpen = (v: boolean) => {
    openAcross = v;
    setOpenState(v);
  };
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNoteState] = useState<string | null>(noteAcross);
  // The drawer that wrote the note may be gone by then: every drawer on screen hears it.
  const setNote = (v: string | null) => {
    noteAcross = v;
    noteListeners.forEach((fn) => fn(v));
  };
  useEffect(() => {
    noteListeners.add(setNoteState);
    setNoteState(noteAcross);
    return () => {
      noteListeners.delete(setNoteState);
    };
  }, []);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || store.fallback) return;
    let gone = false;
    fetch(apiHref("/view/history"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((h: History) => {
        if (!gone) { setHistory(h); setError(null); }
      })
      .catch((err) => { if (!gone) setError(`The history could not be read (${err.message || err}).`); });
    return () => { gone = true; };
  }, [open, version, store.fallback]);

  const act = (fn: () => Promise<unknown>) => {
    setBusy(true);
    setNote(null);
    fn().catch(() => setError("The page did not take it.")).finally(() => setBusy(false));
  };

  const failed = (r: ViewResponse) => {
    if (!r.ok) throw new Error(r.detail || `HTTP ${r.status}`);
    return r;
  };

  /** Redo, keeping the reader too: a version drawn for another reader is passed over the same way. */
  const redo = async () => {
    const reader = readerOf(store.spec);
    const first = failed(await store.redo());
    if (!reader || readerOf(first.spec) === reader) return;
    const second = await store.redo();
    if (second.ok && readerOf(second.spec) === reader) return;
    const label = profileOf(reader)?.label || reader;
    failed(await store.send([{ op: "set_context", persona: reader }], { message: `Still reading as ${label}`, baseVersion: null }));
  };

  /** Undo the last change to the page's content, keeping the reader the page is drawn for. */
  const undo = async () => {
    const reader = readerOf(store.spec);
    const first = failed(await store.undo());
    if (!reader || readerOf(first.spec) === reader) return;
    // That version was the switch to this reader: skip it and take back the change before it.
    const second = await store.undo();
    const label = profileOf(reader)?.label || reader;
    if (!second.ok) {
      // Nothing before the switch: put the switch back exactly as it was (redo keeps its history).
      failed(await store.redo());
      setNote(`Nothing to undo before the page was drawn for the ${label.toLowerCase()}.`);
      return;
    }
    if (readerOf(second.spec) === reader) return;
    failed(await store.send([{ op: "set_context", persona: reader }], { message: `Still reading as ${label}`, baseVersion: null }));
  };

  const entries = (history?.entries || []).slice(0, SHOWN);
  return (
    <details className="ask-changes" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>What changed{typeof version === "number" ? ` (version ${version})` : ""}</summary>
      <div style={{ display: "flex", gap: 8, margin: "8px 0" }}>
        <button type="button" className="cx-btn ask-clear" disabled={busy || store.fallback || !history?.can_undo} onClick={() => act(undo)}>
          Undo
        </button>
        <button type="button" className="cx-btn ask-clear" disabled={busy || store.fallback || !history?.can_redo} onClick={() => act(redo)}>
          Redo
        </button>
      </div>
      {error && <p className="ask-error">{error}</p>}
      {note && <p className="ask-intro">{note}</p>}
      {store.fallback && <p className="ask-error">The page service is offline: no history to show.</p>}
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }} aria-label="The last versions of the page">
        {entries.map((e) => {
          const w = who(e.agent);
          return (
            <li key={e.version} style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: "2px 10px", alignItems: "baseline", fontSize: 13 }}>
              <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 700, color: "var(--ink)" }}>v{e.version}</span>
              <span style={{ minWidth: 0 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 600, color: w.ink }}>
                  <span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 2, background: w.color, flex: "none" }} />
                  {w.title}
                </span>{" "}
                <code style={{ fontSize: 12, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{what(e)}</code>
                {e.message && (
                  <span style={{ display: "block", color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={e.message}>
                    {e.message}
                  </span>
                )}
              </span>
              <time dateTime={e.at} style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
                {clock(e.at)}
              </time>
            </li>
          );
        })}
      </ol>
    </details>
  );
};

export default ChangesDrawer;
