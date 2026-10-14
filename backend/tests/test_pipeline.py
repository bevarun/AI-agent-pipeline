"""Offline tests (no network, no API key): python -m pytest -q"""
import copy

import pytest

from pipeline.agent import Agent, apply_guardrails, critique_message, perceive
from pipeline.deliver import ContractError, validate_contract
from pipeline.ingest_csv import RowError, row_to_record
from pipeline.ingest_fhir import patient_to_record
from pipeline.llm import GeminiClient

PATIENT = {"resourceType": "Patient", "id": "p1", "name": [{"given": ["Ana"], "family": "Diaz"}],
           "birthDate": "1950-01-01", "gender": "female", "telecom": [{"system": "phone", "value": "555-0100"}]}
COND = [{"resourceType": "Condition", "code": {"text": "Essential hypertension"},
         "clinicalStatus": {"coding": [{"code": "active"}]}}]


def agent():
    return Agent(GeminiClient(api_key=""))  # forces rule-based fallback


def test_csv_rejects_bad_rows():
    with pytest.raises(RowError):
        row_to_record({"name": "X", "email": "nope"}, 2)
    with pytest.raises(RowError):
        row_to_record({"email": "a@b.co"}, 3)


def test_csv_maps_aliases():
    r = row_to_record({"First Name": "Sam", "Last Name": "Lee", "Job Title": "CEO", "Email": "s@x.io"}, 2)
    assert r.name == "Sam Lee" and r.priority_signal.score > 50


def test_fhir_normalisation_and_malformed():
    r = patient_to_record(PATIENT, COND, [])
    assert r.id == "pt-p1" and r.context["chronic_conditions"] == ["Essential hypertension"]
    with pytest.raises(ValueError):
        patient_to_record({"resourceType": "Patient", "name": []}, [], [])


def test_fallback_end_to_end_matches_contract():
    for rec in (patient_to_record(PATIENT, COND, []),
                row_to_record({"name": "Sam Lee", "title": "CEO", "email": "s@x.io"}, 2)):
        payload, trace = agent().process(rec)
        validate_contract(payload)
        assert payload["agent_meta"]["llm_used"] is False and trace


def test_contract_violation_detected():
    rec = row_to_record({"name": "Sam Lee", "email": "s@x.io"}, 2)
    payload, _ = agent().process(rec)
    bad = copy.deepcopy(payload)
    bad["action"]["channel"] = "carrier_pigeon"
    with pytest.raises(ContractError):
        validate_contract(bad)


def test_guardrail_no_channel_escalates_and_phi_critic():
    rec = patient_to_record({**PATIENT, "telecom": []}, COND, [])
    t = {"category": "routine", "channel": "phone", "tone": "warm", "escalate": False, "flag_reason": None}
    t, notes = apply_guardrails(rec, perceive(rec), t)
    assert t["channel"] in ("portal", "none") or t["escalate"]
    issues = critique_message(rec, {"subject": "Hi", "body": "Hello Ana, about your hypertension follow-up, please call us."})
    assert any("PHI" in i for i in issues)
