import React, { useRef, useState } from "react";
import { formatBytes, jobsApi } from "../../api.js";

const FIELD_LABEL = {
  full_name: "Name", first_name: "First name", last_name: "Last name", email: "Email", phone: "Phone",
  linkedin_url: "LinkedIn", title: "Title", company: "Company", industry: "Industry", location: "Location",
  company_size: "Company size", headline: "Headline", activity: "Activity", connection_note: "Connection note",
  lead_id: "Lead ID",
};

const MODES = [
  ["upload", "Upload CSV"],
  ["sample", "Sample data"],
  ["none", "Skip"],
];

export default function CsvSource({ mode, setMode, preview, setPreview, maxMb = 5 }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [showErrors, setShowErrors] = useState(false);

  const choose = async (m) => {
    setMode(m);
    setError("");
    setPreview(null);
    if (m === "sample") {
      setBusy(true);
      try {
        setPreview(await jobsApi.samplePreview());
      } catch (e) {
        setError(e.message);
      } finally {
        setBusy(false);
      }
    }
  };

  const handleFile = async (file) => {
    if (!file) return;
    setError("");
    setPreview(null);
    if (!/\.(csv|txt)$/i.test(file.name)) return setError("Please choose a .csv file.");
    if (file.size > maxMb * 1024 * 1024) return setError(`File is ${formatBytes(file.size)}; the limit is ${maxMb} MB.`);
    setBusy(true);
    try {
      setPreview(await jobsApi.upload(file));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const v = preview?.validation;
  const validPct = v ? Math.round((v.valid / Math.max(1, v.valid + v.rejected)) * 100) : 0;

  return (
    <section className="step" aria-labelledby="step-csv-title">
      <header className="step__head">
        <span className="step__num">1</span>
        <div>
          <h3 id="step-csv-title">Lead source</h3>
          <p>LinkedIn / Sales Navigator style CSV. Columns are auto-mapped; every row is pre-validated.</p>
        </div>
        <div className="seg seg--sm" role="radiogroup" aria-label="CSV source">
          {MODES.map(([k, label]) => (
            <button
              key={k}
              id={`csv-mode-${k}`}
              role="radio"
              aria-checked={mode === k}
              className={`seg__btn ${mode === k ? "is-active" : ""}`}
              onClick={() => choose(k)}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {mode === "upload" && !preview && (
        <label
          htmlFor="csv-file-input"
          className={`drop ${drag ? "is-drag" : ""} ${busy ? "is-busy" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            handleFile(e.dataTransfer.files?.[0]);
          }}
        >
          <input
            ref={inputRef}
            id="csv-file-input"
            type="file"
            accept=".csv,text/csv"
            hidden
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <div className="drop__icon" aria-hidden="true">
            {busy ? (
              <span className="spinner" />
            ) : (
              <svg viewBox="0 0 24 24" width="26" height="26">
                <path d="M12 3l5 5h-3v6h-4V8H7l5-5zM5 17h14v3H5z" fill="currentColor" />
              </svg>
            )}
          </div>
          <strong>{busy ? "Uploading & validating…" : "Drop a CSV here or click to browse"}</strong>
          <span>Comma, semicolon or tab delimited · UTF-8 or Windows-1252 · up to {maxMb} MB</span>
        </label>
      )}

      {mode === "sample" && busy && <div className="skeleton skeleton--sm" />}
      {mode === "none" && <p className="step__note">No CSV leads will be ingested in this run.</p>}
      {error && <div className="alert alert--bad" role="alert">{error}</div>}

      {preview && mode !== "none" && (
        <div className="preview">
          <div className="preview__file">
            <div className="file-icon" aria-hidden="true">CSV</div>
            <div className="preview__meta">
              <strong>{preview.filename}</strong>
              <span>
                {preview.rows} rows · {preview.columns.length} columns · {preview.delimiter}-delimited ·{" "}
                {formatBytes(preview.size)}
                {preview.encoding === "cp1252" && " · converted from Windows-1252"}
              </span>
            </div>
            {mode === "upload" && (
              <button id="csv-replace-btn" className="btn btn--ghost" onClick={() => setPreview(null)}>
                Replace
              </button>
            )}
          </div>

          <div className="validity">
            <div className="validity__bar" role="img" aria-label={`${v.valid} valid, ${v.rejected} rejected`}>
              <span className="validity__ok" style={{ width: `${validPct}%` }} />
              <span className="validity__bad" style={{ width: `${100 - validPct}%` }} />
            </div>
            <div className="validity__legend">
              <span><i className="dot dot--ok" /> {v.valid} will be processed</span>
              <span><i className="dot dot--bad" /> {v.rejected} will be dead-lettered</span>
              {v.rejected > 0 && (
                <button id="csv-errors-toggle" className="link-btn" onClick={() => setShowErrors((x) => !x)}>
                  {showErrors ? "Hide" : "Show"} reasons
                </button>
              )}
            </div>
            {showErrors && v.rejected > 0 && (
              <ul className="issue-list">
                {v.errors.map((e) => (
                  <li key={e.line}>
                    <code>line {e.line}</code>
                    <span>{e.name || <em>unnamed row</em>}</span>
                    <span className="issue-list__err">{e.error}</span>
                  </li>
                ))}
                {v.rejected > v.errors.length && <li className="muted">…and {v.rejected - v.errors.length} more</li>}
              </ul>
            )}
          </div>

          <div className="mapping">
            <span className="mapping__label">Recognised</span>
            {Object.entries(preview.mapped).map(([field, col]) => (
              <span key={field} className="pill pill--ok" title={`Column “${col}” → ${field}`}>
                {FIELD_LABEL[field] ?? field}
                {col.toLowerCase() !== field && <em> ← {col}</em>}
              </span>
            ))}
          </div>
          {preview.unmapped.length > 0 && (
            <div className="mapping">
              <span className="mapping__label">Kept as extra context</span>
              {preview.unmapped.map((c) => (
                <span key={c} className="pill">{c}</span>
              ))}
            </div>
          )}

          <div className="table-wrap preview__table">
            <table className="table table--compact">
              <thead>
                <tr>
                  {preview.columns.map((c) => (
                    <th key={c}><span className="th-static">{c}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.sample.map((row, i) => (
                  <tr key={i}>
                    {preview.columns.map((c) => (
                      <td key={c} title={row[c]}>{row[c]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
