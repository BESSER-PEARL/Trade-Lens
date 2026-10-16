// SPDX-License-Identifier: MIT
import React from "react";
import type { TrendAnalysis } from "../api";
import { money, percent, periodLabel, signedPercent } from "../format";
import { DIRECTION_LABEL, SHAPE_LABEL } from "../styles";

interface Props {
  analysis: TrendAnalysis;
  unitSymbol?: string;
}

/** "+48%", or "15 times as high" when the trend ended at three times its start or more. */
function changeText(rel: number | null | undefined): string | null {
  if (typeof rel !== "number" || !Number.isFinite(rel)) return null;
  const ratio = 1 + rel;
  if (ratio >= 3) return `${Math.round(ratio)} times as high`;
  return signedPercent(rel);
}

/** Four figures across the top, under a rule, like the figures of the Overview page. */
export const SummaryPanel: React.FC<Props> = ({ analysis, unitSymbol = "€" }) => {
  const monthly = /^\d{4}-\d{2}$/.test(analysis.start);
  const unitName = monthly ? "months" : "periods";
  const label = analysis.seasonality_label;
  return (
    <section className="tr-figures" aria-label="Summary of the series">
      <div className="tr-figure">
        <span className="tr-figure-label">Direction</span>
        <span className="tr-figure-value">{DIRECTION_LABEL[analysis.direction] ?? analysis.direction}</span>
        <p className="tr-figure-note">
          {analysis.direction !== "volatile" && SHAPE_LABEL[analysis.shape] && (
            <strong style={{ color: "var(--ink)", fontWeight: 600 }}>
              {SHAPE_LABEL[analysis.shape].charAt(0).toUpperCase() + SHAPE_LABEL[analysis.shape].slice(1)}.{" "}
            </strong>
          )}
          {typeof analysis.trend_start === "number" && typeof analysis.trend_end === "number" ? (
            <>
              The trend went from {money(analysis.trend_start, unitSymbol)}{monthly ? " a month" : ""} in {periodLabel(analysis.start)} to{" "}
              {money(analysis.trend_end, unitSymbol)} in {periodLabel(analysis.end)}
              {changeText(analysis.relative_change) ? ` (${changeText(analysis.relative_change)})` : ""}
            </>
          ) : (
            <>
              The trend moved {signedPercent(analysis.relative_change)} from {periodLabel(analysis.start)} to {periodLabel(analysis.end)}
            </>
          )}
        </p>
      </div>
      <div className="tr-figure">
        <span className="tr-figure-label">Trend strength</span>
        <span className="tr-figure-value">{percent(analysis.trend_strength, 0)}</span>
        <p className="tr-figure-note">of the movement follows the trend rather than noise</p>
      </div>
      <div className="tr-figure">
        <span className="tr-figure-label">Seasonality</span>
        <span className="tr-figure-value">{label ? label.charAt(0).toUpperCase() + label.slice(1) : "None"}</span>
        <p className="tr-figure-note">
          {analysis.seasonal_period
            ? `${percent(analysis.seasonal_strength, 0)} of the movement repeats every ${analysis.seasonal_period} ${unitName}`
            : "No seasonal cycle in an annual or short series"}
        </p>
      </div>
      <div className="tr-figure">
        <span className="tr-figure-label">Change points</span>
        <span className="tr-figure-value">{analysis.changepoints.length}</span>
        <p className="tr-figure-note">
          shifts in level across {analysis.n_points} {unitName}
        </p>
      </div>
    </section>
  );
};
