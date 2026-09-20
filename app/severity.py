"""Severity levels and ordering."""
from __future__ import annotations

# Canonical levels, ordered from least to most severe.
SEVERITY_RANKS: dict[str, int] = {
    "debug": 0,
    "info": 1,
    "warning": 2,
    "error": 3,
    "critical": 4,
}

_ALIASES = {
    "warn": "warning",
}


def normalize_severity(value: str) -> str:
    """Normalize a severity string to its canonical lowercase form.

    Raises ValueError for unknown levels.
    """
    level = _ALIASES.get(value.strip().lower(), value.strip().lower())
    if level not in SEVERITY_RANKS:
        allowed = ", ".join(SEVERITY_RANKS)
        raise ValueError(f"unknown severity {value!r}; allowed: {allowed}")
    return level


def higher(a: str, b: str) -> str:
    """Return the more severe of two canonical severity levels."""
    return a if SEVERITY_RANKS[a] >= SEVERITY_RANKS[b] else b
