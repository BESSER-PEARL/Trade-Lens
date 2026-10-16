import React from "react";
import "./Welcome.css";

/** Asks "Ask the data" a question from elsewhere on the page (it listens for this event). */
export const ASK_EVENT = "lux:ask";
/** A question chosen on the reader screen waits here while the page reloads; "Ask the data" asks it once connected. */
const PENDING_ASK = "lux.pendingAsk";
// Identify this page without comparing wall-clock and navigation timestamps (which can differ in private mode).
const PAGE_ID = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
  ? crypto.randomUUID()
  : Math.random().toString(36).slice(2);

/** Leave a question for the page that loads next. */
export function leavePendingAsk(question: string) {
  try {
    sessionStorage.setItem(PENDING_ASK, JSON.stringify({ q: question, page: PAGE_ID }));
  } catch {
    /* blocked storage: the question is not asked */
  }
}

/**
 * Send a question from the previous page once the chat accepts it. The page that wrote it must not send it
 * just before reloading. A different page identifier proves the reload, without relying on browser clocks.
 */
export function sendPendingAsk(send: (question: string) => boolean): boolean {
  try {
    const raw = sessionStorage.getItem(PENDING_ASK);
    if (!raw) return false;
    const { q, page } = JSON.parse(raw) as { q?: string; page?: string };
    if (typeof q !== "string" || !q.trim()) {
      sessionStorage.removeItem(PENDING_ASK);
      return false;
    }
    if (page === PAGE_ID) return false;
    if (!send(q)) return false;
    sessionStorage.removeItem(PENDING_ASK);
    return true;
  } catch {
    return false;
  }
}
export const askTheData = (question: string) => window.dispatchEvent(new CustomEvent(ASK_EVENT, { detail: question }));

interface Props {
  /** Questions to start from. */
  starters: string[];
  disabled?: boolean;
}

/**
 * The first view of a dashboard with nothing of the reader's own on it yet: an invitation to ask, with a few
 * questions to begin with. The base page (figures, flow map, overview chart) is one switch away.
 */
export const Welcome: React.FC<Props> = ({ starters, disabled }) => (
  <section className="wl" aria-label="Start your dashboard">
    <div className="wl-start">
      <span className="wl-plus" aria-hidden="true">+</span>
      <div className="wl-start-body">
        <h2 className="wl-start-title">Start your dashboard</h2>
        <p className="wl-start-text">
          Ask a question in the panel on the right. The answer is drawn here as a chart, and the charts you keep make up your dashboard.
          {starters.length > 0 && " Or begin with one of these:"}
        </p>
        {starters.length > 0 && (
          <div className="wl-chips">
            {starters.map((q) => (
              <button key={q} type="button" className="wl-chip" disabled={disabled} onClick={() => askTheData(q)}>
                {q}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  </section>
);
