"""Orchestration core shared by the CLI (run_pipeline.py) and the job API (jobs/).

ingest (CSV and/or FHIR) -> agent -> deliver, writing traces/results/report into `out_dir`.
Progress is reported through an optional `emit(type, **data)` observer so callers can stream it;
the processing logic itself lives in the existing ingest/agent/deliver modules and is not duplicated.
"""
from __future__ import annotations

import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("run")

Emit = Callable[..., None]


class Cancelled(Exception):
    """Raised between records when the caller asks the run to stop."""


@dataclass
class RunConfig:
    leads_csv: Optional[str]          # None => skip the CSV source
    fhir_base_url: Optional[str]      # None => skip the FHIR source
    cms_url: str = "http://127.0.0.1:8000"
    min_patients: int = 15
    limit: int = 0                    # max total records (split across sources), 0 = all
    chaos: bool = False
    out_dir: Path = field(default_factory=lambda: Path("output"))


def _noop(*_a, **_k) -> None:
    pass


def _read_new_lines(path: Path, skip: int) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()[skip:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for _ in f)


def _rejection_view(entry: dict) -> dict:
    """Compact, UI-friendly version of a dead-letter entry (full FHIR resources can be huge)."""
    view: dict[str, Any] = {"source": entry.get("source"), "error": entry.get("error")}
    if "line" in entry:
        view["line"] = entry["line"]
    if "row" in entry:
        view["row"] = entry["row"]
    if "resource" in entry:
        res = entry["resource"] or {}
        view["resource"] = {"resourceType": res.get("resourceType"), "id": res.get("id")}
    return view


def run(cfg: RunConfig, emit: Emit = _noop, should_cancel: Callable[[], bool] = lambda: False) -> dict:
    from .agent import Agent
    from .deliver import push
    from .ingest_csv import RowError, load_leads, row_to_record
    from .ingest_fhir import load_patients
    from .llm import GeminiClient

    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dead_letter = out / "dead_letter.jsonl"
    stats = {"delivered": 0, "failed": 0, "llm": 0, "fallback": 0, "rejected": 0}
    source_errors: list[dict] = []

    # ---- Stage 1a: CSV ingest
    leads = []
    if cfg.leads_csv:
        emit("stage", stage="ingest_csv", status="running")
        before = _count_lines(dead_letter)
        try:
            leads = load_leads(cfg.leads_csv, dead_letter=str(dead_letter))
            if cfg.chaos:  # deliberate bad rows so the dead-letter path is visible even with a clean CSV
                for bad in ({"name": "Broken Email", "email": "not-an-email"}, {"email": "noname@example.com"}):
                    try:
                        row_to_record(bad, -1)
                    except RowError as e:
                        log.warning("[chaos] injected bad CSV row rejected: %s", e)
                        with open(dead_letter, "a", encoding="utf-8") as dl:
                            dl.write(json.dumps({"source": "linkedin_csv", "error": str(e), "row": bad}) + "\n")
            rejected = _read_new_lines(dead_letter, before)
            for r in rejected:
                emit("rejected", **_rejection_view(r))
            stats["rejected"] += len(rejected)
            emit("stage", stage="ingest_csv", status="done", accepted=len(leads), rejected=len(rejected))
        except Exception as e:  # source-level failure: keep going with the other source
            log.exception("CSV ingest failed")
            source_errors.append({"source": "linkedin_csv", "error": f"{type(e).__name__}: {e}"})
            emit("stage", stage="ingest_csv", status="failed", error=f"{type(e).__name__}: {e}")
    else:
        emit("stage", stage="ingest_csv", status="skipped")

    if should_cancel():
        raise Cancelled()

    # ---- Stage 1b: FHIR ingest
    patients = []
    if cfg.fhir_base_url:
        emit("stage", stage="ingest_fhir", status="running", base_url=cfg.fhir_base_url)
        before = _count_lines(dead_letter)
        try:
            patients = load_patients(cfg.fhir_base_url, minimum=cfg.min_patients,
                                     inject_failures=cfg.chaos, dead_letter=str(dead_letter))
            rejected = _read_new_lines(dead_letter, before)
            for r in rejected:
                emit("rejected", **_rejection_view(r))
            stats["rejected"] += len(rejected)
            emit("stage", stage="ingest_fhir", status="done", accepted=len(patients), rejected=len(rejected))
        except Exception as e:
            log.exception("FHIR ingest failed")
            source_errors.append({"source": "fhir_emr", "error": f"{type(e).__name__}: {e}"})
            emit("stage", stage="ingest_fhir", status="failed", error=f"{type(e).__name__}: {e}")
    else:
        emit("stage", stage="ingest_fhir", status="skipped")

    if cfg.limit:
        if leads and patients:
            half = max(1, cfg.limit // 2)
            records = leads[:half] + patients[:cfg.limit - half]
        else:
            records = (leads or patients)[:cfg.limit]
    else:
        records = [r for pair in itertools.zip_longest(leads, patients) for r in pair if r]
    log.info("Ingested %d leads + %d patients; processing %d", len(leads), len(patients), len(records))
    emit("plan", total=len(records), leads=len(leads), patients=len(patients))

    # ---- Stage 2 + 3: agent + delivery
    llm = GeminiClient()
    if not llm.available:
        log.warning("GEMINI_API_KEY not set: agent will use the rule-based fallback for every record")
    agent = Agent(llm)
    report = ["# Sample output\n"]
    t_start = time.monotonic()
    emit("stage", stage="process", status="running", total=len(records), llm_available=llm.available)
    for i, rec in enumerate(records, 1):
        if should_cancel():
            emit("stage", stage="process", status="cancelled")
            raise Cancelled()
        log.info("=== (%d/%d) %s [%s] ===", i, len(records), rec.id, rec.source)
        emit("record_started", index=i, total=len(records), record_id=rec.id, name=rec.name,
             source=rec.source, pre_score=rec.priority_signal.score)
        try:
            payload, trace = agent.process(
                rec, on_step=lambda s, rid=rec.id: emit("agent_step", record_id=rid, **s))
        except Exception as e:  # one bad record must never take down the batch
            log.exception("[%s] agent crashed; record skipped", rec.id)
            stats["failed"] += 1
            emit("record_failed", index=i, record_id=rec.id, name=rec.name, source=rec.source,
                 stage="agent", error=f"{type(e).__name__}: {e}")
            continue
        delivery = push(payload, cfg.cms_url, chaos=cfg.chaos and i % 4 == 0)
        ok = delivery["status"] == "delivered"
        stats["delivered" if ok else "failed"] += 1
        stats["llm" if payload["agent_meta"]["llm_used"] else "fallback"] += 1
        with open(out / "traces.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"record_id": rec.id, "trace": trace, "delivery": delivery}) + "\n")
        with open(out / "results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"input": rec.model_dump(exclude={"raw_payload"}), "output": payload,
                                "delivery": delivery}) + "\n")
        result = {
            "index": i, "record_id": rec.id, "name": rec.name, "source": rec.source,
            "category": payload["classification"]["category"], "score": payload["classification"]["score"],
            "confidence": payload["classification"]["confidence"], "channel": payload["action"]["channel"],
            "escalate": payload["action"]["escalate"], "llm_used": payload["agent_meta"]["llm_used"],
            "delivery": delivery,
        }
        if ok:
            emit("record_done", **result)
        else:
            emit("record_failed", **result, stage="delivery", error=delivery.get("error", "delivery failed"))
        report.append(
            f"## {i}. {rec.name} (`{rec.id}`, {rec.source})\n"
            f"**Input:** {json.dumps(rec.context, default=str)[:400]}  \n"
            f"**Pre-score:** {rec.priority_signal.score} {rec.priority_signal.factors}\n\n"
            f"**Decision:** {payload['classification']['category']} (score {payload['classification']['score']}, "
            f"{payload['classification']['confidence']} confidence) -> {payload['action']['channel']}, tone={payload['action']['tone']}, "
            f"escalate={payload['action']['escalate']}  \n"
            f"**Rationale:** {payload['rationale']}\n\n"
            f"**Message:** *{payload['message']['subject']}*\n> {payload['message']['body']}\n\n"
            f"**CMS delivery:** {delivery}\n")

    (out / "sample_output.md").write_text("\n".join(report), encoding="utf-8")
    summary = {
        **stats,
        "processed": len(records),
        "leads": len(leads),
        "patients": len(patients),
        "source_errors": source_errors,
        "duration_s": round(time.monotonic() - t_start, 1),
        "llm_calls": llm.calls,
        "tokens_in": llm.tokens_in,
        "tokens_out": llm.tokens_out,
    }
    emit("stage", stage="process", status="done", **{k: stats[k] for k in ("delivered", "failed")})
    log.info("DONE in %.0fs | %s | dead-lettered=%d | LLM calls=%d tokens in/out=%d/%d",
             summary["duration_s"], stats, stats["rejected"], llm.calls, llm.tokens_in, llm.tokens_out)
    return summary
