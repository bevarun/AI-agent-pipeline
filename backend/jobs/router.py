"""Job API routes: uploads, FHIR sources, jobs, live progress (SSE), error reports, artifacts."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from pipeline.runner import RunConfig

from . import fhir_sources, uploads
from .manager import get_manager

router = APIRouter()
ARTIFACTS = {
    "results.jsonl": "application/x-ndjson",
    "traces.jsonl": "application/x-ndjson",
    "dead_letter.jsonl": "application/x-ndjson",
    "events.jsonl": "application/x-ndjson",
    "sample_output.md": "text/markdown",
}


# ------------------------------------------------------------------ models
class FhirOptions(BaseModel):
    enabled: bool = True
    base_url: Optional[str] = None
    min_patients: int = Field(15, ge=1, le=100)


class JobCreate(BaseModel):
    csv: Literal["upload", "sample", "none"] = "sample"
    upload_id: Optional[str] = None
    fhir: FhirOptions = Field(default_factory=FhirOptions)
    limit: int = Field(0, ge=0, le=1000)
    chaos: bool = False


def _job_or_404(job_id: str):
    job = get_manager().get(job_id)
    if not job:
        raise HTTPException(404, detail=f"Job {job_id} not found")
    return job


# ------------------------------------------------------------------ info + sources
@router.get("/pipeline/info")
def pipeline_info():
    from pipeline.llm import GeminiClient
    llm = GeminiClient()
    return {
        "llm_available": llm.available,
        "llm_model": llm.model,
        "cms_url": os.getenv("CMS_URL", "http://127.0.0.1:8000"),
        "fhir_presets": fhir_sources.PRESETS,
        "fhir_default": fhir_sources.default_base_url(),
        "max_upload_mb": uploads.MAX_BYTES // (1024 * 1024),
        "sample_csv_available": uploads.SAMPLE_CSV.exists(),
    }


class ProbeBody(BaseModel):
    base_url: str


@router.post("/sources/fhir/test")
def test_fhir(body: ProbeBody):
    return fhir_sources.probe(body.base_url)


# ------------------------------------------------------------------ uploads
@router.post("/uploads", status_code=201)
async def upload_csv(request: Request, filename: str = Query(..., min_length=1, max_length=255)):
    """Raw-body upload (Content-Type: text/csv). Avoids a python-multipart dependency."""
    declared = int(request.headers.get("content-length") or 0)
    if declared > uploads.MAX_BYTES:
        raise HTTPException(413, detail=f"File exceeds {uploads.MAX_BYTES // (1024 * 1024)} MB")
    raw = await request.body()
    try:
        return await asyncio.to_thread(uploads.save_upload, filename, raw)
    except uploads.UploadError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/uploads/sample")
def sample_upload():
    try:
        return uploads.sample_preview()
    except uploads.UploadError as e:
        raise HTTPException(404, detail=str(e))


# ------------------------------------------------------------------ jobs
@router.post("/jobs", status_code=202)
def create_job(body: JobCreate):
    leads_csv, input_info = None, {"csv": body.csv}
    try:
        if body.csv == "upload":
            if not body.upload_id:
                raise HTTPException(422, detail="upload_id is required when csv='upload'")
            path, meta = uploads.resolve(body.upload_id)
            leads_csv = str(path)
            input_info.update(filename=meta["filename"], rows=meta["rows"], upload_id=body.upload_id)
        elif body.csv == "sample":
            if not uploads.SAMPLE_CSV.exists():
                raise HTTPException(422, detail="Sample mock_leads.csv is missing on the server")
            leads_csv = str(uploads.SAMPLE_CSV)
            input_info.update(filename=uploads.SAMPLE_CSV.name)
        fhir_url = None
        if body.fhir.enabled:
            fhir_url = fhir_sources.validate_url(body.fhir.base_url or fhir_sources.default_base_url())
            input_info.update(fhir_base_url=fhir_url)
    except (uploads.UploadError, fhir_sources.SourceError) as e:
        raise HTTPException(422, detail=str(e))
    if not leads_csv and not fhir_url:
        raise HTTPException(422, detail="Select at least one source (CSV and/or FHIR)")

    cfg = RunConfig(leads_csv=leads_csv, fhir_base_url=fhir_url,
                    cms_url=os.getenv("CMS_URL", "http://127.0.0.1:8000"),
                    min_patients=body.fhir.min_patients, limit=body.limit, chaos=body.chaos)
    try:
        job = get_manager().submit(body.model_dump(), input_info, cfg)
    except RuntimeError as e:
        raise HTTPException(429, detail=str(e))
    return job.public()


@router.get("/jobs")
def list_jobs(limit: int = Query(30, ge=1, le=200)):
    return [j.public() for j in get_manager().list()[:limit]]


@router.delete("/jobs")
def clear_jobs():
    get_manager().clear_history()
    return {"status": "cleared"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return _job_or_404(job_id).public()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return get_manager().cancel(_job_or_404(job_id)).public()


@router.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str, after: int = Query(0, ge=0),
                     last_event_id: Optional[str] = Header(default=None)):
    """Server-Sent Events. Replays from `after` (or Last-Event-ID on reconnect), then streams live.
    Sends a final `end` message once the job is terminal and fully replayed."""
    job = _job_or_404(job_id)
    cursor = int(last_event_id) if (last_event_id or "").isdigit() else after

    async def stream():
        nonlocal cursor
        yield "retry: 3000\n\n"
        last_beat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            batch = job.events_after(cursor)
            for ev in batch:
                cursor = ev["seq"]
                yield f"id: {ev['seq']}\ndata: {json.dumps(ev, default=str)}\n\n"
            if batch:
                last_beat = time.monotonic()
            elif job.terminal and cursor >= len(job.events):
                yield f"data: {json.dumps({'type': 'end', 'job': job.snapshot()})}\n\n"
                return
            elif time.monotonic() - last_beat > 15:
                yield ": keep-alive\n\n"
                last_beat = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.get("/jobs/{job_id}/errors")
def job_errors(job_id: str):
    """Everything that went wrong in a run, grouped and annotated with the record being processed."""
    job = _job_or_404(job_id)
    report = {"job_id": job.id, "status": job.status, "error": job.error,
              "sources": [], "rejected": [], "records": [], "warnings": []}
    for ev in job.events_after(0):
        t, d, cur = ev["type"], ev["data"], (ev.get("job") or {}).get("current")
        if t == "stage" and d.get("status") == "failed":
            report["sources"].append({"stage": d["stage"], "error": d.get("error"), "ts": ev["ts"]})
        elif t == "rejected":
            report["rejected"].append({**d, "ts": ev["ts"]})
        elif t == "record_failed":
            report["records"].append({**d, "ts": ev["ts"]})
        elif t == "log" and d.get("level") in ("warning", "error", "critical"):
            report["warnings"].append({**d, "ts": ev["ts"], "record_id": (cur or {}).get("record_id")})
    report["counts"] = {k: len(report[k]) for k in ("sources", "rejected", "records", "warnings")}
    return report


@router.get("/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str):
    job = _job_or_404(job_id)
    return [{"name": n, "size": (job.dir / n).stat().st_size}
            for n in ARTIFACTS if (job.dir / n).exists()]


@router.get("/jobs/{job_id}/artifacts/{name}")
def get_artifact(job_id: str, name: str):
    job = _job_or_404(job_id)
    if name not in ARTIFACTS or not (job.dir / name).exists():
        raise HTTPException(404, detail="Artifact not found")
    return FileResponse(job.dir / name, media_type=ARTIFACTS[name], filename=f"{job.id}-{name}")
