"""Mock CMS/CRM: FastAPI service that enforces the outbound JSON-schema contract.

Run:  uvicorn cms.app:app --port 8000
Storage: SQLite (cms/cms.db, upsert by record_id => idempotent) + append-only JSONL audit log.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator

BASE = Path(__file__).resolve().parent
SCHEMA = json.loads((BASE.parent / "schemas" / "outbound_record.schema.json").read_text(encoding="utf-8"))
validator = Draft202012Validator(SCHEMA)
DB = BASE / "cms.db"
AUDIT = BASE.parent / "output" / "cms_received.jsonl"
AUDIT.parent.mkdir(exist_ok=True)
lock = threading.Lock()

app = FastAPI(title="Mock CMS", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET"], allow_headers=["*"])


def conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS records (
        cms_id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT UNIQUE, source TEXT, category TEXT,
        channel TEXT, escalate INTEGER, payload TEXT, received_at TEXT)""")
    return c


@app.post("/records", status_code=201)
async def create_record(request: Request, idempotency_key: str | None = Header(default=None),
                        x_chaos: str | None = Header(default=None)):
    if x_chaos == "503":  # simulated outage to exercise client retries
        raise HTTPException(status_code=503, detail="simulated CMS outage")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body is not valid JSON")
    errors = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in validator.iter_errors(payload)]
    if errors:
        raise HTTPException(status_code=422, detail={"contract_violations": errors[:10]})
    if idempotency_key and idempotency_key != payload["record_id"]:
        raise HTTPException(status_code=409, detail="Idempotency-Key must equal record_id")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with lock, conn() as c:
        c.execute("""INSERT INTO records (record_id, source, category, channel, escalate, payload, received_at)
                     VALUES (?,?,?,?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET
                     source=excluded.source, category=excluded.category, channel=excluded.channel,
                     escalate=excluded.escalate, payload=excluded.payload, received_at=excluded.received_at""",
                  (payload["record_id"], payload["source"], payload["classification"]["category"],
                   payload["action"]["channel"], int(payload["action"]["escalate"]), json.dumps(payload), now))
        cms_id = c.execute("SELECT cms_id FROM records WHERE record_id=?", (payload["record_id"],)).fetchone()[0]
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"cms_id": cms_id, "received_at": now, "payload": payload}) + "\n")
    return {"status": "stored", "cms_id": cms_id, "record_id": payload["record_id"]}


@app.get("/records")
def list_records(source: str | None = None, escalate: bool | None = None):
    q, args = "SELECT cms_id, payload, received_at FROM records WHERE 1=1", []
    if source:
        q += " AND source=?"; args.append(source)
    if escalate is not None:
        q += " AND escalate=?"; args.append(int(escalate))
    with conn() as c:
        return [{"cms_id": i, "received_at": t, **json.loads(p)} for i, p, t in c.execute(q, args)]


@app.delete("/records")
def clear_records():
    with lock, conn() as c:
        c.execute("DELETE FROM records")
        try:
            c.execute("DELETE FROM sqlite_sequence WHERE name='records'")
        except Exception:
            pass
    if AUDIT.exists():
        try:
            AUDIT.unlink()
        except Exception:
            pass
    return {"status": "cleared", "count": 0}


@app.get("/records/{record_id}")
def get_record(record_id: str):
    with conn() as c:
        row = c.execute("SELECT cms_id, payload FROM records WHERE record_id=?", (record_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return {"cms_id": row[0], **json.loads(row[1])}


@app.get("/schema")
def schema():
    return SCHEMA


@app.get("/health")
def health():
    return {"ok": True}
