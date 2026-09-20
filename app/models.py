"""Request/response schemas."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from .severity import normalize_severity


class EventIn(BaseModel):
    source: str = Field(min_length=1, max_length=255, examples=["edge-gateway-01"])
    event_id: str = Field(min_length=1, max_length=255, examples=["evt-8f31a2"])
    occurred_at: datetime = Field(examples=["2026-09-20T08:15:30Z"])
    severity: str = Field(min_length=1, max_length=32, examples=["error"])

    @field_validator("source", "event_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # Naive timestamps are interpreted as UTC.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @field_validator("severity")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_severity(value)


class SegmentOut(BaseModel):
    id: int
    source: str
    start_at: datetime
    end_at: datetime
    max_severity: str
    event_count: int
    revision: int


class EventResult(BaseModel):
    segment_id: int
    deduplicated: bool
    segment: SegmentOut


def segment_from_row(row: sqlite3.Row) -> SegmentOut:
    return SegmentOut(
        id=row["id"],
        source=row["source"],
        start_at=datetime.fromtimestamp(row["start_at"], tz=timezone.utc),
        end_at=datetime.fromtimestamp(row["end_at"], tz=timezone.utc),
        max_severity=row["max_severity"],
        event_count=row["event_count"],
        revision=row["revision"],
    )
