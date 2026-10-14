"""Unified internal record schema shared by both sources."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ContactInfo(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    address: Optional[str] = None


class PrioritySignal(BaseModel):
    """Deterministic, source-specific pre-score. The agent sees it but may disagree (and says why)."""
    score: int = Field(ge=0, le=100)
    factors: list[str] = Field(default_factory=list)


class Record(BaseModel):
    id: str
    source: Literal["linkedin_csv", "fhir_emr"]
    name: str
    contact_info: ContactInfo = Field(default_factory=ContactInfo)
    context: dict[str, Any] = Field(default_factory=dict)
    priority_signal: PrioritySignal
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    ingested_at: str = Field(default_factory=utcnow)


def setup_logging() -> None:
    os.makedirs("output", exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler("output/pipeline.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
