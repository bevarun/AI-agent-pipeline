"""The agent: a bounded, observable loop per record.

    PERCEIVE -> TRIAGE (LLM: classify + decide action + rationale)
             -> GUARDRAILS (deterministic policy that can override the LLM)
             -> COMPOSE (LLM: personalised message)
             -> CRITIQUE (deterministic checks; feed failures back and regenerate, max 2x)
             -> ASSEMBLE (contract-shaped payload)

Every step appends to a trace so a reviewer can see what happened and why.
If the LLM is unavailable or keeps failing, the agent degrades to a rule-based
path (llm_used=false) rather than dropping the record.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .ingest_fhir import CHRONIC_KEYWORDS
from .llm import GeminiClient, LLMError
from .models import Record, utcnow

log = logging.getLogger("agent")

LABELS = {"linkedin_csv": ["hot", "warm", "cold"], "fhir_emr": ["urgent", "routine", "low"]}
CHANNELS = {"linkedin_csv": ["email", "linkedin", "none"], "fhir_emr": ["phone", "sms", "email", "portal", "none"]}
TONES = ["professional", "warm", "empathetic", "urgent", "casual"]
MAX_TRIAGE_TRIES, MAX_COMPOSE_TRIES = 2, 3

SYSTEM_LEAD = """You are an outbound sales-qualification agent for a B2B automation/AI services firm.
Qualify the lead and choose the best outreach action. Be skeptical: a senior title alone is not intent.
Respond ONLY with JSON."""
SYSTEM_PATIENT = """You are a patient-outreach coordination agent for a clinic. You triage who needs follow-up
and how, you do NOT give medical advice or diagnoses. Prefer safe, conservative decisions: when in doubt, escalate
to a human care coordinator. Respond ONLY with JSON."""


# ---------------------------------------------------------------- perceive
def perceive(rec: Record) -> dict[str, Any]:
    """Build the minimal fact sheet the LLM may see. For patients we apply data minimisation:
    first name only; no address/phone/email values, only whether each channel exists."""
    c = rec.contact_info
    facts: dict[str, Any] = {
        "available_channels": {
            "email": bool(c.email), "phone": bool(c.phone), "linkedin": bool(c.linkedin_url),
        },
        "rule_based_priority_score": rec.priority_signal.score,
        "rule_based_factors": rec.priority_signal.factors,
    }
    if rec.source == "fhir_emr":
        facts["patient_first_name"] = rec.name.split()[0]
        facts["context"] = {k: v for k, v in rec.context.items() if v not in (None, [], "")}
        facts["available_channels"] = {"phone": bool(c.phone), "sms": bool(c.phone), "email": bool(c.email), "portal": True}
    else:
        facts["lead_name"] = rec.name
        facts["context"] = rec.context
        # Sales Navigator leads are LinkedIn connections: DM is always possible even without email/URL.
        facts["available_channels"]["linkedin"] = True
    return facts


def _triage_prompt(rec: Record, facts: dict, feedback: str = "") -> str:
    labels, channels = LABELS[rec.source], CHANNELS[rec.source]
    guide = ("hot = clear buying intent or strong fit + reachable; warm = good fit, unclear intent; cold = poor fit or unreachable."
             if rec.source == "linkedin_csv" else
             "urgent = clinically meaningful gap in care needing prompt human follow-up; routine = standard recall/reminder; low = no action needed soon.")
    return f"""Record facts (JSON):
{json.dumps(facts, indent=1, default=str)}

Definitions: {guide}
Allowed category: {labels}. Allowed channel (must be an available one, or "none"): {channels}. Allowed tone: {TONES}.
The rule-based score is a hint; you may deviate, but explain why.
{feedback}
Return JSON with keys:
category, score (int 0-100), confidence ("low"|"medium"|"high"), channel, tone, escalate (bool),
flag_reason (string or null), rationale (2-4 sentences citing the specific facts that drove your decision)."""


def _validate_triage(rec: Record, facts: dict, t: dict) -> dict:
    errs = []
    if t.get("category") not in LABELS[rec.source]:
        errs.append(f"category must be one of {LABELS[rec.source]}")
    if t.get("channel") not in CHANNELS[rec.source]:
        errs.append(f"channel must be one of {CHANNELS[rec.source]}")
    if t.get("tone") not in TONES:
        errs.append(f"tone must be one of {TONES}")
    if t.get("confidence") not in ("low", "medium", "high"):
        errs.append("confidence must be low|medium|high")
    try:
        t["score"] = max(0, min(100, int(t.get("score"))))
    except (TypeError, ValueError):
        errs.append("score must be an integer 0-100")
    if not isinstance(t.get("escalate"), bool):
        errs.append("escalate must be boolean")
    if not isinstance(t.get("rationale"), str) or len(t["rationale"].strip()) < 10:
        errs.append("rationale must be a descriptive string")
    if errs:
        raise ValueError("; ".join(errs))
    t["flag_reason"] = t.get("flag_reason") or None
    return t


# ---------------------------------------------------------------- guardrails
def apply_guardrails(rec: Record, facts: dict, t: dict) -> tuple[dict, list[str]]:
    """Deterministic policy. LLM output is advisory; safety/reachability rules win."""
    notes: list[str] = []
    avail = facts["available_channels"]
    ch = t["channel"]
    if ch != "none" and not avail.get(ch, False):
        alt = next((c for c in CHANNELS[rec.source] if c != "none" and avail.get(c)), "none")
        notes.append(f"channel '{ch}' unavailable for this record -> '{alt}'")
        t["channel"], ch = alt, alt
    if ch == "none":
        reachable = any(v for k, v in avail.items() if k != "none")
        if reachable and rec.source == "linkedin_csv" and t["category"] == "cold":
            # Deliberate deprioritisation by the agent, not a delivery problem: no outreach, no escalation.
            t["escalate"] = False
            t["flag_reason"] = t["flag_reason"] or "Deprioritised: cold lead, no outreach"
            notes.append("agent chose no outreach for cold lead (channels were available) -> kept as deprioritised")
        else:
            if not t["escalate"]:
                notes.append("no reachable channel -> escalate for manual review")
            t["escalate"] = True
            t["flag_reason"] = t["flag_reason"] or "No reachable contact channel"
    if rec.source == "fhir_emr":
        if t["category"] == "urgent" and not t["escalate"]:
            notes.append("urgent patient must be escalated to a care coordinator")
            t["escalate"] = True
        if t["category"] == "urgent" and t["tone"] in ("casual", "professional"):
            t["tone"] = "empathetic"
            notes.append("tone forced to 'empathetic' for urgent patient outreach")
        if t["channel"] == "email" and t["category"] == "urgent":
            notes.append("urgent patient outreach should not rely on email only -> phone")
            t["channel"] = "phone" if avail.get("phone") else t["channel"]
    else:
        if t["category"] == "cold" and t["escalate"]:
            t["escalate"] = False
            notes.append("cold leads are never escalated")
    return t, notes


def _profile(rec: Record) -> dict:
    """Display-only summary for the CMS UI. Deliberately excludes diagnoses (data minimisation)."""
    c = rec.context
    if rec.source == "linkedin_csv":
        return {"headline": c.get("title"), "organization": c.get("company"), "location": c.get("location")}
    bits = [f"{c['age']}y" if c.get("age") is not None else None, c.get("gender")]
    return {"headline": " · ".join(b for b in bits if b) or None, "organization": None, "location": None}


# ---------------------------------------------------------------- compose + critique
def _compose_prompt(rec: Record, facts: dict, t: dict, feedback: str) -> str:
    if rec.source == "linkedin_csv":
        ctx = {k: v for k, v in rec.context.items() if k != "extra"}
        brief = (f"Write a short {t['tone']} outreach message ({'LinkedIn DM, max 70 words' if t['channel']=='linkedin' else 'email, 80-120 words'}) "
                 f"to {rec.name}. Context: {json.dumps(ctx)}. Reference something specific (role, company or notes). "
                 "One soft call-to-action. No hype, no placeholders like [Name], sign off as 'Alex'.")
    else:
        # PHI minimisation: the message prompt deliberately receives NO diagnoses/conditions.
        brief = (f"Write a short {t['tone']} {t['channel']} message (max 80 words) to patient {facts['patient_first_name']} "
                 f"from the clinic, category={t['category']}. Invite them to contact the clinic or book a follow-up visit. "
                 "Do NOT mention any medical condition, test, diagnosis or medication. Do not give medical advice. "
                 "No placeholders. Sign off as 'The Care Team'.")
    return f"{brief}\n{feedback}\nReturn JSON: {{\"subject\": str, \"body\": str}}"


def critique_message(rec: Record, msg: dict) -> list[str]:
    body, subject = str(msg.get("body", "")), str(msg.get("subject", ""))
    issues = []
    if len(body) < 30:
        issues.append("body too short")
    if len(body) > 1200:
        issues.append("body too long")
    if re.search(r"\[[^\]]+\]|\{\{|<name>", body + subject, re.I):
        issues.append("contains unfilled placeholder")
    first = rec.name.split()[0].lower()
    if first not in body.lower():
        issues.append("does not address recipient by first name")
    if rec.source == "fhir_emr":
        terms = {w.lower() for c in rec.context.get("active_conditions", []) for w in re.findall(r"[A-Za-z]{5,}", c)}
        leaked = [w for w in terms | set(CHRONIC_KEYWORDS) if w in (body + subject).lower()]
        if leaked or re.search(r"diagnos|medication|prescri", body, re.I):
            issues.append(f"mentions clinical details ({', '.join(sorted(leaked)[:3]) or 'diagnosis/medication'}) - PHI minimisation violation")
    return issues


# ---------------------------------------------------------------- fallbacks (no LLM)
def rule_based_triage(rec: Record, facts: dict) -> dict:
    s = rec.priority_signal.score
    avail = facts["available_channels"]
    if rec.source == "linkedin_csv":
        cat = "hot" if s >= 70 else "warm" if s >= 40 else "cold"
        ch = "email" if avail["email"] else "linkedin" if avail["linkedin"] else "none"
        tone, esc = "professional", False
    else:
        cat = "urgent" if s >= 65 else "routine" if s >= 40 else "low"
        ch = "phone" if avail["phone"] else "email" if avail["email"] else "portal"
        tone, esc = ("empathetic", cat == "urgent")
    return {"category": cat, "score": s, "confidence": "low", "channel": ch, "tone": tone, "escalate": esc,
            "flag_reason": "LLM unavailable - rule-based decision", "rationale":
            f"Rule-based fallback: score {s} from factors {rec.priority_signal.factors or 'none'}."}


def template_message(rec: Record, t: dict) -> dict:
    first = rec.name.split()[0]
    if rec.source == "linkedin_csv":
        return {"subject": f"Quick idea for {rec.context.get('company', 'your team')}",
                "body": f"Hi {first}, I came across your work{' at ' + rec.context['company'] if rec.context.get('company') else ''} "
                        "and thought a short chat about automating repetitive workflows could be useful. Open to a quick call? - Alex"}
    return {"subject": "A note from your care team",
            "body": f"Hello {first}, we'd like to check in and help you schedule a follow-up visit. "
                    "Please call the clinic or use the patient portal at your convenience. - The Care Team"}


# ---------------------------------------------------------------- the agent
class Agent:
    def __init__(self, llm: GeminiClient):
        self.llm = llm

    def process(self, rec: Record, on_step=None) -> tuple[dict, list[dict]]:
        """`on_step(entry)` is an optional observer (used by the job API for live progress)."""
        t0 = time.monotonic()
        trace: list[dict] = []

        def step(name: str, **info):
            entry = {"step": name, "at": utcnow(), **info}
            trace.append(entry)
            log.info("[%s] %s %s", rec.id, name, json.dumps(info, default=str)[:300])
            if on_step:
                try:
                    on_step(entry)
                except Exception:  # an observer must never break processing
                    log.debug("on_step observer failed", exc_info=True)

        facts = perceive(rec)
        step("perceive", score_hint=rec.priority_signal.score, factors=rec.priority_signal.factors)
        llm_used = self.llm.available

        # --- triage with validation feedback loop
        triage, feedback = None, ""
        if llm_used:
            for attempt in range(1, MAX_TRIAGE_TRIES + 1):
                try:
                    log.info("[%s] LLM Triage Request (attempt %d/%d, score_hint=%s)",
                             rec.id, attempt, MAX_TRIAGE_TRIES, rec.priority_signal.score)
                    raw = self.llm.generate_json(
                        SYSTEM_LEAD if rec.source == "linkedin_csv" else SYSTEM_PATIENT,
                        _triage_prompt(rec, facts, feedback), temperature=0.2)
                    triage = _validate_triage(rec, facts, raw)
                    log.info("[%s] LLM Triage Validated: category=%s, score=%d, channel=%s, tone=%s, escalate=%s",
                             rec.id, triage["category"], triage["score"], triage["channel"], triage["tone"], triage["escalate"])
                    step("triage", attempt=attempt, **{k: triage[k] for k in ("category", "score", "channel", "tone", "escalate")})
                    break
                except (LLMError, ValueError) as e:
                    log.warning("[%s] LLM Triage attempt %d failed validation/error: %s", rec.id, attempt, e)
                    step("triage_failed", attempt=attempt, error=str(e)[:200])
                    feedback = f"Your previous answer was invalid: {e}. Fix it."
                    if isinstance(e, LLMError) and "after" in str(e):  # retries already exhausted in client
                        break
        if triage is None:
            llm_used = False
            triage = rule_based_triage(rec, facts)
            log.info("[%s] Using rule-based triage fallback: category=%s, score=%d", rec.id, triage["category"], triage["score"])
            step("triage_fallback", reason="LLM unavailable or invalid output", **{k: triage[k] for k in ("category", "score", "channel")})

        triage, notes = apply_guardrails(rec, facts, triage)
        if notes:
            log.info("[%s] Guardrails applied: %s", rec.id, "; ".join(notes))
            step("guardrails_override", changes=notes)
            triage["rationale"] += " [Policy: " + "; ".join(notes) + "]"

        # --- compose + critique loop
        if triage["channel"] == "none":
            msg = {"subject": "Manual review required",
                   "body": f"No automated outreach sent. Reason: {triage['flag_reason']}. A human should review this record."}
            step("compose_skipped", reason="no channel")
        else:
            msg, feedback = None, ""
            for attempt in range(1, MAX_COMPOSE_TRIES + 1):
                candidate = None
                if llm_used:
                    try:
                        log.info("[%s] LLM Compose Request (attempt %d/%d, channel=%s, tone=%s)",
                                 rec.id, attempt, MAX_COMPOSE_TRIES, triage["channel"], triage["tone"])
                        c = self.llm.generate_json("You write concise, compliant outreach messages. Respond ONLY with JSON.",
                                                   _compose_prompt(rec, facts, triage, feedback), temperature=0.7)
                        if isinstance(c.get("subject"), str) and isinstance(c.get("body"), str):
                            candidate = c
                            log.info("[%s] LLM Message Generated: subject=%r", rec.id, c.get("subject", "")[:50])
                    except LLMError as e:
                        log.warning("[%s] LLM Compose attempt %d failed: %s", rec.id, attempt, e)
                        step("compose_failed", attempt=attempt, error=str(e)[:200])
                        llm_used = False
                if candidate is None:
                    candidate = template_message(rec, triage)
                    log.info("[%s] Using template fallback message", rec.id)
                    step("compose_template", attempt=attempt)
                issues = critique_message(rec, candidate)
                step("critique", attempt=attempt, issues=issues or "pass")
                if not issues:
                    msg = candidate
                    log.info("[%s] Message passed critique on attempt %d", rec.id, attempt)
                    break
                log.warning("[%s] Message failed critique (attempt %d): %s", rec.id, attempt, issues)
                feedback = "Previous draft rejected: " + "; ".join(issues) + ". Rewrite fixing all of these."
            if msg is None:  # LLM drafts kept failing the critic: use the safe template, never ship a bad draft
                msg = template_message(rec, triage)
                triage["flag_reason"] = (triage["flag_reason"] or "") + " Draft failed critique; safe template used."
                step("compose_safe_template", reason="critique failed after retries")

        payload = {
            "schema_version": "1.0",
            "record_id": rec.id,
            "source": rec.source,
            "profile": _profile(rec),
            "contact": {"name": rec.name, "email": rec.contact_info.email, "phone": rec.contact_info.phone,
                        "linkedin_url": rec.contact_info.linkedin_url},
            "classification": {"category": triage["category"], "score": triage["score"], "confidence": triage["confidence"]},
            "action": {"channel": triage["channel"], "tone": triage["tone"], "escalate": triage["escalate"],
                       "flag_reason": (triage["flag_reason"] or "").strip() or None},
            "message": {"subject": msg["subject"][:200], "body": msg["body"][:1500]},
            "rationale": triage["rationale"][:1200],
            "agent_meta": {"llm_used": llm_used, "model": self.llm.model if llm_used else "rule-based",
                           "steps": len(trace) + 1, "latency_ms": int((time.monotonic() - t0) * 1000),
                           "processed_at": utcnow()},
        }
        step("assemble", decision=f"{payload['classification']['category']}/{payload['action']['channel']}/escalate={payload['action']['escalate']}")
        return payload, trace
