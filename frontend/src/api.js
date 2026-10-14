export async function fetchRecords(signal) {
  const res = await fetch("/api/records", { signal });
  if (!res.ok) throw new Error(`CMS responded with HTTP ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- job API
async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`/api${path}`, options);
  } catch {
    throw new Error("API unreachable. Is `uvicorn server:app --port 8000` running?");
  }
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const detail = body?.detail;
    const generic404 = res.status === 404 && (!detail || detail === "Not Found");
    const msg = generic404
      ? "Job API not found. Restart the backend with `uvicorn server:app --port 8000` (not cms.app)."
      : typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ")
          : `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return body;
}

const json = (method, data) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export const jobsApi = {
  info: () => request("/pipeline/info"),
  samplePreview: () => request("/uploads/sample"),
  upload: (file) =>
    request(`/uploads?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": "text/csv" },
      body: file,
    }),
  testFhir: (base_url) => request("/sources/fhir/test", json("POST", { base_url })),
  create: (payload) => request("/jobs", json("POST", payload)),
  list: () => request("/jobs"),
  get: (id) => request(`/jobs/${id}`),
  cancel: (id) => request(`/jobs/${id}/cancel`, { method: "POST" }),
  errors: (id) => request(`/jobs/${id}/errors`),
  artifacts: (id) => request(`/jobs/${id}/artifacts`),
  artifactUrl: (id, name) => `/api/jobs/${id}/artifacts/${name}`,
};

/** Subscribe to a job's SSE stream. Returns an unsubscribe function. */
export function streamJob(id, { onEvent, onEnd, onError }) {
  const es = new EventSource(`/api/jobs/${id}/events`);
  es.onmessage = (msg) => {
    let ev;
    try {
      ev = JSON.parse(msg.data);
    } catch {
      return;
    }
    if (ev.type === "end") {
      es.close();
      onEnd?.(ev);
    } else {
      onEvent(ev);
    }
  };
  es.onerror = () => onError?.(es.readyState); // EventSource retries automatically (Last-Event-ID)
  return () => es.close();
}

export const TERMINAL_STATUSES = ["succeeded", "completed_with_errors", "failed", "cancelled", "interrupted"];

export const STATUS_META = {
  queued: { label: "Queued", tone: "muted" },
  running: { label: "Running", tone: "live" },
  succeeded: { label: "Succeeded", tone: "ok" },
  completed_with_errors: { label: "Completed with issues", tone: "warn" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "muted" },
  interrupted: { label: "Interrupted", tone: "bad" },
};

export function formatDuration(startIso, endIso) {
  if (!startIso) return "—";
  const ms = (endIso ? new Date(endIso) : new Date()) - new Date(startIso);
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function formatBytes(n = 0) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const SOURCE_LABEL = { linkedin_csv: "Lead", fhir_emr: "Patient" };

export const CATEGORY_META = {
  hot: { label: "Hot", tone: "hot" },
  warm: { label: "Warm", tone: "warm" },
  cold: { label: "Cold", tone: "cold" },
  urgent: { label: "Urgent", tone: "urgent" },
  routine: { label: "Routine", tone: "routine" },
  low: { label: "Low", tone: "low" },
};

// Order used for sorting by priority across both domains
export const CATEGORY_RANK = { urgent: 6, hot: 5, warm: 4, routine: 3, cold: 2, low: 1 };

export const CHANNEL_LABEL = {
  email: "Email",
  linkedin: "LinkedIn DM",
  phone: "Phone call",
  sms: "SMS",
  portal: "Patient portal",
  none: "No outreach",
};

export function initials(name = "") {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0].toUpperCase())
    .join("");
}

export function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
