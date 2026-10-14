"""Source B: SMART Health IT public FHIR R4 sandbox -> unified Record.

Synthetic data only. We pull Patient resources (paged), then Condition and
Appointment per patient, and validate each Patient defensively since FHIR
entries in the wild are frequently sparse or malformed.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .models import ContactInfo, PrioritySignal, Record

log = logging.getLogger("ingest.fhir")

CHRONIC_KEYWORDS = ("diabet", "hypertens", "heart", "cardiac", "asthma", "copd", "cancer", "neoplasm",
                    "kidney", "renal", "depress", "stroke", "obes", "coronary", "anemia")


class FhirError(Exception):
    pass


def fhir_get(url: str, params: Optional[dict] = None, retries: int = 4, timeout: float = 20.0) -> dict:
    """GET with exponential backoff on timeouts, connection errors, 429 and 5xx."""
    delay = 1.5
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"Accept": "application/fhir+json"})
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            if attempt == retries:
                raise FhirError(f"GET {url} failed after {retries} attempts: {e}") from e
            log.warning("FHIR GET attempt %d/%d failed (%s: %s); retrying in %.1fs",
                        attempt, retries, type(e).__name__, e, delay)
            time.sleep(delay)
            delay *= 2
    raise FhirError("unreachable")


def _age(birth: Optional[str]) -> Optional[int]:
    if not birth:
        return None
    try:
        b = datetime.strptime(birth[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    t = date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def _patient_name(p: dict) -> str:
    names = p.get("name") or []
    if not names:
        raise ValueError("Patient has no name[]")
    official = next((n for n in names if n.get("use") == "official"), names[0])
    given = " ".join(official.get("given") or [])
    full = f"{given} {official.get('family', '')}".strip()
    if not full:
        raise ValueError("Patient name empty")
    return full


def _telecom(p: dict, system: str) -> Optional[str]:
    for t in p.get("telecom") or []:
        if t.get("system") == system and t.get("value"):
            return t["value"]
    return None


def validate_patient(res: dict) -> None:
    if res.get("resourceType") != "Patient":
        raise ValueError(f"unexpected resourceType {res.get('resourceType')!r}")
    if not res.get("id"):
        raise ValueError("Patient missing id")


def _condition_label(c: dict) -> Optional[str]:
    code = c.get("code") or {}
    if code.get("text"):
        return code["text"]
    for cd in code.get("coding") or []:
        if cd.get("display"):
            return cd["display"]
    return None


def _is_active(c: dict) -> bool:
    codings = (c.get("clinicalStatus") or {}).get("coding") or []
    return not codings or any(cd.get("code") in ("active", "recurrence", "relapse") for cd in codings)


def patient_to_record(patient: dict, conditions: list[dict], appointments: list[dict]) -> Record:
    validate_patient(patient)
    name = _patient_name(patient)
    age = _age(patient.get("birthDate"))
    active = sorted({l for c in conditions if _is_active(c) and (l := _condition_label(c))})
    chronic = [c for c in active if any(k in c.lower() for k in CHRONIC_KEYWORDS)]
    now = datetime.now(timezone.utc).isoformat()
    upcoming = [a for a in appointments if a.get("status") in ("booked", "pending", "proposed")
                and (a.get("start") or "") >= now]
    addr = (patient.get("address") or [{}])[0]
    address = ", ".join(x for x in [", ".join(addr.get("line") or []), addr.get("city"), addr.get("state")] if x) or None
    email, phone = _telecom(patient, "email"), _telecom(patient, "phone")

    score, factors = 20, []
    if age is not None and (age >= 65 or age <= 5):
        score += 15
        factors.append(f"age_risk_band({age})(+15)")
    if chronic:
        pts = min(10 * len(chronic), 30)
        score += pts
        factors.append(f"chronic_conditions:{len(chronic)}(+{pts})")
    if len(active) >= 5:
        score += 10
        factors.append("high_condition_burden(+10)")
    if not upcoming:
        score += 15
        factors.append("no_upcoming_appointment(+15)")
    if not (email or phone):
        factors.append("no_contact_info")

    return Record(
        id=f"pt-{patient['id']}",
        source="fhir_emr",
        name=name,
        contact_info=ContactInfo(email=email, phone=phone, address=address),
        context={
            "age": age, "gender": patient.get("gender"),
            "active_conditions": active[:10], "chronic_conditions": chronic[:10],
            "upcoming_appointments": len(upcoming), "total_appointments": len(appointments),
            "language": ((patient.get("communication") or [{}])[0].get("language") or {}).get("text"),
        },
        priority_signal=PrioritySignal(score=min(score, 100), factors=factors),
        raw_payload={"patient": patient, "conditions": conditions[:20], "appointments": appointments[:20]},
    )


def _entries(bundle: dict) -> list[dict]:
    return [e.get("resource") for e in bundle.get("entry", []) if isinstance(e, dict) and e.get("resource")]


def _next_link(bundle: dict) -> Optional[str]:
    return next((l.get("url") for l in bundle.get("link", []) if l.get("relation") == "next"), None)


def load_patients(base_url: str, minimum: int = 15, inject_failures: bool = False,
                  dead_letter: str = "output/dead_letter.jsonl") -> list[Record]:
    base_url = base_url.rstrip("/")
    Path(dead_letter).parent.mkdir(parents=True, exist_ok=True)
    resources: list[dict] = []
    url, params = f"{base_url}/Patient", {"_count": 20}
    if inject_failures:
        # Deliberate failure #1: simulated API timeout on the first call to exercise retry/backoff.
        log.warning("[chaos] simulating timeout on first Patient request")
        try:
            fhir_get(url, params, retries=1, timeout=0.001)
        except FhirError as e:
            log.warning("[chaos] first attempt failed as expected: %s", e)
    pages = 0
    while url and len(resources) < minimum + 5 and pages < 5:
        bundle = fhir_get(url, params)
        resources.extend(_entries(bundle))
        url, params, pages = _next_link(bundle), None, pages + 1
    if inject_failures:
        # Deliberate failure #2: malformed FHIR entries that must be dead-lettered, not crash the run.
        resources.insert(1, {"resourceType": "Patient", "name": [{"given": ["Ghost"]}]})  # no id
        resources.insert(3, {"resourceType": "Observation", "id": "not-a-patient"})

    records: list[Record] = []
    with open(dead_letter, "a", encoding="utf-8") as dl:
        for res in resources:
            try:
                validate_patient(res)
                pid = res["id"]
                try:
                    conds = _entries(fhir_get(f"{base_url}/Condition", {"patient": pid, "_count": 50}))
                    appts = _entries(fhir_get(f"{base_url}/Appointment", {"patient": pid, "_count": 50}))
                except FhirError as e:  # degrade gracefully: patient still usable without enrichment
                    log.warning("Patient %s: related-resource fetch failed (%s); continuing without", pid, e)
                    conds, appts = [], []
                records.append(patient_to_record(res, conds, appts))
            except (ValueError, KeyError, TypeError) as e:
                log.warning("FHIR entry rejected (%s id=%s): %s", res.get("resourceType"), res.get("id"), e)
                dl.write(json.dumps({"source": "fhir_emr", "error": str(e), "resource": res}) + "\n")
    log.info("FHIR: %d valid patients loaded", len(records))
    return records
