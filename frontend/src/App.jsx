import React, { useCallback, useEffect, useMemo, useState } from "react";
import { CATEGORY_RANK, fetchRecords, jobsApi } from "./api.js";
import StatCards from "./components/StatCards.jsx";
import Filters from "./components/Filters.jsx";
import RecordTable from "./components/RecordTable.jsx";
import DetailDrawer from "./components/DetailDrawer.jsx";
import JobList from "./components/runs/JobList.jsx";
import JobDetail from "./components/runs/JobDetail.jsx";
import NewRun from "./components/runs/NewRun.jsx";

const sortValue = {
  name: (r) => r.contact.name.toLowerCase(),
  category: (r) => CATEGORY_RANK[r.classification.category] ?? 0,
  score: (r) => r.classification.score,
  channel: (r) => r.action.channel,
  escalate: (r) => Number(r.action.escalate),
};

export default function App() {
  const [view, setView] = useState("records"); // "records" | "runs"
  const [records, setRecords] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [filters, setFilters] = useState({ source: "all", category: "all", escalated: false, q: "" });
  const [sort, setSort] = useState({ key: "score", dir: "desc" });
  const [selectedId, setSelectedId] = useState(null);

  // Job management state
  const [jobs, setJobs] = useState([]);
  const [jobsError, setJobsError] = useState("");
  const [selectedJobId, setSelectedJobId] = useState(null);
  const [isCreatingRun, setIsCreatingRun] = useState(false);
  const [pipelineInfo, setPipelineInfo] = useState(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const data = await fetchRecords();
      setRecords(data);
      setStatus("ready");
      setError("");
    } catch (e) {
      setError(e.message);
      setStatus((s) => (s === "ready" ? "ready" : "error"));
    } finally {
      setRefreshing(false);
    }
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const list = await jobsApi.list();
      setJobs(list);
      setJobsError("");
      return list;
    } catch (e) {
      setJobsError(e.message);
      return [];
    }
  }, []);

  const loadInfo = useCallback(async () => {
    try {
      const info = await jobsApi.info();
      setPipelineInfo(info);
    } catch (e) {
      // non-fatal
    }
  }, []);

  useEffect(() => {
    load();
    loadJobs().then((list) => {
      if (list && list.length > 0) {
        setSelectedJobId((current) => current ?? list[0].id);
      } else {
        setIsCreatingRun(true);
      }
    });
    loadInfo();

    const t = setInterval(() => {
      load();
      loadJobs();
    }, 15000);
    return () => clearInterval(t);
  }, [load, loadJobs, loadInfo]);

  const activeJobsCount = useMemo(() => {
    return jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  }, [jobs]);

  const counts = useMemo(
    () => ({
      all: records.length,
      linkedin_csv: records.filter((r) => r.source === "linkedin_csv").length,
      fhir_emr: records.filter((r) => r.source === "fhir_emr").length,
    }),
    [records]
  );

  const rows = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    const out = records.filter((r) => {
      if (filters.source !== "all" && r.source !== filters.source) return false;
      if (filters.category !== "all" && r.classification.category !== filters.category) return false;
      if (filters.escalated && !r.action.escalate) return false;
      if (q) {
        const hay = [r.contact.name, r.profile?.headline, r.profile?.organization, r.rationale, r.message.body]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const get = sortValue[sort.key];
    out.sort((a, b) => {
      const x = get(a), y = get(b);
      const cmp = x < y ? -1 : x > y ? 1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [records, filters, sort]);

  const selected = useMemo(() => records.find((r) => r.record_id === selectedId) ?? null, [records, selectedId]);
  const closeDrawer = useCallback(() => setSelectedId(null), []);

  const handleStartNewRun = useCallback(() => {
    setView("runs");
    setIsCreatingRun(true);
    setSelectedJobId(null);
  }, []);

  const handleJobCreated = useCallback((newJob) => {
    setJobs((prev) => [newJob, ...prev]);
    setSelectedJobId(newJob.id);
    setIsCreatingRun(false);
  }, []);

  const handleJobTerminal = useCallback(() => {
    load();
    loadJobs();
  }, [load, loadJobs]);

  return (
    <div className="app">
      <div className="bg-glow" aria-hidden="true" />
      <header className="topbar">
        <div className="brand">
          <div className="brand__mark">
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
              <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" fill="currentColor" />
            </svg>
          </div>
          <div>
            <h1>Intake Intelligence</h1>
            <p>AI-qualified leads &amp; patient outreach</p>
          </div>
        </div>

        <nav className="nav-tabs" aria-label="Main navigation">
          <button
            id="nav-records"
            className={`nav-tab ${view === "records" ? "is-active" : ""}`}
            onClick={() => setView("records")}
          >
            Records Dashboard
            <span className="nav-tab__count">{records.length}</span>
          </button>
          <button
            id="nav-runs"
            className={`nav-tab ${view === "runs" ? "is-active" : ""}`}
            onClick={() => setView("runs")}
          >
            Pipeline Runs
            {activeJobsCount > 0 ? (
              <span className="nav-tab__pill nav-tab__pill--live">
                <i /> {activeJobsCount} active
              </span>
            ) : (
              <span className="nav-tab__count">{jobs.length}</span>
            )}
          </button>
        </nav>

        <div className="topbar__actions">
          {view === "records" && (
            <button
              id="topbar-new-run-btn"
              className="btn btn--cta-sm"
              onClick={handleStartNewRun}
            >
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" fill="currentColor" />
              </svg>
              New Run
            </button>
          )}
          <div className={`live ${status === "error" ? "live--down" : ""}`}>
            <i /> {status === "error" ? "CMS offline" : "Connected to CMS"}
          </div>
        </div>
      </header>

      <main className="main">
        {view === "records" && (
          <>
            {status === "loading" && (
              <div className="skeleton-wrap" aria-busy="true">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="skeleton" />
                ))}
              </div>
            )}

            {status === "error" && (
              <div className="empty">
                <div className="empty__icon">!</div>
                <h3>Can’t reach the CMS</h3>
                <p>{error}. Start it with <code>uvicorn server:app --port 8000</code> and refresh.</p>
                <button id="retry-btn" className="btn btn--primary" onClick={load}>Retry</button>
              </div>
            )}

            {status === "ready" && (
              <>
                {error && <div className="banner">Showing last known data — {error}</div>}
                <StatCards records={records} />
                <section className="panel">
                  <Filters filters={filters} setFilters={setFilters} counts={counts} onRefresh={load} refreshing={refreshing} />
                  <RecordTable rows={rows} sort={sort} setSort={setSort} selectedId={selectedId} onSelect={setSelectedId} />
                  <footer className="panel__foot">
                    Showing {rows.length} of {records.length} records · click a row for the agent’s reasoning
                  </footer>
                </section>
              </>
            )}
          </>
        )}

        {view === "runs" && (
          <div className="runs-layout">
            <JobList
              jobs={jobs}
              selectedId={selectedJobId}
              onSelect={(id) => {
                setSelectedJobId(id);
                setIsCreatingRun(false);
              }}
              onNew={() => {
                setIsCreatingRun(true);
                setSelectedJobId(null);
              }}
              creating={isCreatingRun}
              error={jobsError}
            />

            <div className="runs-layout__content">
              {isCreatingRun ? (
                <NewRun info={pipelineInfo} onCreated={handleJobCreated} />
              ) : selectedJobId ? (
                <JobDetail jobId={selectedJobId} onTerminal={handleJobTerminal} />
              ) : (
                <div className="empty panel">
                  <div className="empty__icon">
                    <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor">
                      <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" />
                    </svg>
                  </div>
                  <h3>No run selected</h3>
                  <p>Choose a past pipeline run from history or start a new run with CSV upload &amp; FHIR sources.</p>
                  <button className="btn btn--cta" onClick={() => setIsCreatingRun(true)}>
                    Start a new run
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      <DetailDrawer record={selected} onClose={closeDrawer} />
    </div>
  );
}
