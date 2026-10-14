"""In-process job store + single background worker.

- Jobs run one at a time (the LLM free tier is rate-limited and the CMS is a single SQLite file).
- Every progress event is appended to an in-memory list *and* `output/jobs/<id>/events.jsonl`,
  so SSE clients can reconnect with Last-Event-ID and finished jobs survive a server restart.
- WARNING+ log records produced on the worker thread (retries, backoff, rejected rows...) are
  forwarded as `log` events, which is what makes the error reporting detailed without touching
  the ingest/agent/deliver modules.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any, Optional

from pipeline.models import setup_logging
from pipeline.runner import Cancelled, RunConfig, run

log = logging.getLogger("jobs")

BASE_DIR = Path(__file__).resolve().parent.parent
JOBS_DIR = BASE_DIR / "output" / "jobs"
TERMINAL = {"succeeded", "completed_with_errors", "failed", "cancelled", "interrupted"}
MAX_QUEUED = 5
MAX_STEP_EVENTS_PER_JOB = 5000  # guard against unbounded memory on very large runs
_DUPLICATE_PREFIXES = ("CSV line ", "FHIR entry rejected", "[chaos] injected bad CSV row")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    config: dict[str, Any]
    input: dict[str, Any]
    created_at: str = field(default_factory=_now)
    status: str = "queued"
    stage: Optional[str] = None
    stages: dict[str, dict] = field(default_factory=dict)
    progress: dict[str, int] = field(default_factory=lambda: {
        "total": 0, "processed": 0, "delivered": 0, "failed": 0, "rejected": 0, "warnings": 0})
    current: Optional[dict] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[dict] = None
    cancel_requested: bool = False
    events: list[dict] = field(default_factory=list, repr=False)
    step_events: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def dir(self) -> Path:
        return JOBS_DIR / self.id

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def snapshot(self) -> dict:
        return {"status": self.status, "stage": self.stage, "progress": dict(self.progress),
                "current": self.current}

    def public(self) -> dict:
        return {
            "id": self.id, "status": self.status, "stage": self.stage, "stages": self.stages,
            "progress": self.progress, "current": self.current, "config": self.config, "input": self.input,
            "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at,
            "error": self.error, "summary": self.summary, "cancel_requested": self.cancel_requested,
            "event_count": len(self.events),
        }

    def events_after(self, seq: int) -> list[dict]:
        with self.lock:
            return self.events[seq:] if seq < len(self.events) else []


class _JobLogHandler(logging.Handler):
    """Forwards WARNING+ records emitted on the worker thread into the active job's event stream."""

    def __init__(self, manager: "JobManager"):
        super().__init__(level=logging.WARNING)
        self.manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        job = self.manager.active
        if job is None or record.thread != self.manager.worker_ident or record.name.startswith("jobs"):
            return
        try:
            msg = record.getMessage()
            if msg.startswith(_DUPLICATE_PREFIXES):  # already reported as structured `rejected` events
                return
            if record.exc_info and record.exc_info[1] is not None:
                msg += f" ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            self.manager.emit(job, "log", level=record.levelname.lower(), logger=record.name, message=msg[:1000])
        except Exception:
            pass


class JobManager:
    def __init__(self):
        setup_logging()
        if logging.getLogger().level > logging.INFO:
            logging.getLogger().setLevel(logging.INFO)
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.active: Optional[Job] = None
        self.worker_ident: Optional[int] = None
        self._lock = threading.Lock()
        self._load_history()
        logging.getLogger().addHandler(_JobLogHandler(self))
        self._worker = threading.Thread(target=self._loop, name="pipeline-worker", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------ persistence
    def _persist(self, job: Job) -> None:
        job.dir.mkdir(parents=True, exist_ok=True)
        target = job.dir / "job.json"
        data = json.dumps(job.public(), indent=2, default=str)
        for attempt in range(5):
            try:
                tmp = job.dir / f"job.json.tmp.{os.getpid()}"
                tmp.write_text(data, encoding="utf-8")
                os.replace(tmp, target)
                return
            except (PermissionError, OSError):
                if attempt == 4:
                    try:
                        target.write_text(data, encoding="utf-8")
                    except Exception:
                        pass
                else:
                    time.sleep(0.04 * (attempt + 1))
            except Exception:
                try:
                    target.write_text(data, encoding="utf-8")
                except Exception:
                    pass
                return

    def _load_history(self) -> None:
        for meta in sorted(JOBS_DIR.glob("*/job.json")):
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
                job = Job(id=d["id"], config=d.get("config", {}), input=d.get("input", {}),
                          created_at=d.get("created_at", _now()), status=d.get("status", "failed"),
                          stage=d.get("stage"), stages=d.get("stages", {}), progress=d.get("progress", {}),
                          started_at=d.get("started_at"), finished_at=d.get("finished_at"),
                          error=d.get("error"), summary=d.get("summary"))
                ev_file = meta.parent / "events.jsonl"
                if ev_file.exists():
                    with open(ev_file, encoding="utf-8") as f:
                        job.events = [json.loads(ln) for ln in f if ln.strip()]
                if not job.terminal:  # server stopped mid-run
                    job.status, job.error = "interrupted", "Server restarted while this job was running"
                    job.finished_at = job.finished_at or _now()
                    self._persist(job)
                self.jobs[job.id] = job
            except Exception as e:
                log.warning("Skipping unreadable job metadata %s: %s", meta, e)

    def clear_history(self) -> None:
        with self._lock:
            self.jobs.clear()
            self.active = None
            if JOBS_DIR.exists():
                for p in JOBS_DIR.glob("*"):
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)

    # ------------------------------------------------------------ events
    def emit(self, job: Job, type_: str, **data) -> None:
        if type_ == "agent_step":
            job.step_events += 1
            if job.step_events > MAX_STEP_EVENTS_PER_JOB:
                return
        self._apply(job, type_, data)
        with job.lock:
            ev = {"seq": len(job.events) + 1, "ts": _now(), "type": type_, "data": data, "job": job.snapshot()}
            job.events.append(ev)
        try:
            job.dir.mkdir(parents=True, exist_ok=True)
            with open(job.dir / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        except OSError:
            pass
        if type_ in ("status", "stage", "plan", "record_done", "record_failed"):
            self._persist(job)

    def _apply(self, job: Job, type_: str, d: dict) -> None:
        """Fold an event into the job's summary state (what GET /jobs/{id} returns)."""
        p = job.progress
        if type_ == "stage":
            job.stage = d["stage"]
            job.stages[d["stage"]] = {k: v for k, v in d.items() if k != "stage"}
        elif type_ == "plan":
            p["total"] = d["total"]
        elif type_ == "rejected":
            p["rejected"] = p.get("rejected", 0) + 1
        elif type_ == "record_started":
            job.current = {k: d[k] for k in ("index", "record_id", "name", "source")}
        elif type_ in ("record_done", "record_failed"):
            p["processed"] = p.get("processed", 0) + 1
            p["delivered" if type_ == "record_done" else "failed"] += 1
            job.current = None
        elif type_ == "log" and d.get("level") in ("warning", "error", "critical"):
            p["warnings"] = p.get("warnings", 0) + 1

    def _set_status(self, job: Job, status: str, **extra) -> None:
        job.status = status
        for k, v in extra.items():
            setattr(job, k, v)
        self.emit(job, "status", status=status, **({"error": job.error} if job.error else {}))

    # ------------------------------------------------------------ public API
    def submit(self, config: dict, input_info: dict, run_cfg: RunConfig) -> Job:
        with self._lock:
            pending = sum(1 for j in self.jobs.values() if j.status in ("queued", "running"))
            if pending >= MAX_QUEUED:
                raise RuntimeError(f"Too many pending jobs ({pending}); wait for one to finish")
            job = Job(id=uuid.uuid4().hex[:12], config=config, input=input_info)
            run_cfg.out_dir = job.dir
            job._run_cfg = run_cfg  # type: ignore[attr-defined]
            self.jobs[job.id] = job
        self._set_status(job, "queued")
        self.queue.put(job.id)
        return job

    def cancel(self, job: Job) -> Job:
        if job.terminal:
            return job
        job.cancel_requested = True
        if job.status == "queued":
            self._set_status(job, "cancelled", finished_at=_now())
        else:
            self.emit(job, "log", level="info", logger="jobs", message="Cancellation requested; stopping after the current record")
            self._persist(job)
        return job

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    # ------------------------------------------------------------ worker
    def _loop(self) -> None:
        self.worker_ident = threading.get_ident()
        while True:
            job = self.jobs.get(self.queue.get())
            if job is None or job.status != "queued":
                continue
            self.active = job
            try:
                self._run(job)
            finally:
                self.active = None

    def _run(self, job: Job) -> None:
        self._set_status(job, "running", started_at=_now())
        try:
            summary = run(job._run_cfg,  # type: ignore[attr-defined]
                          emit=lambda t, **d: self.emit(job, t, **d),
                          should_cancel=lambda: job.cancel_requested)
        except Cancelled:
            self._set_status(job, "cancelled", finished_at=_now())
            return
        except Exception as e:
            log.exception("Job %s crashed", job.id)
            self._set_status(job, "failed", finished_at=_now(), error=f"{type(e).__name__}: {e}")
            return
        job.summary = summary
        if summary["processed"] == 0 and summary["source_errors"]:
            errs = "; ".join(f"{s['source']}: {s['error']}" for s in summary["source_errors"])
            self._set_status(job, "failed", finished_at=_now(), error=f"No records could be ingested ({errs})")
        elif summary["failed"] or summary["source_errors"] or summary["rejected"]:
            self._set_status(job, "completed_with_errors", finished_at=_now())
        else:
            self._set_status(job, "succeeded", finished_at=_now())


_manager: Optional[JobManager] = None
_manager_lock = threading.Lock()


def get_manager() -> JobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager()
        return _manager
