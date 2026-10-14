"""CSV upload: store, normalise and pre-validate with the *same* row_to_record used by ingest.

The preview tells the user before a run starts which columns were recognised and which rows
will be dead-lettered, so there are no surprises once the (slow, rate-limited) agent stage begins.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.ingest_csv import ALIASES, RowError, row_to_record

from .manager import BASE_DIR

UPLOAD_DIR = BASE_DIR / "output" / "uploads"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
SAMPLE_CSV = BASE_DIR / "mock_leads.csv"
_ID_RE = re.compile(r"^[a-f0-9]{12}$")


class UploadError(ValueError):
    pass


def _norm(header: str) -> str:
    return re.sub(r"[\s\-]+", "_", (header or "").strip().lower())  # mirrors ingest_csv.row_to_record


def _decode(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise UploadError("File is not UTF-8 or Windows-1252 text. Re-export the CSV as UTF-8.")


def analyse(text: str) -> dict:
    """Parse + validate CSV text. Returns the preview dict and the normalised (comma-delimited) text."""
    if not text.strip():
        raise UploadError("The file is empty.")
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        raise UploadError("No header row found. The first line must contain column names.")
    if len(set(_norm(h) for h in headers)) != len(headers):
        raise UploadError("Duplicate column names after normalisation (e.g. 'Email' and 'email').")

    rows = []
    for i, row in enumerate(reader, start=2):
        if i - 1 > MAX_ROWS:
            raise UploadError(f"Too many rows (limit {MAX_ROWS}).")
        if None in row:  # more cells than headers
            row.pop(None)
        if not any((v or "").strip() for v in row.values()):
            continue
        rows.append((i, row))
    if not rows:
        raise UploadError("The file has a header but no data rows.")

    normed = {_norm(h): h for h in headers}
    mapping = {}
    for field, aliases in ALIASES.items():
        hit = next((normed[a] for a in aliases if a in normed), None)
        if hit:
            mapping[field] = hit
    used = set(mapping.values())
    if not ({"full_name", "first_name", "last_name"} & mapping.keys()):
        raise UploadError("No name column recognised. Add a 'name' column (or 'first_name' / 'last_name').")

    errors, seen, valid = [], set(), 0
    for line, row in rows:
        try:
            rec = row_to_record(row, line)
            if rec.id in seen:
                raise RowError("duplicate lead")
            seen.add(rec.id)
            valid += 1
        except RowError as e:
            errors.append({"line": line, "error": str(e), "name": row.get(mapping.get("full_name", ""), "")
                           or " ".join(filter(None, [row.get(mapping.get("first_name", ""), ""),
                                                     row.get(mapping.get("last_name", ""), "")]))})

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for _, row in rows:
        w.writerow(row)

    return {
        "preview": {
            "rows": len(rows),
            "columns": headers,
            "delimiter": {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}[delimiter],
            "mapped": mapping,
            "unmapped": [h for h in headers if h not in used],
            "sample": [row for _, row in rows[:5]],
            "validation": {"valid": valid, "rejected": len(errors), "errors": errors[:50]},
        },
        "normalised": out.getvalue(),
    }


def save_upload(filename: str, raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise UploadError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if not filename.lower().endswith((".csv", ".txt")):
        raise UploadError("Only .csv files are accepted.")
    text, encoding = _decode(raw)
    result = analyse(text)
    upload_id = uuid.uuid4().hex[:12]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / f"{upload_id}.csv").write_text(result["normalised"], encoding="utf-8")
    meta = {"upload_id": upload_id, "filename": Path(filename).name[:200], "size": len(raw), "encoding": encoding,
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **result["preview"]}
    (UPLOAD_DIR / f"{upload_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def sample_preview() -> dict:
    if not SAMPLE_CSV.exists():
        raise UploadError("Sample file mock_leads.csv is not present in the backend folder.")
    text, encoding = _decode(SAMPLE_CSV.read_bytes())
    return {"upload_id": None, "filename": SAMPLE_CSV.name, "size": SAMPLE_CSV.stat().st_size,
            "encoding": encoding, "sample_file": True, **analyse(text)["preview"]}


def resolve(upload_id: str) -> tuple[Path, dict]:
    if not _ID_RE.match(upload_id or ""):
        raise UploadError("Invalid upload id.")
    path, meta = UPLOAD_DIR / f"{upload_id}.csv", UPLOAD_DIR / f"{upload_id}.json"
    if not path.exists() or not meta.exists():
        raise UploadError("Upload not found (it may have been removed). Upload the file again.")
    return path, json.loads(meta.read_text(encoding="utf-8"))
