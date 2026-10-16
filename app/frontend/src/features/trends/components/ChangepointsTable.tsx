// SPDX-License-Identifier: MIT
import React from "react";
import type { Changepoint } from "../api";
import { money, periodLabel, signedPercent } from "../format";

interface Props {
  changepoints: Changepoint[];
  unitSymbol?: string;
}

/** The change points of the chart above, numbered as its rules are. */
export const ChangepointsTable: React.FC<Props> = ({ changepoints, unitSymbol = "€" }) => {
  if (changepoints.length === 0) {
    return (
      <p className="tr-quiet">
        No lasting shift in level over this period at the current sensitivity. Move the sensitivity towards
        "more", or widen the period.
      </p>
    );
  }
  return (
    <table className="tr-table">
      <caption style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
        Change points: the average level before and after each shift
      </caption>
      <thead>
        <tr>
          <th scope="col">Around</th>
          <th scope="col" className="tr-num">Level before</th>
          <th scope="col" className="tr-num">Level after</th>
          <th scope="col" className="tr-num">Change</th>
        </tr>
      </thead>
      <tbody>
        {changepoints.map((cp, i) => (
          <tr key={cp.period}>
            <th scope="row">
              <span className="tr-cp-n" aria-label={`Change point ${i + 1},`}>{i + 1}</span>
              {periodLabel(cp.period)}
            </th>
            <td className="tr-num">{money(cp.before_mean, unitSymbol)}</td>
            <td className="tr-num">{money(cp.after_mean, unitSymbol)}</td>
            <td className="tr-num" style={{ fontWeight: 700 }}>{signedPercent(cp.relative_change)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};
