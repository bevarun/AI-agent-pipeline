import React from "react";
import { STATUS_META, formatTime } from "../../api.js";

export function StatusPill({ status }) {
  const meta = STATUS_META[status] ?? { label: status ?? "…", tone: "muted" };
  return (
    <span className={`status status--${meta.tone}`}>
      <i aria-hidden="true" />
      {meta.label}
    </span>
  );
}

function jobTitle(job) {
  const parts = [];
  if (job.input?.filename) parts.push(job.input.filename);
  if (job.input?.fhir_base_url) parts.push(new URL(job.input.fhir_base_url).hostname);
  return parts.join(" + ") || "Pipeline run";
}

export default function JobList({ jobs, selectedId, onSelect, onNew, creating, error }) {
  return (
    <aside className="joblist" aria-label="Run history">
      <button id="new-run-btn" className={`joblist__new ${creating ? "is-active" : ""}`} onClick={onNew}>
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" fill="currentColor" />
        </svg>
        New run
      </button>
      <h3 className="joblist__title">History</h3>
      {error && <p className="joblist__empty">{error}</p>}
      {!error && jobs.length === 0 && <p className="joblist__empty">No runs yet. Start one to see it here.</p>}
      <ul>
        {jobs.map((j) => {
          const p = j.progress || {};
          const pct = p.total ? Math.round(((p.processed || 0) / p.total) * 100) : j.status === "succeeded" ? 100 : 0;
          return (
            <li key={j.id}>
              <button
                id={`job-${j.id}`}
                className={`jobitem ${selectedId === j.id ? "is-selected" : ""}`}
                onClick={() => onSelect(j.id)}
              >
                <div className="jobitem__top">
                  <StatusPill status={j.status} />
                  <time>{formatTime(j.created_at)}</time>
                </div>
                <strong className="jobitem__name" title={jobTitle(j)}>{jobTitle(j)}</strong>
                <div className="jobitem__bar">
                  <span className={`jobitem__fill jobitem__fill--${j.status}`} style={{ width: `${pct}%` }} />
                </div>
                <div className="jobitem__stats">
                  <span>{p.processed || 0}/{p.total || 0} processed</span>
                  {p.failed > 0 && <span className="t-bad">{p.failed} failed</span>}
                  {p.rejected > 0 && <span className="t-warn">{p.rejected} rejected</span>}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
