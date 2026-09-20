"""Runtime configuration, sourced from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DATABASE_PATH = "./data/alerts.db"
DEFAULT_SILENCE_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class Settings:
    """Service settings.

    database_path: SQLite file path (or ":memory:").
    silence_interval_seconds: events for the same source whose occurred_at
        timestamps are at most this far apart belong to the same segment.
    """

    database_path: str = DEFAULT_DATABASE_PATH
    silence_interval_seconds: float = DEFAULT_SILENCE_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if self.silence_interval_seconds < 0:
            raise ValueError("silence_interval_seconds must be >= 0")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH),
            silence_interval_seconds=float(
                os.getenv("SILENCE_INTERVAL_SECONDS", DEFAULT_SILENCE_INTERVAL_SECONDS)
            ),
        )
