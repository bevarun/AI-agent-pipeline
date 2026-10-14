import React from "react";
import { CATEGORY_META, initials } from "../api.js";

export function CategoryBadge({ category }) {
  const meta = CATEGORY_META[category] ?? { label: category, tone: "low" };
  return (
    <span className={`badge badge--${meta.tone}`}>
      <span className="badge__dot" />
      {meta.label}
    </span>
  );
}

export function Avatar({ name, source, size = "md" }) {
  return (
    <div className={`avatar avatar--${size} avatar--${source}`} aria-hidden="true">
      {initials(name)}
    </div>
  );
}

export function ScoreBar({ score }) {
  const tone = score >= 75 ? "high" : score >= 45 ? "mid" : "low";
  return (
    <div className="score" title={`Score ${score}/100`}>
      <div className="score__track">
        <div className={`score__fill score__fill--${tone}`} style={{ width: `${score}%` }} />
      </div>
      <span className="score__num">{score}</span>
    </div>
  );
}

export function SourceTag({ source }) {
  return (
    <span className={`tag tag--${source}`}>{source === "linkedin_csv" ? "LinkedIn lead" : "EMR patient"}</span>
  );
}
