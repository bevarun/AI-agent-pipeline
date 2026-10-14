import React, { useEffect, useState } from "react";
import { jobsApi } from "../../api.js";

/** value = { choice: presetId | "custom" | "none", customUrl, minPatients } */
export default function FhirSource({ presets = [], value, onChange }) {
  const [probe, setProbe] = useState(null); // { ok, ... } | null
  const [testing, setTesting] = useState(false);
  const set = (patch) => onChange({ ...value, ...patch });

  const baseUrl =
    value.choice === "custom" ? value.customUrl.trim() : presets.find((p) => p.id === value.choice)?.base_url;

  useEffect(() => setProbe(null), [baseUrl]);

  const test = async () => {
    if (!baseUrl) return;
    setTesting(true);
    setProbe(null);
    try {
      setProbe(await jobsApi.testFhir(baseUrl));
    } catch (e) {
      setProbe({ ok: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const options = [
    ...presets.map((p) => ({ id: p.id, label: p.label, sub: p.base_url, desc: p.description })),
    { id: "custom", label: "Custom FHIR R4 server", sub: "Any public R4 endpoint", desc: "Bring your own sandbox." },
    { id: "none", label: "No patient source", sub: "Leads only", desc: "Skip FHIR ingestion for this run." },
  ];

  return (
    <section className="step" aria-labelledby="step-fhir-title">
      <header className="step__head">
        <span className="step__num">2</span>
        <div>
          <h3 id="step-fhir-title">Patient source</h3>
          <p>Synthetic FHIR R4 data. Patients are paged, enriched with Conditions &amp; Appointments, then validated.</p>
        </div>
      </header>

      <div className="choice-grid" role="radiogroup" aria-label="FHIR source">
        {options.map((o) => (
          <button
            key={o.id}
            id={`fhir-choice-${o.id}`}
            role="radio"
            aria-checked={value.choice === o.id}
            className={`choice ${value.choice === o.id ? "is-active" : ""}`}
            onClick={() => set({ choice: o.id })}
          >
            <span className="choice__radio" aria-hidden="true" />
            <span className="choice__body">
              <strong>{o.label}</strong>
              <code>{o.sub}</code>
              <span>{o.desc}</span>
            </span>
          </button>
        ))}
      </div>

      {value.choice !== "none" && (
        <div className="fhir-config">
          {value.choice === "custom" && (
            <label className="field-input field-input--grow" htmlFor="fhir-custom-url">
              <span>Base URL</span>
              <input
                id="fhir-custom-url"
                type="url"
                placeholder="https://your-fhir-server/baseR4"
                value={value.customUrl}
                onChange={(e) => set({ customUrl: e.target.value })}
              />
            </label>
          )}
          <label className="field-input" htmlFor="fhir-min-patients">
            <span>Min. patients</span>
            <input
              id="fhir-min-patients"
              type="number"
              min={1}
              max={100}
              value={value.minPatients}
              onChange={(e) => set({ minPatients: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })}
            />
          </label>
          <button id="fhir-test-btn" className={`btn ${testing ? "is-spinning" : ""}`} onClick={test} disabled={!baseUrl || testing}>
            <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
              <path d="M4 12a8 8 0 0114-5.3V4h2v6h-6V8h2.7A6 6 0 106 12H4z" fill="currentColor" />
            </svg>
            {testing ? "Testing…" : "Test connection"}
          </button>
        </div>
      )}

      {probe && (
        <div className={`alert ${probe.ok ? "alert--ok" : "alert--bad"}`} role="status">
          {probe.ok ? (
            <>
              <strong>Reachable</strong> · FHIR {probe.fhir_version ?? "?"}
              {probe.software && ` · ${probe.software}`} · {probe.latency_ms} ms
              {probe.supports?.length > 0 && ` · supports ${probe.supports.join(", ")}`}
              {probe.warnings?.map((w) => (
                <div key={w} className="alert__sub">⚠ {w}</div>
              ))}
            </>
          ) : (
            <>
              <strong>Not reachable</strong> · {probe.error}
            </>
          )}
        </div>
      )}
    </section>
  );
}
