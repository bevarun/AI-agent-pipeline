"""FHIR source selection: curated public R4 sandboxes + custom URL with a connectivity probe."""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import requests

PRESETS = [
    {"id": "smart", "label": "SMART Health IT sandbox", "base_url": "https://r4.smarthealthit.org",
     "description": "Synthetic Synthea patients with conditions & appointments. Default source."},
    {"id": "hapi", "label": "HAPI FHIR public server", "base_url": "https://hapi.fhir.org/baseR4",
     "description": "Community test server. Large, noisy data set; good for exercising validation."},
]


class SourceError(ValueError):
    pass


def default_base_url() -> str:
    return os.getenv("FHIR_BASE_URL", PRESETS[0]["base_url"])


def validate_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise SourceError("FHIR base URL must be an absolute http(s) URL, e.g. https://r4.smarthealthit.org")
    return url


def probe(url: str, timeout: float = 8.0) -> dict:
    """GET {base}/metadata (CapabilityStatement). Never raises; returns a diagnostic dict."""
    try:
        base = validate_url(url)
    except SourceError as e:
        return {"ok": False, "base_url": url, "error": str(e)}
    t0 = time.monotonic()
    try:
        r = requests.get(f"{base}/metadata", timeout=timeout, headers={"Accept": "application/fhir+json"})
        latency = round((time.monotonic() - t0) * 1000)
        if r.status_code >= 400:
            return {"ok": False, "base_url": base, "latency_ms": latency,
                    "error": f"Server answered HTTP {r.status_code} for /metadata"}
        body = r.json()
        if body.get("resourceType") != "CapabilityStatement":
            return {"ok": False, "base_url": base, "latency_ms": latency,
                    "error": f"/metadata returned {body.get('resourceType')!r}, not a CapabilityStatement"}
        resources = {res.get("type") for rest in body.get("rest", []) for res in rest.get("resource", [])}
        missing = [t for t in ("Patient", "Condition", "Appointment") if resources and t not in resources]
        return {"ok": True, "base_url": base, "latency_ms": latency, "fhir_version": body.get("fhirVersion"),
                "software": (body.get("software") or {}).get("name") or body.get("name"),
                "supports": sorted(t for t in ("Patient", "Condition", "Appointment") if t in resources),
                "warnings": [f"Server does not advertise {t}; enrichment will be skipped" for t in missing]}
    except requests.Timeout:
        return {"ok": False, "base_url": base, "error": f"Timed out after {timeout:.0f}s"}
    except requests.ConnectionError as e:
        return {"ok": False, "base_url": base, "error": f"Connection failed: {str(e)[:200]}"}
    except ValueError:
        return {"ok": False, "base_url": base, "error": "/metadata did not return JSON"}
