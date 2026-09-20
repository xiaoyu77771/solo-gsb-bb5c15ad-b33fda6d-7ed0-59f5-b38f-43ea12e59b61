"""SQLite persistence layer.

A single connection guarded by a reentrant lock keeps writes serialized;
FastAPI sync endpoints run in a threadpool, so the lock also protects
against interleaved transactions across threads.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT    NOT NULL,
    start_at      REAL    NOT NULL,          -- epoch seconds, inclusive
    end_at        REAL    NOT NULL,          -- epoch seconds, inclusive
    max_severity  TEXT    NOT NULL,
    event_count   INTEGER NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 1,
    created_at    REAL    NOT NULL,
    updated_at    REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_source_window
    ON segments (source, start_at, end_at);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    event_id    TEXT    NOT NULL,
    occurred_at REAL    NOT NULL,            -- epoch seconds
    severity    TEXT    NOT NULL,
    segment_id  INTEGER NOT NULL REFERENCES segments (id),
    received_at REAL    NOT NULL,
    UNIQUE (source, event_id)                -- idempotent submissions
);
CREATE INDEX IF NOT EXISTS idx_events_segment ON events (segment_id);
"""


class Database:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements atomically; rolls back on any exception."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
