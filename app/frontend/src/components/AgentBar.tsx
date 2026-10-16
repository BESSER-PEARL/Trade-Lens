// The compact row of the agents: when the agent cards at the top of "Ask the data" are out of sight
// (the page or the panel scrolled), a slim sticky row keeps the agents in view: a dot in each pen and
// a short name; the one at work is lit in its pen with a subtle pulse. Beside the page it sticks to the
// top of the panel; on narrower screens, to the top of the window. Clicking an agent shows what it can
// do, with a link to the question box.

import React, { CSSProperties, useEffect, useState } from "react";
import type { AgentInfo } from "../view/types";

/** "Trend finder" -> "Trend"; "Forecaster" stays. */
export function shortTitle(title: string): string {
  return title.replace(/\s+finder$/i, "");
}

interface Props {
  agents: AgentInfo[];
  /** The registry name of the agent at work, if any. */
  working?: string;
  /** True when at least one agent is registered (the others are shown as not running). */
  anyOnline: boolean;
  /** The agent cards; the row shows while they are out of sight. */
  cards: React.RefObject<HTMLElement | null>;
  /** The id of the question input. */
  inputId: string;
  pen: (a: AgentInfo) => CSSProperties;
}

export const AgentBar: React.FC<Props> = ({ agents, working, anyOnline, cards, inputId, pen }) => {
  const [out, setOut] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const el = cards.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    // Out of sight: less than a fifth of the cards visible, in the window and in the scrolled panel.
    const io = new IntersectionObserver(([e]) => setOut(!e.isIntersecting || e.intersectionRatio < 0.2), {
      threshold: [0, 0.2, 0.5],
    });
    io.observe(el);
    return () => io.disconnect();
  }, [cards]);

  useEffect(() => {
    if (!out) setOpen(null);
  }, [out]);

  const shown = agents.find((a) => a.name === open);
  const toQuestion = () => {
    const input = document.getElementById(inputId) as HTMLInputElement | null;
    if (!input) return;
    const reduce = !!window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    input.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    input.focus({ preventScroll: true });
    setOpen(null);
  };

  return (
    <div className={`ask-bar${out ? " ask-bar--on" : ""}`} aria-hidden={!out}>
      <div className="ask-bar-inner">
        <ul className="ask-bar-row" aria-label="Agents">
          {agents.map((a) => {
            const on = working === a.name;
            const off = anyOnline && !a.online;
            return (
              <li key={a.name}>
                <button
                  type="button"
                  className={`ask-bar-agent${on ? " ask-bar-agent--on" : ""}${off ? " ask-bar-agent--off" : ""}`}
                  style={pen(a)}
                  tabIndex={out ? 0 : -1}
                  aria-expanded={open === a.name}
                  aria-current={on ? "true" : undefined}
                  title={`${a.title}: ${a.can}${on ? " (working on your question)" : off ? " (not running)" : ""}`}
                  onClick={() => setOpen(open === a.name ? null : a.name)}
                >
                  <span className="ask-bar-dot" aria-hidden="true" />
                  {shortTitle(a.title)}
                  {on && <span className="ask-bar-sr"> is working on your question</span>}
                </button>
              </li>
            );
          })}
        </ul>
        {shown && (
          <p className="ask-bar-can" style={pen(shown)}>
            <span>
              <strong>{shown.title}.</strong> {/[.!?]$/.test(shown.can) ? shown.can : `${shown.can}.`}
            </span>
            <button type="button" className="ask-bar-ask" tabIndex={out ? 0 : -1} onClick={toQuestion}>
              Ask a question
            </button>
          </p>
        )}
      </div>
    </div>
  );
};
