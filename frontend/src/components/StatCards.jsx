import React, { useMemo } from "react";

function Stat({ icon, label, value, hint, accent }) {
  return (
    <div className={`stat stat--${accent}`}>
      <div className={`stat__icon stat__icon--${accent}`} aria-hidden="true">
        {icon}
      </div>
      <div className="stat__body">
        <span className="stat__label">{label}</span>
        <span className="stat__value">{value}</span>
        <span className="stat__hint">{hint}</span>
      </div>
    </div>
  );
}

export default function StatCards({ records }) {
  const s = useMemo(() => {
    const leads = records.filter((r) => r.source === "linkedin_csv");
    const patients = records.filter((r) => r.source === "fhir_emr");
    const priority = records.filter((r) => ["hot", "urgent"].includes(r.classification.category)).length;
    const escalated = records.filter((r) => r.action.escalate).length;
    const llm = records.filter((r) => r.agent_meta.llm_used).length;
    const avg = records.length
      ? Math.round(records.reduce((a, r) => a + r.classification.score, 0) / records.length)
      : 0;
    const dist = {};
    records.forEach((r) => (dist[r.classification.category] = (dist[r.classification.category] || 0) + 1));
    return { leads: leads.length, patients: patients.length, priority, escalated, llm, avg, dist };
  }, [records]);

  const total = records.length;
  const order = ["urgent", "hot", "warm", "routine", "cold", "low"];

  return (
    <section className="stats" aria-label="Pipeline summary">
      <Stat
        accent="indigo"
        label="Records processed"
        value={total}
        hint={`${s.leads} leads · ${s.patients} patients`}
        icon={
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
          </svg>
        }
      />
      <Stat
        accent="rose"
        label="Top priority"
        value={s.priority}
        hint="Hot leads + urgent patients"
        icon={
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        }
      />
      <Stat
        accent="amber"
        label="Escalated to human"
        value={s.escalated}
        hint="Flagged by agent / guardrails"
        icon={
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
            <line x1="4" y1="22" x2="4" y2="15" />
          </svg>
        }
      />
      <Stat
        accent="teal"
        label="AI-decided"
        value={total ? `${Math.round((s.llm / total) * 100)}%` : "—"}
        hint={`${s.llm} LLM · ${total - s.llm} rule-based`}
        icon={
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" />
          </svg>
        }
      />
      <div className="stat stat--wide">
        <span className="stat__label">Category mix · avg score {s.avg}</span>
        {total > 0 ? (
          <>
            <div className="mix" role="img" aria-label="Category distribution">
              {order
                .filter((c) => s.dist[c])
                .map((c) => (
                  <div key={c} className={`mix__seg mix__seg--${c}`} style={{ flexGrow: s.dist[c] }} title={`${c}: ${s.dist[c]}`} />
                ))}
            </div>
            <div className="mix__legend">
              {order
                .filter((c) => s.dist[c])
                .map((c) => (
                  <span key={c}>
                    <i className={`mix__swatch mix__seg--${c}`} />
                    {c} {s.dist[c]}
                  </span>
                ))}
            </div>
          </>
        ) : (
          <div style={{ color: "var(--c-text-muted, #8b949e)", fontSize: "0.85rem", marginTop: "10px" }}>
            No records yet — start a pipeline run to see category distribution
          </div>
        )}
      </div>
    </section>
  );
}
