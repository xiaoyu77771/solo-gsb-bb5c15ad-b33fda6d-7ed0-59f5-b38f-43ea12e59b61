"""Core segmentation logic.

Events for one source are sessionized by occurred_at: consecutive events
(ordered by event time) separated by more than `silence_seconds` belong to
different segments. Events may arrive out of order, so an incoming event can:

  * fall inside an existing segment            -> join it
  * extend a segment's start or end boundary   -> join it
  * bridge two segments (within silence of both) -> merge them
  * touch nothing                              -> open a new segment

Duplicate (source, event_id) submissions are idempotent: they never change
counts, boundaries, severity, or revision.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from .db import Database
from .severity import higher


@dataclass(frozen=True)
class Submission:
    segment: sqlite3.Row
    deduplicated: bool  # True when (source, event_id) was already stored
    segment_created: bool  # True when this event opened a new segment


def submit_event(
    db: Database,
    silence_seconds: float,
    *,
    source: str,
    event_id: str,
    occurred_at: float,
    severity: str,
) -> Submission:
    """Record one event and fold it into the segment structure.

    `occurred_at` is epoch seconds; `severity` must already be normalized.
    The whole operation runs in a single IMMEDIATE transaction.
    """
    with db.transaction() as conn:
        duplicate = conn.execute(
            "SELECT segment_id FROM events WHERE source = ? AND event_id = ?",
            (source, event_id),
        ).fetchone()
        if duplicate is not None:
            segment = conn.execute(
                "SELECT * FROM segments WHERE id = ?", (duplicate["segment_id"],)
            ).fetchone()
            return Submission(segment=segment, deduplicated=True, segment_created=False)

        # A segment absorbs the event when the event time lies within
        # [start_at - silence, end_at + silence]. Because segments of one
        # source are separated by gaps larger than the silence interval,
        # at most two segments can match (the event then bridges them).
        matches = conn.execute(
            """
            SELECT * FROM segments
            WHERE source = ? AND start_at <= ? AND end_at >= ?
            ORDER BY id
            """,
            (source, occurred_at + silence_seconds, occurred_at - silence_seconds),
        ).fetchall()

        now = time.time()
        if not matches:
            cursor = conn.execute(
                """
                INSERT INTO segments
                    (source, start_at, end_at, max_severity,
                     event_count, revision, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (source, occurred_at, occurred_at, severity, now, now),
            )
            segment_id = cursor.lastrowid
            segment_created = True
        elif len(matches) == 1:
            segment = matches[0]
            segment_id = segment["id"]
            conn.execute(
                """
                UPDATE segments
                SET start_at     = MIN(start_at, ?),
                    end_at       = MAX(end_at, ?),
                    max_severity = ?,
                    event_count  = event_count + 1,
                    revision     = revision + 1,
                    updated_at   = ?
                WHERE id = ?
                """,
                (
                    occurred_at,
                    occurred_at,
                    higher(segment["max_severity"], severity),
                    now,
                    segment_id,
                ),
            )
            segment_created = False
        else:
            # The event bridges several segments: fold them into the oldest
            # one and re-point their events.
            keep = matches[0]
            segment_id = keep["id"]
            start_at = min([m["start_at"] for m in matches] + [occurred_at])
            end_at = max([m["end_at"] for m in matches] + [occurred_at])
            max_severity = severity
            for m in matches:
                max_severity = higher(max_severity, m["max_severity"])
            event_count = sum(m["event_count"] for m in matches) + 1
            revision = max(m["revision"] for m in matches) + 1
            merged_ids = [m["id"] for m in matches[1:]]
            placeholders = ",".join("?" for _ in merged_ids)
            conn.execute(
                """
                UPDATE segments
                SET start_at = ?, end_at = ?, max_severity = ?,
                    event_count = ?, revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (start_at, end_at, max_severity, event_count, revision, now, segment_id),
            )
            conn.execute(
                f"UPDATE events SET segment_id = ? WHERE segment_id IN ({placeholders})",
                (segment_id, *merged_ids),
            )
            conn.execute(
                f"DELETE FROM segments WHERE id IN ({placeholders})", tuple(merged_ids)
            )
            segment_created = False

        conn.execute(
            """
            INSERT INTO events
                (source, event_id, occurred_at, severity, segment_id, received_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source, event_id, occurred_at, severity, segment_id, now),
        )
        segment = conn.execute(
            "SELECT * FROM segments WHERE id = ?", (segment_id,)
        ).fetchone()
        return Submission(segment=segment, deduplicated=False, segment_created=segment_created)
