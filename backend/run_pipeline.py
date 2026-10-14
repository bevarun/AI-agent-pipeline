"""Orchestrator: ingest (CSV + FHIR) -> agent -> deliver. Writes full traces and a human-readable report.

Usage:
  python run_pipeline.py                 # everything
  python run_pipeline.py --limit 6       # first 3 leads + 3 patients (quick demo / free-tier friendly)
  python run_pipeline.py --chaos         # inject: malformed CSV/FHIR handling, API timeout, CMS 503s
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from pipeline.models import setup_logging


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads", default=os.getenv("LEADS_CSV", "mock_leads.csv"))
    ap.add_argument("--fhir", default=os.getenv("FHIR_BASE_URL", "https://r4.smarthealthit.org"))
    ap.add_argument("--cms", default=os.getenv("CMS_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--min-patients", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0, help="max total records to process (split across sources)")
    ap.add_argument("--chaos", action="store_true", help="inject deliberate failures to demo error handling")
    args = ap.parse_args()

    setup_logging()
    from pipeline.runner import RunConfig, run

    Path("output").mkdir(exist_ok=True)
    for f in ("dead_letter.jsonl", "traces.jsonl", "results.jsonl", "cms_received.jsonl"):
        Path("output", f).unlink(missing_ok=True)

    summary = run(RunConfig(
        leads_csv=args.leads or None,
        fhir_base_url=args.fhir or None,
        cms_url=args.cms,
        min_patients=args.min_patients,
        limit=args.limit,
        chaos=args.chaos,
        out_dir=Path("output"),
    ))
    return 0 if summary["failed"] == 0 and not summary["source_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
