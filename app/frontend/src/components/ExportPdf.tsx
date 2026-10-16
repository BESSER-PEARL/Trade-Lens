import React, { useState } from "react";
import { openMadeForPrint, restoreMadeAfterPrint } from "../view/MadeCard";
import { exportNoteOf } from "../view/personaCards";
import { APP_NAME, DASHBOARD_AGENTS } from "../view/branding";
import "./ExportPdf.css";

const today = () => new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
const isoToday = () => new Date().toISOString().slice(0, 10);

/**
 * Exports the page as a PDF through the browser's print dialog ("Save as PDF"), so the charts stay sharp
 * vector drawings. Before it prints it opens every chart's "How this was made" card, the statisticians'
 * fold included, so the printout carries where the data was pulled from and how each figure was
 * calculated; afterwards the cards go back as the reader had them. What is printed is what is on the page.
 */
export const ExportPdfButton: React.FC<{ disabled?: boolean; ids: string[] }> = ({ disabled, ids }) => {
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (busy) return;
    setBusy(true);
    const title = document.title;
    document.title = `${APP_NAME} dashboard ${isoToday()}`;
    openMadeForPrint(ids);
    const done = () => {
      window.removeEventListener("afterprint", done);
      restoreMadeAfterPrint();
      document.title = title;
      setBusy(false);
    };
    window.addEventListener("afterprint", done);
    // The cards fetch product names and the charts redraw at the page width of the printout.
    await new Promise((r) => setTimeout(r, 1800));
    window.print();
  };

  return (
    <button
      type="button"
      className="cx-pctl-btn cx-export"
      disabled={disabled || busy}
      onClick={run}
      title="Save the page as a PDF, with where each figure comes from and how it was calculated"
    >
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false" style={{ flex: "none" }}>
        <path d="M8 2v8M4.8 7.2 8 10.4l3.2-3.2M3 13h10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span>{busy ? "Preparing the PDF…" : "Export PDF"}</span>
    </button>
  );
};

/** The top of the printout: what it is, for whom, when, and the reader's note. Not shown on screen. */
export const PrintHead: React.FC<{ reader?: string; readerId?: string }> = ({ reader, readerId }) => (
  <div className="cx-print-only cx-print-head">
    <p className="cx-print-brand">{APP_NAME}</p>
    <p className="cx-print-meta">
      Exported {today()}
      {reader ? ` for ${reader}` : ""}
    </p>
    <p className="cx-print-note">{exportNoteOf(readerId)}</p>
  </div>
);

/** The end of the printout: the data and its limits. Not shown on screen. */
export const PrintSource: React.FC = () => (
  <footer className="cx-print-only cx-print-source">
    <p>
      Source: Eurostat Comext, dataset DS-045409 (EU trade since 1988 by HS2-4-6 and CN8), nominal euros, latest months provisional. Luxembourg reports on its own
      from 1999. Eurostat's Community concept counts goods that only pass through Luxembourg (quasi-transit); STATEC's national
      figures leave them out and so differ. Every figure is computed from this data by a fixed method; the {DASHBOARD_AGENTS} select predefined tools and explain the results.
    </p>
    <p>Generated with {APP_NAME}, built with BESSER.</p>
  </footer>
);
