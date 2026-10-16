import React, { useEffect, useRef, useState } from "react";
import { DynamicCanvas } from "../view/DynamicCanvas";
import { AiHypothesis } from "../view/AiHypothesis";
import { setNote, type PromptGroupData } from "../view/panelPrompts";
import { ReplyText } from "./AgentComponent";
import "./PromptGroup.css";

/**
 * Everything one question drew: the question once, its charts together, what the agents said about them
 * (folded away until asked for), and a note of the reader's own about what was done here.
 */
export const PromptGroup: React.FC<{ group: PromptGroupData }> = ({ group }) => {
  const { at, prompt, panelIds, replies, note } = group;
  const [more, setMore] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note);
  const box = useRef<HTMLTextAreaElement>(null);
  const charts = panelIds.length === 1 ? "this chart" : "these charts";
  const base = `group-${at}`;

  useEffect(() => {
    if (editing) box.current?.focus();
  }, [editing]);

  const open = () => {
    setDraft(note);
    setEditing(true);
  };
  const close = (keep: boolean) => {
    if (keep) setNote(at, draft);
    setEditing(false);
  };

  return (
    <article className="cx-group" aria-label={`Charts for: ${prompt}`}>
      <header className="cx-group-head">
        <p className="cx-asked" title={new Date(at).toLocaleString()}>
          <span className="cx-asked-label">You asked</span> {prompt}
        </p>
      </header>

      <DynamicCanvas rows={[{ panels: panelIds }]} ruled={false} />

      {(replies.length > 0 || note) && (
        <div className="cx-print-only cx-print-words">
          {replies.map((r, i) => (
            <div key={i} className="cx-print-reply">
              <span className="cx-asked-label">{replies.length > 1 ? r.agent : "About these charts"}</span>
              <ReplyText text={r.text} />
            </div>
          ))}
          {note && (
            <div className="cx-print-reply">
              <span className="cx-asked-label">Note</span>
              <p>{note}</p>
            </div>
          )}
        </div>
      )}

      <div className="cx-group-foot">
        {replies.length > 0 && (
          <div className="cx-more-about">
            <button type="button" className="cx-more-about-btn" aria-expanded={more} aria-controls={`${base}-more`} onClick={() => setMore((o) => !o)}>
              <span aria-hidden="true">{more ? "▾" : "▸"}</span> {more ? "Hide the explanation" : `More about ${charts}`}
            </button>
            {more && (
              <div id={`${base}-more`} className="cx-more-about-body">
                {replies.map((r, i) => (
                  <div key={i} className="cx-more-about-reply">
                    {replies.length > 1 && <span className="cx-asked-label">{r.agent}</span>}
                    {/^cause finder$/i.test(r.agent) && <AiHypothesis style={{ marginLeft: replies.length > 1 ? 8 : 0 }} />}
                    <ReplyText text={r.text} />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {editing ? (
          <div className="cx-note cx-note--edit">
            <label className="cx-note-label" htmlFor={`${base}-note`}>Your note</label>
            <textarea
              id={`${base}-note`}
              ref={box}
              className="cx-note-input"
              rows={3}
              value={draft}
              placeholder="What did you do here? What did you find?"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.stopPropagation();
                  close(false);
                }
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) close(true);
              }}
            />
            <div className="cx-note-actions">
              <button type="button" className="cx-btn cx-btn--ink" onClick={() => close(true)}>
                Save note
              </button>
              <button type="button" className="cx-pctl-btn" onClick={() => close(false)}>
                Cancel
              </button>
              {note && (
                <button
                  type="button"
                  className="cx-pctl-btn"
                  onClick={() => {
                    setNote(at, "");
                    setEditing(false);
                  }}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ) : note ? (
          <div className="cx-note">
            <span className="cx-note-label">Your note</span>
            <p className="cx-note-text">{note}</p>
            <button type="button" className="cx-more-about-btn" onClick={open}>
              Edit note
            </button>
          </div>
        ) : (
          <button type="button" className="cx-more-about-btn" onClick={open}>
            <span aria-hidden="true">+</span> Add a note
          </button>
        )}
      </div>
    </article>
  );
};
