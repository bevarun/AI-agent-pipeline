"""Offline tests for the job layer (no network, no API key): python -m pytest -q"""
import pytest

from jobs.uploads import UploadError, analyse
from pipeline.runner import Cancelled, RunConfig, run

CSV = "name,email,title,company\nSam Lee,s@x.io,CEO,Acme\nBad Row,not-an-email,CTO,Beta\nAna Diaz,a@d.io,Intern,Gamma\n"


def test_upload_preview_uses_ingest_validation():
    p = analyse(CSV)["preview"]
    assert p["rows"] == 3 and p["validation"]["valid"] == 2 and p["validation"]["rejected"] == 1
    assert p["validation"]["errors"][0]["line"] == 3 and p["mapped"]["email"] == "email"


def test_upload_semicolon_is_normalised_to_comma():
    out = analyse(CSV.replace(",", ";"))
    assert out["preview"]["delimiter"] == "semicolon" and out["normalised"].startswith("name,email")


def test_upload_requires_name_column():
    with pytest.raises(UploadError):
        analyse("email,title\na@b.co,CEO\n")


def _offline(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr("pipeline.deliver.push",
                        lambda payload, url, chaos=False: {"status": "delivered", "attempts": 1, "cms_id": 1})


def test_runner_emits_progress_and_dead_letters(tmp_path, monkeypatch):
    _offline(monkeypatch)
    src = tmp_path / "leads.csv"
    src.write_text(CSV, encoding="utf-8")
    events = []
    summary = run(RunConfig(leads_csv=str(src), fhir_base_url=None, out_dir=tmp_path / "out"),
                  emit=lambda t, **d: events.append((t, d)))
    types = [t for t, _ in events]
    assert summary["delivered"] == 2 and summary["rejected"] == 1 and summary["fallback"] == 2
    assert "plan" in types and types.count("record_done") == 2 and "agent_step" in types
    assert any(t == "rejected" and d["line"] == 3 for t, d in events)
    assert (tmp_path / "out" / "results.jsonl").exists()


def test_runner_degrades_when_a_source_fails(tmp_path, monkeypatch):
    _offline(monkeypatch)
    summary = run(RunConfig(leads_csv=str(tmp_path / "missing.csv"), fhir_base_url=None, out_dir=tmp_path))
    assert summary["processed"] == 0 and summary["source_errors"][0]["source"] == "linkedin_csv"


def test_runner_cancellation(tmp_path, monkeypatch):
    _offline(monkeypatch)
    src = tmp_path / "leads.csv"
    src.write_text(CSV, encoding="utf-8")
    with pytest.raises(Cancelled):
        run(RunConfig(leads_csv=str(src), fhir_base_url=None, out_dir=tmp_path / "out"), should_cancel=lambda: True)
