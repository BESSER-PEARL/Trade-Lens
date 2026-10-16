// SPDX-License-Identifier: MIT
import React from "react";

/** The cause finder's agent name, as the agents and the marks carry it. */
export const CAUSE_FINDER = "cause_finder";

/** Whatever the cause finder writes is a hypothesis: it lines dated events up with the data, which is not proof. */
export const AiHypothesis: React.FC<{ style?: React.CSSProperties }> = ({ style }) => (
  <span
    className="ai-hypothesis"
    style={style}
    title="The cause finder lines dated events up with the data. A date that lines up is not proof of cause."
  >
    AI Hypothesis
  </span>
);
