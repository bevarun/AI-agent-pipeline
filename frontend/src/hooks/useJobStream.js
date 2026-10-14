import { useEffect, useReducer, useRef } from "react";
import { jobsApi, streamJob } from "../api.js";

const MAX_ACTIVITY = 400;

const initial = {
  job: null,
  records: {}, // record_id -> { index, name, source, status, steps[], ...result }
  order: [], // record ids in processing order
  rejected: [],
  activity: [], // stage/status/log lines
  conn: "connecting", // connecting | live | reconnecting | closed
  error: "",
  lastSeq: 0,
};

const STAGE_LABEL = { ingest_csv: "CSV ingest", ingest_fhir: "FHIR ingest", process: "Agent & delivery" };

function activityLine(ev) {
  const { type, data, ts, seq } = ev;
  if (type === "stage") {
    const extra = data.error
      ? ` — ${data.error}`
      : data.accepted !== undefined
        ? ` — ${data.accepted} accepted, ${data.rejected} rejected`
        : "";
    return { seq, ts, level: data.status === "failed" ? "error" : "stage", text: `${STAGE_LABEL[data.stage] ?? data.stage}: ${data.status}${extra}` };
  }
  if (type === "status") return { seq, ts, level: data.status === "failed" ? "error" : "status", text: `Job ${data.status.replaceAll("_", " ")}${data.error ? ` — ${data.error}` : ""}` };
  if (type === "plan") return { seq, ts, level: "stage", text: `Plan: ${data.total} records (${data.leads} leads, ${data.patients} patients)` };
  if (type === "log") return { seq, ts, level: data.level, text: `${data.logger} · ${data.message}` };
  if (type === "rejected") return { seq, ts, level: "warning", text: `Rejected ${data.source}${data.line ? ` line ${data.line}` : ""}: ${data.error}` };
  if (type === "record_failed") return { seq, ts, level: "error", text: `${data.name} failed at ${data.stage}: ${data.error}` };
  if (type === "record_done") return { seq, ts, level: "ok", text: `${data.name} → ${data.category} (${data.score}) via ${data.channel}${data.llm_used ? "" : " · fallback"}` };
  return null;
}

function applyEvent(s, ev) {
  if (ev.seq && ev.seq <= s.lastSeq) return s; // duplicate after reconnect
  const next = { ...s, lastSeq: ev.seq ?? s.lastSeq };
  if (ev.job) next.job = { ...(s.job || {}), ...ev.job };
  const d = ev.data || {};
  switch (ev.type) {
    case "stage":
      next.job = { ...next.job, stages: { ...(next.job?.stages || {}), [d.stage]: d } };
      break;
    case "status":
      next.job = { ...next.job, status: d.status, error: d.error ?? next.job?.error };
      break;
    case "rejected":
      next.rejected = [...s.rejected, { ...d, ts: ev.ts }];
      break;
    case "record_started":
      next.records = { ...s.records, [d.record_id]: { ...d, status: "processing", steps: [] } };
      next.order = s.order.includes(d.record_id) ? s.order : [...s.order, d.record_id];
      break;
    case "agent_step": {
      const r = s.records[d.record_id];
      if (r) next.records = { ...s.records, [d.record_id]: { ...r, steps: [...r.steps, d] } };
      break;
    }
    case "record_done":
    case "record_failed": {
      const r = s.records[d.record_id] || { steps: [] };
      next.records = { ...s.records, [d.record_id]: { ...r, ...d, status: ev.type === "record_done" ? "done" : "failed" } };
      if (!s.order.includes(d.record_id)) next.order = [...s.order, d.record_id];
      break;
    }
    default:
      break;
  }
  const line = activityLine(ev);
  if (line) next.activity = [...s.activity, line].slice(-MAX_ACTIVITY);
  return next;
}

function reducer(s, a) {
  switch (a.type) {
    case "reset":
      return initial;
    case "job": {
      const fresh = (a.job.event_count ?? Infinity) >= s.lastSeq;
      return { ...s, job: fresh ? { ...(s.job || {}), ...a.job } : { ...a.job, ...(s.job || {}) } };
    }
    case "events":
      return a.events.reduce(applyEvent, s);
    case "conn":
      return { ...s, conn: a.conn };
    case "error":
      return { ...s, error: a.error };
    default:
      return s;
  }
}

/** Live view of one job: initial GET + SSE stream (replayed from the start, then live). */
export function useJobStream(jobId, onTerminal) {
  const [state, dispatch] = useReducer(reducer, initial);
  const buffer = useRef([]);
  const timer = useRef(null);
  const terminalCb = useRef(onTerminal);
  terminalCb.current = onTerminal;

  useEffect(() => {
    if (!jobId) return undefined;
    let alive = true;
    dispatch({ type: "reset" });

    const flush = () => {
      timer.current = null;
      if (!alive || !buffer.current.length) return;
      const events = buffer.current;
      buffer.current = [];
      dispatch({ type: "events", events });
    };

    jobsApi
      .get(jobId)
      .then((job) => alive && dispatch({ type: "job", job }))
      .catch((e) => alive && dispatch({ type: "error", error: e.message }));

    const stop = streamJob(jobId, {
      onEvent: (ev) => {
        dispatch({ type: "conn", conn: "live" });
        buffer.current.push(ev);
        if (!timer.current) timer.current = setTimeout(flush, 80);
      },
      onEnd: () => {
        flush();
        dispatch({ type: "conn", conn: "closed" });
        jobsApi.get(jobId).then((job) => {
          if (!alive) return;
          dispatch({ type: "job", job });
          terminalCb.current?.(job);
        });
      },
      onError: (readyState) => alive && dispatch({ type: "conn", conn: readyState === 2 ? "closed" : "reconnecting" }),
    });

    return () => {
      alive = false;
      stop();
      clearTimeout(timer.current);
      timer.current = null;
      buffer.current = [];
    };
  }, [jobId]);

  return state;
}
