"""Stage 3: push contract-validated payloads to the CMS with retry/backoff and idempotency."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests
from jsonschema import Draft202012Validator

log = logging.getLogger("deliver")

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "outbound_record.schema.json"
_validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


class ContractError(Exception):
    pass


def validate_contract(payload: dict) -> None:
    errors = sorted(_validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        raise ContractError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5]))


def push(payload: dict, cms_url: str, chaos: bool = False, retries: int = 4) -> dict:
    """Returns {'status': 'delivered'|'failed', 'attempts': n, ...}. Never raises for delivery problems.
    4xx (contract/validation) is NOT retried; 5xx/timeouts are. Idempotency-Key = record_id, so retries are safe."""
    try:
        validate_contract(payload)  # fail fast on our side before hitting the network
    except ContractError as e:
        log.error("[%s] outbound contract violation: %s", payload.get("record_id"), e)
        return {"status": "failed", "attempts": 0, "error": f"contract: {e}"}
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            headers = {"Idempotency-Key": payload["record_id"]}
            if chaos and attempt == 1:
                headers["X-Chaos"] = "503"  # CMS simulates an outage for this call only
            r = requests.post(f"{cms_url.rstrip('/')}/records", json=payload, headers=headers, timeout=10)
            if r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                log.error("[%s] CMS rejected (HTTP %s): %s", payload["record_id"], r.status_code, r.text[:200])
                return {"status": "failed", "attempts": attempt, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            body = r.json()
            log.info("[%s] delivered to CMS (attempt %d) cms_id=%s", payload["record_id"], attempt, body.get("cms_id"))
            return {"status": "delivered", "attempts": attempt, "cms_id": body.get("cms_id"),
                    "http_status": r.status_code}
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            log.warning("[%s] delivery attempt %d/%d failed: %s", payload["record_id"], attempt, retries, e)
            if attempt == retries:
                return {"status": "failed", "attempts": attempt, "error": str(e)}
            time.sleep(delay)
            delay *= 2
    return {"status": "failed", "attempts": retries, "error": "unknown"}
