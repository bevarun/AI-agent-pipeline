"""Source A: LinkedIn Sales Navigator-style CSV -> unified Record.

Column names in exports vary, so we map through an alias table and keep every
unmapped column in `context["extra"]` instead of dropping data.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from pathlib import Path

from .models import ContactInfo, PrioritySignal, Record

log = logging.getLogger("ingest.csv")

ALIASES = {
    "first_name": ["first_name", "first name", "firstname", "given_name"],
    "last_name": ["last_name", "last name", "lastname", "surname", "family_name"],
    "full_name": ["name", "full_name", "full name", "lead_name", "contact_name"],
    "email": ["email", "email_address", "work_email", "e-mail"],
    "phone": ["phone", "phone_number", "mobile", "telephone"],
    "linkedin_url": ["linkedin_url", "linkedin", "profile_url", "linkedin_profile", "url"],
    "title": ["title", "job_title", "position", "role"],
    "company": ["company", "company_name", "organization", "account_name", "employer"],
    "industry": ["industry", "sector"],
    "location": ["location", "city", "region", "geography", "country"],
    "company_size": ["company_size", "employees", "headcount", "company_headcount"],
    "headline": ["linkedin_headline", "headline", "about", "summary"],
    "activity": ["recent_activity", "activity", "notes", "note"],
    "connection_note": ["connection_note", "connection", "message"],
    "lead_id": ["lead_id", "id"],
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SENIORITY = [  # (regex, points, label)
    (r"\b(founder|co-founder|owner|ceo|cto|coo|cfo|cmo|chief|president|managing partner)\b", 40, "C-level/owner"),
    (r"\b(vp|vice president|head of|director|partner)\b", 30, "VP/Director"),
    (r"\b(manager|lead|principal|senior)\b", 18, "Manager/Lead"),
    (r"\b(intern|student|assistant|junior|associate)\b", 3, "Junior"),
]


class RowError(ValueError):
    pass


def _pick(row_norm: dict, key: str):
    for alias in ALIASES[key]:
        v = row_norm.get(alias)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


STRONG_INTENT = r"(requested a (demo|call)|pricing|asking about|looking for|struggling|hiring|warm intro|pilot|recommendations)"
WEAK_INTENT = r"(liked|shared|commented|attended|posted|connected)"
COLD_SIGNAL = r"(no recent activity|dormant|cold outreach|no note|cold profile|imported|badge scan|saved search)"


def _score(title, company, email, linkedin, size, text) -> tuple[int, list[str]]:
    score, factors = 10, []
    for pattern, pts, label in SENIORITY:
        if title and re.search(pattern, title.lower()):
            score += pts
            factors.append(f"seniority:{label}(+{pts})")
            break
    if email:
        score += 10
        factors.append("has_email(+10)")
    if linkedin:
        score += 5
        factors.append("has_linkedin(+5)")
    nums = [int(n) for n in re.findall(r"\d+", (size or "").replace(",", ""))]
    if nums and max(nums) >= 11:
        score += 10
        factors.append(f"company_size>=11({size})(+10)")
    low = (text or "").lower()
    if re.search(STRONG_INTENT, low):
        score += 25
        factors.append("strong_intent_signal(+25)")
    elif re.search(WEAK_INTENT, low):
        score += 10
        factors.append("weak_engagement(+10)")
    if re.search(COLD_SIGNAL, low):
        score -= 5
        factors.append("cold_signal(-5)")
    return max(0, min(score, 100)), factors


def row_to_record(row: dict, line_no: int) -> Record:
    norm = {re.sub(r"[\s\-]+", "_", (k or "").strip().lower()): v for k, v in row.items()}
    first, last, full = _pick(norm, "first_name"), _pick(norm, "last_name"), _pick(norm, "full_name")
    name = full or " ".join(p for p in (first, last) if p)
    if not name:
        raise RowError("missing name")
    email = _pick(norm, "email")
    if email and not EMAIL_RE.match(email):
        raise RowError(f"malformed email {email!r}")
    # Sales Navigator exports may carry no email/URL: the lead is still reachable via LinkedIn DM.
    linkedin = _pick(norm, "linkedin_url")
    title, company = _pick(norm, "title"), _pick(norm, "company")
    size = _pick(norm, "company_size")
    headline, activity, conn_note = _pick(norm, "headline"), _pick(norm, "activity"), _pick(norm, "connection_note")

    used = {a for k in ALIASES for a in ALIASES[k]}
    extra = {k: v for k, v in norm.items() if k not in used and v and str(v).strip()}
    score, factors = _score(title, company, email, linkedin, size, f"{activity} {conn_note}")
    lead_id = _pick(norm, "lead_id")
    stable = lead_id.lower() if lead_id else hashlib.sha1((email or linkedin or name).lower().encode()).hexdigest()[:10]
    return Record(
        id=f"lead-{stable}",
        source="linkedin_csv",
        name=name,
        contact_info=ContactInfo(email=email, phone=_pick(norm, "phone"), linkedin_url=linkedin),
        context={k: v for k, v in {
            "title": title, "company": company, "industry": _pick(norm, "industry"),
            "location": _pick(norm, "location"), "company_size": size, "headline": headline,
            "recent_activity": activity, "connection_note": conn_note, "extra": extra or None,
        }.items() if v},
        priority_signal=PrioritySignal(score=score, factors=factors),
        raw_payload={"csv_line": line_no, **row},
    )


def load_leads(path: str, dead_letter: str = "output/dead_letter.jsonl") -> list[Record]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path} not found - place mock_leads.csv in the project root or set LEADS_CSV")
    records, seen = [], set()
    Path(dead_letter).parent.mkdir(parents=True, exist_ok=True)
    with p.open(newline="", encoding="utf-8-sig") as f, open(dead_letter, "a", encoding="utf-8") as dl:
        for i, row in enumerate(csv.DictReader(f), start=2):  # line 1 = header
            try:
                rec = row_to_record(row, i)
                if rec.id in seen:
                    raise RowError("duplicate lead")
                seen.add(rec.id)
                records.append(rec)
            except RowError as e:
                log.warning("CSV line %s rejected: %s", i, e)
                dl.write(json.dumps({"source": "linkedin_csv", "line": i, "error": str(e), "row": row}) + "\n")
    log.info("CSV: %d valid leads loaded", len(records))
    return records
