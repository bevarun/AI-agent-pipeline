import React, { useEffect, useMemo, useRef, useState } from "react";
import { CHANNEL_LABEL, TERMINAL_STATUSES, formatBytes, formatDuration, formatTime, jobsApi } from "../../api.js";
import { useJobStream } from "../../hooks/useJobStream.js";
import { Avatar, CategoryBadge, SourceTag } from "../Badges.jsx";
import ErrorReport from "./ErrorReport.jsx";
import { StatusPill } from "./JobList.jsx";

const STAGES = [
  ["ingest_csv", "CSV ingest"],
  ["ingest_fhir", "FHIR ingest"],
  ["process", "Agent & delivery"],
];

const STEP_LABEL = {
  perceive: "Perceive",
  triage: "Triage (LLM)",
  triage_failed: "Triage retry",
  triage_fallback: "Rule-based triage",
  guardrails_override: "Guardrails",
  compose_skipped: "No outreach",
  compose_failed: "Compose failed",
  compose_template: "Template draft",
  critique: "Critique",
  compose_safe_template: "Safe template",
};
const BAD_STEPS = new Set(["triage_failed", "compose_failed"]);
const WARN_STEPS = new Set(["triage_fallback", "guardrails_override", "compose_safe_template", "compose_template"]);

function stepDetail(s) {
  if (s.error) return s.error;
  if (s.step === "triage" || s.step === "triage_fallback") return `${s.category} · ${s.score} · ${s.channel}`;
  if (s.step === "critique") return Array.isArray(s.issues) ? s.issues.join("; ") : s.issues;
  if (s.step === "guardrails_override") return (s.changes || []).join("; ");
  if (s.step === "perceive") return `pre-score ${s.score_hint}`;
  return s.reason || "";
}

function StageIcon({ status }) {
  if (status === "running") return <span className="spinner spinner--sm" />;
  if (status === "done") return <span className="stage__ico stage__ico--ok">✓</span>;
  if (status === "failed") return <span className="stage__ico stage__ico--bad">!</span>;
  if (status === "skipped") return <span className="stage__ico">–</span>;
  if (status === "cancelled") return <span className="stage__ico stage__ico--bad">×</span>;
  return <span className="stage__ico stage__ico--idle" />;
}

function useTicker(active) {
  const [, setT] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setT((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
}

function RecordRow({ r, open, onToggle }) {
  const status = r.status === "processing" ? (
    <span className="rstate rstate--live"><span className="spinner spinner--xs" /> Processing</span>
  ) : r.status === "done" ? (
    <span className="rstate rstate--ok">✓ Delivered</span>
  ) : (
    <span className="rstate rstate--bad">✕ Failed</span>
  );
  const lastStep = r.steps[r.steps.length - 1];
  return (
    <>
      <tr className={`row ${open ? "is-selected" : ""}`} onClick={onToggle} id={`run-record-${r.record_id}`}>
        <td className="mono muted">{r.index}</td>
        <td>
          <div className="person">
            <Avatar name={r.name} source={r.source} />
            <div className="person__text">
              <span className="person__name">{r.name}</span>
              <span className="person__sub"><SourceTag source={r.source} /></span>
            </div>
          </div>
        </td>
        <td>{status}</td>
        <td>
          {r.category ? <CategoryBadge category={r.category} /> : (
            <span className="muted">{lastStep ? STEP_LABEL[lastStep.step] ?? lastStep.step : "Starting…"}</span>
          )}
        </td>
        <td className="mono">{r.score ?? "—"}</td>
        <td>{r.channel ? CHANNEL_LABEL[r.channel] ?? r.channel : "—"}</td>
        <td>
          {r.llm_used === undefined ? "—" : r.llm_used ? <span className="pill pill--ok">LLM</span> : <span className="pill pill--warn">Fallback</span>}
        </td>
        <td className="td-end mono">{r.delivery ? `${r.delivery.attempts}×` : "—"}</td>
      </tr>
      {open && (
        <tr className="row-detail">
          <td colSpan={8}>
            {r.error && <div className="alert alert--bad">{r.stage === "delivery" ? "Delivery failed" : "Agent error"} · {r.error}</div>}
            <ol className="timeline">
              {r.steps.map((s, i) => (
                <li key={i} className={BAD_STEPS.has(s.step) ? "is-bad" : WARN_STEPS.has(s.step) ? "is-warn" : ""}>
                  <span className="timeline__dot" />
                  <strong>{STEP_LABEL[s.step] ?? s.step}{s.attempt > 1 ? ` #${s.attempt}` : ""}</strong>
                  <span className="muted">{stepDetail(s)}</span>
                  <time>{new Date(s.at).toLocaleTimeString()}</time>
                </li>
              ))}
              {r.delivery && (
                <li className={r.delivery.status === "delivered" ? "is-ok" : "is-bad"}>
                  <span className="timeline__dot" />
                  <strong>CMS delivery</strong>
                  <span className="muted">
                    {r.delivery.status} after {r.delivery.attempts} attempt(s)
                    {r.delivery.cms_id ? ` · cms_id ${r.delivery.cms_id}` : ""}
                  </span>
                </li>
              )}
            </ol>
          </td>
        </tr>
      )}
    </>
  );
}

function Artifacts({ jobId, terminal }) {
  const [items, setItems] = useState(null);
  useEffect(() => {
    jobsApi.artifacts(jobId).then(setItems).catch(() => setItems([]));
  }, [jobId, terminal]);
  if (!items) return <div className="skeleton skeleton--sm" />;
  if (!items.length) return <p className="muted pad">No artifacts yet.</p>;
  return (
    <ul className="artifacts">
      {items.map((a) => (
        <li key={a.name}>
          <span className="file-icon file-icon--sm" aria-hidden="true">{a.name.split(".").pop().toUpperCase()}</span>
          <span className="artifacts__name">{a.name}</span>
          <span className="muted">{formatBytes(a.size)}</span>
          <a id={`artifact-${a.name}`} className="btn btn--ghost" href={jobsApi.artifactUrl(jobId, a.name)} download>
            Download
          </a>
        </li>
      ))}
    </ul>
  );
}

export default function JobDetail({ jobId, onTerminal }) {
  const s = useJobStream(jobId, onTerminal);
  const [tab, setTab] = useState("records");
  const [openId, setOpenId] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const logRef = useRef(null);
  const job = s.job;
  const terminal = job ? TERMINAL_STATUSES.includes(job.status) : false;
  useTicker(job?.status === "running");

  useEffect(() => {
    setTab("records");
    setOpenId(null);
  }, [jobId]);

  useEffect(() => {
    const el = logRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [s.activity.length, tab]);

  const records = useMemo(() => [...s.order].reverse().map((id) => s.records[id]), [s.order, s.records]);

  if (s.error && !job) return <div className="alert alert--bad">{s.error}</div>;
  if (!job) return <div className="skeleton-wrap"><div className="skeleton" /><div className="skeleton" /></div>;

  const p = job.progress || {};
  const total = p.total || 0;
  const pctOk = total ? (p.delivered / total) * 100 : 0;
  const pctBad = total ? (p.failed / total) * 100 : 0;
  const issues = (p.failed || 0) + (p.rejected || 0) + (p.warnings || 0);
  const stages = job.stages || {};
  const current = job.current ? s.records[job.current.record_id] : null;
  const currentStep = current?.steps[current.steps.length - 1];

  const cancel = async () => {
    setCancelling(true);
    try {
      await jobsApi.cancel(jobId);
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="jobdetail">
      <header className="jobhead">
        <div className="jobhead__main">
          <div className="jobhead__row">
            <StatusPill status={job.status} />
            <code className="muted">job {job.id}</code>
            <span className={`conn conn--${s.conn}`} title="Live update connection">
              <i aria-hidden="true" /> {s.conn === "live" ? "Live" : s.conn === "reconnecting" ? "Reconnecting…" : s.conn === "closed" ? "Final" : "Connecting…"}
            </span>
          </div>
          <h2>
            {job.input?.filename ?? "No CSV"}
            {job.input?.fhir_base_url && <span className="muted"> + {job.input.fhir_base_url.replace(/^https?:\/\//, "")}</span>}
          </h2>
          <p className="muted">
            Created {formatTime(job.created_at)} · Duration {formatDuration(job.started_at, job.finished_at)}
            {job.config?.limit > 0 && ` · limit ${job.config.limit}`}
            {job.config?.chaos && " · chaos mode"}
          </p>
        </div>
        {!terminal && (
          <button id="cancel-run-btn" className="btn btn--danger" onClick={cancel} disabled={cancelling || job.cancel_requested}>
            {job.cancel_requested ? "Stopping…" : "Cancel run"}
          </button>
        )}
      </header>

      {job.error && <div className="alert alert--bad"><strong>Run failed</strong> · {job.error}</div>}

      <section className="progress-card">
        <div className="progress-card__top">
          <div>
            <span className="progress-card__big">{p.processed || 0}</span>
            <span className="muted"> / {total || "—"} records</span>
          </div>
          <div className="kpis">
            <span className="kpi kpi--ok"><b>{p.delivered || 0}</b> delivered</span>
            <span className="kpi kpi--bad"><b>{p.failed || 0}</b> failed</span>
            <span className="kpi kpi--warn"><b>{p.rejected || 0}</b> rejected</span>
            <span className="kpi"><b>{p.warnings || 0}</b> warnings</span>
          </div>
        </div>
        <div className={`pbar ${job.status === "running" ? "is-live" : ""}`} role="progressbar"
          aria-valuemin={0} aria-valuemax={total} aria-valuenow={p.processed || 0}>
          <span className="pbar__ok" style={{ width: `${pctOk}%` }} />
          <span className="pbar__bad" style={{ width: `${pctBad}%` }} />
        </div>
        <div className="progress-card__now">
          {job.status === "queued" && <span className="muted">Waiting for the worker — another run may be in progress.</span>}
          {job.status === "running" && !job.current && (
            <span className="muted">{stages.process ? "Finalising…" : "Ingesting sources…"}</span>
          )}
          {job.status === "running" && job.current && (
            <span>
              <span className="spinner spinner--xs" /> Processing <b>{job.current.index}/{total}</b> · {job.current.name}
              {currentStep && <span className="muted"> · {STEP_LABEL[currentStep.step] ?? currentStep.step}</span>}
            </span>
          )}
          {terminal && job.summary && (
            <span className="muted">
              Finished in {job.summary.duration_s}s · {job.summary.llm_calls} LLM calls · tokens {job.summary.tokens_in}/{job.summary.tokens_out} ·{" "}
              {job.summary.llm} LLM / {job.summary.fallback} fallback decisions
            </span>
          )}
        </div>

        <ol className="stages">
          {STAGES.map(([key, label]) => {
            const st = stages[key] || {};
            return (
              <li key={key} className={`stage stage--${st.status || "pending"}`}>
                <StageIcon status={st.status} />
                <div>
                  <strong>{label}</strong>
                  <small>
                    {st.status === "failed"
                      ? st.error
                      : st.accepted !== undefined
                        ? `${st.accepted} accepted · ${st.rejected} rejected`
                        : st.status === "done" && key === "process"
                          ? `${st.delivered} delivered · ${st.failed} failed`
                          : st.status === "running" && key === "process"
                            ? `${st.total} records${st.llm_available ? "" : " · fallback mode"}`
                            : st.status ?? "pending"}
                  </small>
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="panel">
        <div className="toolbar">
          <div className="seg" role="tablist" aria-label="Run detail">
            {[
              ["records", "Records", s.order.length],
              ["activity", "Activity", s.activity.length],
              ["issues", "Issues", issues],
              ["artifacts", "Artifacts", null],
            ].map(([k, label, n]) => (
              <button key={k} id={`run-tab-${k}`} role="tab" aria-selected={tab === k}
                className={`seg__btn ${tab === k ? "is-active" : ""}`} onClick={() => setTab(k)}>
                {label}
                {n !== null && <span className={`seg__count ${k === "issues" && n ? "seg__count--warn" : ""}`}>{n}</span>}
              </button>
            ))}
          </div>
          {terminal && (
            <span className="muted small">Results are in the CMS — see the Records dashboard.</span>
          )}
        </div>

        {tab === "records" && (
          records.length ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    {["#", "Record", "Status", "Decision", "Score", "Channel", "Engine", "Tries"].map((h, i) => (
                      <th key={h} className={i === 7 ? "th-end" : ""}><span className="th-static">{h}</span></th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {records.map((r) => (
                    <RecordRow key={r.record_id} r={r} open={openId === r.record_id}
                      onToggle={() => setOpenId((o) => (o === r.record_id ? null : r.record_id))} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted pad">{terminal ? "No records were processed." : "Records appear here as the agent picks them up…"}</p>
          )
        )}

        {tab === "activity" && (
          <ul className="loglines loglines--scroll" ref={logRef}>
            {s.activity.map((l) => (
              <li key={l.seq} className={`log log--${l.level}`}>
                <time>{new Date(l.ts).toLocaleTimeString()}</time>
                <span className="log__msg">{l.text}</span>
              </li>
            ))}
            {!s.activity.length && <li className="muted">Waiting for events…</li>}
          </ul>
        )}

        {tab === "issues" && (
          <div className="pad">
            <ErrorReport jobId={jobId} terminal={terminal}
              liveCounts={{ failed: p.failed || 0, rejected: p.rejected || 0, warnings: p.warnings || 0 }} />
          </div>
        )}

        {tab === "artifacts" && <div className="pad"><Artifacts jobId={jobId} terminal={terminal} /></div>}
      </section>
    </div>
  );
}
