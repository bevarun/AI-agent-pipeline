import React from "react";
import { SOURCE_LABEL } from "../api.js";

const SOURCES = [
  ["all", "All records"],
  ["linkedin_csv", "Leads"],
  ["fhir_emr", "Patients"],
];
const CATEGORIES = ["all", "hot", "warm", "cold", "urgent", "routine", "low"];

export default function Filters({ filters, setFilters, counts, onRefresh, refreshing }) {
  const set = (patch) => setFilters((f) => ({ ...f, ...patch }));
  return (
    <div className="toolbar">
      <div className="seg" role="tablist" aria-label="Record source">
        {SOURCES.map(([key, label]) => (
          <button
            key={key}
            id={`tab-${key}`}
            role="tab"
            aria-selected={filters.source === key}
            className={`seg__btn ${filters.source === key ? "is-active" : ""}`}
            onClick={() => set({ source: key })}
          >
            {label}
            <span className="seg__count">{counts[key] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="toolbar__right">
        <div className="search">
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M11 4a7 7 0 105.2 11.7l3.5 3.5 1.4-1.4-3.5-3.5A7 7 0 0011 4zm0 2a5 5 0 110 10 5 5 0 010-10z" fill="currentColor" />
          </svg>
          <input
            id="search-input"
            type="search"
            placeholder="Search name, company, rationale…"
            value={filters.q}
            onChange={(e) => set({ q: e.target.value })}
            aria-label="Search records"
          />
        </div>

        <select
          id="category-filter"
          className="select"
          value={filters.category}
          onChange={(e) => set({ category: e.target.value })}
          aria-label="Filter by category"
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c === "all" ? "All categories" : c[0].toUpperCase() + c.slice(1)}
            </option>
          ))}
        </select>

        <label className={`chip ${filters.escalated ? "is-active" : ""}`} htmlFor="escalated-toggle">
          <input
            id="escalated-toggle"
            type="checkbox"
            checked={filters.escalated}
            onChange={(e) => set({ escalated: e.target.checked })}
          />
          Escalated only
        </label>

        <button id="refresh-btn" className={`btn ${refreshing ? "is-spinning" : ""}`} onClick={onRefresh}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M17.6 6.4A8 8 0 104 12h2a6 6 0 116-6c1.7 0 3.2.7 4.2 1.8L13 11h8V3l-3.4 3.4z" fill="currentColor" />
          </svg>
          Refresh
        </button>
      </div>
    </div>
  );
}
