import React, { useEffect, useState } from "react";
import { jobsApi } from "../../api.js";

const STAGE_LABEL = { ingest_csv: "CSV ingest", ingest_fhir: "FHIR ingest", process: "Agent & delivery" };

function rowSnippet(r) {
  if (r.row) {
    const entries = Object.entries(r.row).filter(([, v]) => v);
    return entries.slice(0, 4).map(([k, v]) => `${k}: ${v}`).join(" · ") || "(empty row)";
  }
  if (r.resource) return `${r.resource.resourceType ?? "?"} id=${r.resource.id ?? "(none)"}`;
  return "";
}

function Section({ id, title, count, tone, hint, children }) {
  const [open, setOpen] = useState(count > 0);
  useEffect(() => setOpen(count > 0), [count]);
  return (
    <section className={`issues__section ${count ? "" : "is-empty"}`}>
      <button id={`issues-${id}`} className="issues__head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className={`count count--${count ? tone : "none"}`}>{count}</span>
        <span className="issues__title">
          <strong>{title}</strong>
          <small>{hint}</small>
        </span>
        <span className={`chev ${open ? "is-open" : ""}`} aria-hidden="true">›</span>
      </button>
      {open && count > 0 && <div className="issues__body">{children}</div>}
    </section>
  );
}

export default function ErrorReport({ jobId, liveCounts, terminal }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  // Refetch whenever the live counters move (cheap: it's derived server-side from the event log).
  const key = `${liveCounts.failed}-${liveCounts.rejected}-${liveCounts.warnings}-${terminal}`;
  useEffect(() => {
    let alive = true;
    jobsApi
      .errors(jobId)
      .then((r) => alive && (setReport(r), setError("")))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [jobId, key]);

  if (error) return <div className="alert alert--bad">{error}</div>;
  if (!report) return <div className="skeleton skeleton--sm" />;

  const total = report.counts.sources + report.counts.rejected + report.counts.records + report.counts.warnings;

  return (
    <div className="issues">
      {report.error && (
        <div className="alert alert--bad">
          <strong>Run failed</strong> · {report.error}
        </div>
      )}
      {total === 0 && !report.error && (
        <div className="empty empty--inline">
          <div className="empty__icon empty__icon--ok">✓</div>
          <h3>No issues{terminal ? "" : " so far"}</h3>
          <p>Every ingested row was valid and every record was delivered to the CMS.</p>
        </div>
      )}

      <Section id="sources" title="Source failures" count={report.counts.sources} tone="bad"
        hint="A whole source could not be read; the run continued with the other source.">
        <ul className="issue-list">
          {report.sources.map((s, i) => (
            <li key={i}>
              <code>{STAGE_LABEL[s.stage] ?? s.stage}</code>
              <span className="issue-list__err">{s.error}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="records" title="Failed records" count={report.counts.records} tone="bad"
        hint="Agent crashed or the CMS rejected / never acknowledged the payload.">
        <table className="table table--compact">
          <thead>
            <tr><th><span className="th-static">#</span></th><th><span className="th-static">Record</span></th><th><span className="th-static">Stage</span></th><th><span className="th-static">Attempts</span></th><th><span className="th-static">Error</span></th></tr>
          </thead>
          <tbody>
            {report.records.map((r) => (
              <tr key={`${r.record_id}-${r.ts}`}>
                <td>{r.index}</td>
                <td><strong>{r.name}</strong><div className="muted mono">{r.record_id}</div></td>
                <td><span className="pill pill--bad">{r.stage}</span></td>
                <td>{r.delivery?.attempts ?? "—"}</td>
                <td className="issue-list__err">{r.error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section id="rejected" title="Rejected at ingest (dead-lettered)" count={report.counts.rejected} tone="warn"
        hint="Rows/resources that failed validation. They were never sent to the agent.">
        <table className="table table--compact">
          <thead>
            <tr><th><span className="th-static">Source</span></th><th><span className="th-static">Where</span></th><th><span className="th-static">Reason</span></th><th><span className="th-static">Data</span></th></tr>
          </thead>
          <tbody>
            {report.rejected.map((r, i) => (
              <tr key={i}>
                <td><span className={`tag tag--${r.source}`}>{r.source === "linkedin_csv" ? "CSV" : "FHIR"}</span></td>
                <td className="mono">{r.line > 0 ? `line ${r.line}` : r.line === -1 ? "injected" : "—"}</td>
                <td className="issue-list__err">{r.error}</td>
                <td className="muted ellipsis" title={rowSnippet(r)}>{rowSnippet(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <a className="link-btn" href={jobsApi.artifactUrl(jobId, "dead_letter.jsonl")} download>
          Download dead_letter.jsonl
        </a>
      </Section>

      <Section id="warnings" title="Warnings & retries" count={report.counts.warnings} tone="warn"
        hint="Transient problems the pipeline recovered from (timeouts, backoff, fallbacks).">
        <ul className="loglines">
          {report.warnings.map((w, i) => (
            <li key={i} className={`log log--${w.level}`}>
              <time>{new Date(w.ts).toLocaleTimeString()}</time>
              <span className="log__src">{w.logger}</span>
              {w.record_id && <code>{w.record_id}</code>}
              <span className="log__msg">{w.message}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
