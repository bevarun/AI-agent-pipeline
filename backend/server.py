"""Single entrypoint: job-management API + the existing mock CMS, on one port.

Run (from backend/):  uvicorn server:app --port 8000
The CMS app is mounted unchanged, so `uvicorn cms.app:app` still works on its own.
"""
from dotenv import load_dotenv

load_dotenv()  # before importing modules that read env (GEMINI_API_KEY, FHIR_BASE_URL, CMS_URL)

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from cms.app import app as cms_app  # noqa: E402
from jobs.manager import get_manager  # noqa: E402
from jobs.router import router as jobs_router  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_manager()  # start the worker + restore job history eagerly
    yield


def reset_all_data():
    import sqlite3
    import shutil
    from pathlib import Path
    base = Path(__file__).resolve().parent
    db_path = base / "cms" / "cms.db"
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as c:
                c.execute("DELETE FROM records")
                try:
                    c.execute("DELETE FROM sqlite_sequence WHERE name='records'")
                except Exception:
                    pass
        except Exception:
            pass
    out_dir = base / "output"
    if out_dir.exists():
        for item in out_dir.iterdir():
            if item.name == ".gitkeep":
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink(missing_ok=True)
                except Exception:
                    pass
    try:
        get_manager().clear_history()
    except Exception:
        pass
    return {"status": "all_data_cleared", "records": 0, "jobs": 0}


app = FastAPI(title="Intake pipeline API", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/pipeline/reset")
def handle_reset():
    return reset_all_data()


app.include_router(jobs_router)
app.mount("/", cms_app)  # /records, /schema, /health ... (must be last: it matches every path)
