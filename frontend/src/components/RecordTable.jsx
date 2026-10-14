import React from "react";
import { Avatar, CategoryBadge, ScoreBar, SourceTag } from "./Badges.jsx";
import { CHANNEL_LABEL } from "../api.js";

const COLUMNS = [
  ["name", "Name"],
  ["category", "Category"],
  ["score", "Score"],
  ["channel", "Next action"],
  ["escalate", "Escalation"],
];

export default function RecordTable({ rows, sort, setSort, selectedId, onSelect }) {
  const toggle = (key) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));

  if (!rows.length) {
    return (
      <div className="empty">
        <div className="empty__icon">∅</div>
        <h3>No records match your filters</h3>
        <p>Adjust the search or filters, or run the pipeline to ingest more data.</p>
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {COLUMNS.map(([key, label]) => (
              <th key={key} onClick={() => toggle(key)} aria-sort={sort.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                <button className="th-btn">
                  {label}
                  <span className={`sort ${sort.key === key ? "is-on" : ""}`}>{sort.key === key && sort.dir === "asc" ? "▲" : "▼"}</span>
                </button>
              </th>
            ))}
            <th className="th-end">CMS</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={r.record_id}
              id={`row-${r.record_id}`}
              className={`row ${selectedId === r.record_id ? "is-selected" : ""}`}
              style={{ animationDelay: `${Math.min(i, 14) * 25}ms` }}
              onClick={() => onSelect(r.record_id)}
              tabIndex={0}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(r.record_id)}
            >
              <td>
                <div className="person">
                  <Avatar name={r.contact.name} source={r.source} />
                  <div className="person__text">
                    <span className="person__name">{r.contact.name}</span>
                    <span className="person__sub">
                      {[r.profile?.headline, r.profile?.organization].filter(Boolean).join(" · ") || <SourceTag source={r.source} />}
                    </span>
                  </div>
                </div>
              </td>
              <td>
                <CategoryBadge category={r.classification.category} />
              </td>
              <td>
                <ScoreBar score={r.classification.score} />
              </td>
              <td>
                <div className="action">
                  <span className="action__channel">{CHANNEL_LABEL[r.action.channel] ?? r.action.channel}</span>
                  <span className="action__tone">{r.action.tone}</span>
                </div>
              </td>
              <td>
                {r.action.escalate ? <span className="flag flag--on">⚑ Escalate</span> : <span className="flag">Auto</span>}
              </td>
              <td className="td-end">
                <span className="synced" title={`CMS id ${r.cms_id}`}>
                  <i /> #{r.cms_id}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
