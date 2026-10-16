// The quiet controls in a panel's header: a pin ("Keep this chart"), which sends pin_panel as the user;
// "Remove", which sends remove_panel as the user, on every panel, the page's own included (the page can be
// emptied; "Back to the starting page" restores it), hidden while the panel is kept; and "How this was
// made", which opens the card under the chart. A pinned panel is never replaced, changed or removed by an
// agent (docs/agents.md, "The agents' area"); the agents keep three charts on the page and a new one takes
// the place of the oldest one not kept.

import React from "react";
import { TOKENS } from "./tokens";
import { useViewStore } from "./ViewStore";
import { MadeToggle } from "./MadeCard";
import { ModelCardToggle } from "./ModelCard";
import type { Panel as PanelSpec, ViewResponse } from "./types";

const PinIcon: React.FC<{ filled: boolean }> = ({ filled }) => (
  <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false" style={{ flex: "none" }}>
    <path
      d="M5.5 1.75h5l-.9 4.1 2.65 2.4v1.25H8.6V14.5L8 15.25l-.6-.75V9.5H3.75V8.25L6.4 5.85z"
      fill={filled ? TOKENS.ink : "none"}
      stroke={TOKENS.ink}
      strokeWidth="1.3"
      strokeLinejoin="round"
    />
  </svg>
);

function why(r: ViewResponse): string {
  const e = r.errors && r.errors[0];
  if (e && e.code === "pinned") return "It is kept: unpin it first.";
  return (e && e.message) || r.detail || "The page did not take it; try again.";
}

/** The pin toggle (every panel) and Remove (not while the panel is pinned or carries a kept note). */
export const PanelControls: React.FC<{ panel: PanelSpec; noun?: string }> = ({ panel, noun = "chart" }) => {
  const { spec, send, fallback } = useViewStore();
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const pinned = !!panel.pinned;
  // Every panel the person has not kept can go, the page's own included (its marks go with it). As
  // "Clear the page", a panel that carries a note the person kept stays: remove the note first.
  const keptNote = !!spec?.marks.some((m) => m.target === panel.id && m.status === "kept");
  const removable = !pinned && !keptNote;
  const off = busy || fallback;

  const act = async (ops: any[], message: string) => {
    setBusy(true);
    setError(null);
    const r = await send(ops, { message, baseVersion: null });
    setBusy(false);
    if (!r.ok) setError(why(r));
  };
  const name = panel.title ? `“${panel.title}”` : `this ${noun}`;

  return (
    <span className="cx-pctl">
      <button
        type="button"
        className={`cx-pctl-btn${pinned ? " cx-pctl-btn--on" : ""}`}
        aria-pressed={pinned}
        disabled={off}
        title={pinned ? "Kept: agents will not change it. Click to let them replace it again." : `Keep this ${noun}: agents will not change or replace it`}
        onClick={() => act([{ op: "pin_panel", id: panel.id, pinned: !pinned }], pinned ? `Unpinned ${name}` : `Kept ${name}`)}
      >
        <PinIcon filled={pinned} />
        <span className="cx-pctl-label">{pinned ? "Kept: agents will not change it" : `Keep this ${noun}`}</span>
        {pinned && (
          <span className="cx-pctl-short" aria-hidden="true">
            Kept
          </span>
        )}
      </button>
      {removable && (
        <button
          type="button"
          className="cx-pctl-btn"
          disabled={off}
          title={`Remove ${name} from the page`}
          aria-label={`Remove ${name}`}
          onClick={() => act([{ op: "remove_panel", id: panel.id, force: true }], `Removed ${name}`)}
        >
          Remove
        </button>
      )}
      <MadeToggle id={panel.id} />
      <ModelCardToggle panel={panel} />
      {error && (
        <span className="cx-pctl-error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
};
