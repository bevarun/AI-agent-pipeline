import React, { useEffect, useMemo, useState } from "react";
import { jobsApi } from "../../api.js";
import CsvSource from "./CsvSource.jsx";
import FhirSource from "./FhirSource.jsx";

export default function NewRun({ info, onCreated }) {
  const [csvMode, setCsvMode] = useState("upload");
  const [preview, setPreview] = useState(null);
  const [fhir, setFhir] = useState({ choice: "smart", customUrl: "", minPatients: 15 });
  const [limit, setLimit] = useState(0);
  const [chaos, setChaos] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const presets = info?.fhir_presets ?? [];
  useEffect(() => {
    // Default to whichever preset matches the server's FHIR_BASE_URL.
    const match = presets.find((p) => p.base_url === info?.fhir_default);
    if (match) setFhir((f) => ({ ...f, choice: match.id }));
  }, [info]); // eslint-disable-line react-hooks/exhaustive-deps

  const fhirUrl =
    fhir.choice === "none"
      ? null
      : fhir.choice === "custom"
        ? fhir.customUrl.trim()
        : presets.find((p) => p.id === fhir.choice)?.base_url;

  const problems = useMemo(() => {
    const p = [];
    if (csvMode !== "none" && !preview) p.push(csvMode === "upload" ? "Upload a CSV file" : "Sample data not loaded");
    if (csvMode !== "none" && preview && preview.validation.valid === 0) p.push("The CSV has no valid rows");
    if (fhir.choice === "custom" && !/^https?:\/\/\S+$/i.test(fhirUrl || "")) p.push("Enter a valid FHIR base URL");
    if (csvMode === "none" && fhir.choice === "none") p.push("Select at least one source");
    return p;
  }, [csvMode, preview, fhir.choice, fhirUrl]);

  const leadCount = csvMode !== "none" && preview ? preview.validation.valid : 0;
  const plannedLabel = [
    leadCount ? `${leadCount} leads` : null,
    fhir.choice !== "none" ? `≥${fhir.minPatients} patients` : null,
  ]
    .filter(Boolean)
    .join(" + ");

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const job = await jobsApi.create({
        csv: csvMode,
        upload_id: csvMode === "upload" ? preview?.upload_id : null,
        fhir: { enabled: fhir.choice !== "none", base_url: fhirUrl, min_patients: fhir.minPatients },
        limit: Number(limit) || 0,
        chaos,
      });
      onCreated(job);
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="composer">
      <div className="composer__intro">
        <h2>New pipeline run</h2>
        <p>
          Ingestion, the AI agent and CMS delivery run on the server exactly as the CLI does — this page just
          configures the run and streams its progress.
        </p>
      </div>

      <CsvSource
        mode={csvMode}
        setMode={setCsvMode}
        preview={preview}
        setPreview={setPreview}
        maxMb={info?.max_upload_mb ?? 5}
      />

      <FhirSource presets={presets} value={fhir} onChange={setFhir} />

      <section className="step" aria-labelledby="step-opts-title">
        <header className="step__head">
          <span className="step__num">3</span>
          <div>
            <h3 id="step-opts-title">Run options</h3>
            <p>Keep runs small while iterating — rate limited to 4 LLM calls/minute (~15s between calls).</p>
          </div>
        </header>
        <div className="opts">
          <label className="field-input" htmlFor="run-limit">
            <span>Record limit</span>
            <input
              id="run-limit"
              type="number"
              min={0}
              max={1000}
              value={limit}
              onChange={(e) => setLimit(Math.max(0, Math.min(1000, Number(e.target.value) || 0)))}
            />
            <small>0 = process everything</small>
          </label>

          <label className={`toggle ${chaos ? "is-on" : ""}`} htmlFor="run-chaos">
            <input id="run-chaos" type="checkbox" checked={chaos} onChange={(e) => setChaos(e.target.checked)} />
            <span className="toggle__track" aria-hidden="true"><span /></span>
            <span className="toggle__text">
              <strong>Chaos mode</strong>
              <small>Inject bad rows, a FHIR timeout and CMS 503s to exercise retries &amp; dead-lettering.</small>
            </span>
          </label>

          <div className={`llm-status ${info?.llm_available ? "is-ok" : "is-warn"}`}>
            <i aria-hidden="true" />
            {info ? (
              info.llm_available ? (
                <span><strong>{info.llm_model}</strong> connected — agent decisions use the LLM</span>
              ) : (
                <span><strong>No GEMINI_API_KEY</strong> — every record will use the rule-based fallback</span>
              )
            ) : (
              <span>Checking LLM configuration…</span>
            )}
          </div>
        </div>
      </section>

      {error && <div className="alert alert--bad" role="alert">{error}</div>}

      <footer className="composer__foot">
        <div className="composer__summary">
          {problems.length ? (
            <span className="muted">{problems[0]}</span>
          ) : (
            <>
              <span className="muted">Ready:</span> <strong>{plannedLabel}</strong>
              {limit > 0 && <span className="muted"> · capped at {limit}</span>}
              {chaos && <span className="pill pill--warn">chaos</span>}
            </>
          )}
        </div>
        <button
          id="start-run-btn"
          className="btn btn--cta"
          disabled={problems.length > 0 || submitting}
          onClick={submit}
        >
          {submitting ? <span className="spinner spinner--sm" /> : (
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path d="M8 5v14l11-7z" fill="currentColor" />
            </svg>
          )}
          {submitting ? "Queuing…" : "Start run"}
        </button>
      </footer>
    </div>
  );
}
