import React, { useEffect, useState } from "react";
import { Avatar, CategoryBadge, ScoreBar, SourceTag } from "./Badges.jsx";
import { CHANNEL_LABEL, formatTime } from "../api.js";

function Field({ label, children }) {
  return (
    <div className="field">
      <span className="field__label">{label}</span>
      <span className="field__value">{children ?? "—"}</span>
    </div>
  );
}

export default function DetailDrawer({ record, onClose }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!record) return;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [record, onClose]);

  useEffect(() => setCopied(false), [record?.record_id]);

  if (!record) return null;
  const { contact, classification: c, action: a, message, agent_meta: m, profile } = record;

  const copy = async () => {
    await navigator.clipboard.writeText(`${message.subject}\n\n${message.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Details for ${contact.name}`}>
        <header className="drawer__head">
          <Avatar name={contact.name} source={record.source} size="lg" />
          <div className="drawer__title">
            <h2>{contact.name}</h2>
            <p>{[profile?.headline, profile?.organization, profile?.location].filter(Boolean).join(" · ") || record.record_id}</p>
            <div className="drawer__tags">
              <SourceTag source={record.source} />
              <CategoryBadge category={c.category} />
              {a.escalate && <span className="flag flag--on">⚑ Escalate</span>}
            </div>
          </div>
          <button id="drawer-close" className="icon-btn" onClick={onClose} aria-label="Close details">
            ✕
          </button>
        </header>

        <div className="drawer__body">
          <section className="card">
            <h3>Agent rationale</h3>
            <p className="rationale">{record.rationale}</p>
            <div className="grid2">
              <Field label="Score">
                <ScoreBar score={c.score} />
              </Field>
              <Field label="Confidence">
                <span className={`conf conf--${c.confidence}`}>{c.confidence}</span>
              </Field>
            </div>
          </section>

          <section className="card">
            <h3>Decision</h3>
            <div className="grid2">
              <Field label="Channel">{CHANNEL_LABEL[a.channel] ?? a.channel}</Field>
              <Field label="Tone">{a.tone}</Field>
              <Field label="Escalate">{a.escalate ? "Yes — human review" : "No — automated"}</Field>
              <Field label="Flag reason">{a.flag_reason}</Field>
            </div>
          </section>

          <section className="card">
            <div className="card__head">
              <h3>Generated message</h3>
              <button id="copy-message" className="btn btn--ghost" onClick={copy}>
                {copied ? "Copied ✓" : "Copy"}
              </button>
            </div>
            <div className="message">
              <div className="message__subject">{message.subject}</div>
              <p className="message__body">{message.body}</p>
            </div>
          </section>

          <section className="card">
            <h3>Contact</h3>
            <div className="grid2">
              <Field label="Email">{contact.email}</Field>
              <Field label="Phone">{contact.phone}</Field>
              <Field label="LinkedIn">{contact.linkedin_url}</Field>
              <Field label="Record ID">
                <code>{record.record_id}</code>
              </Field>
            </div>
          </section>

          <section className="card card--meta">
            <h3>Pipeline & delivery</h3>
            <div className="grid2">
              <Field label="Model">
                {m.llm_used ? m.model : "Rule-based fallback"}
              </Field>
              <Field label="Agent steps">{m.steps}</Field>
              <Field label="Latency">{(m.latency_ms / 1000).toFixed(1)}s</Field>
              <Field label="Processed">{formatTime(m.processed_at)}</Field>
              <Field label="CMS id">
                <span className="synced">
                  <i /> #{record.cms_id}
                </span>
              </Field>
              <Field label="Received by CMS">{formatTime(record.received_at)}</Field>
            </div>
          </section>
        </div>
      </aside>
    </>
  );
}
