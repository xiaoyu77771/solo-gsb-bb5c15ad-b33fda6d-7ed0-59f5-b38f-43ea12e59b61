"""End-to-end tests over the HTTP API using an in-memory database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SILENCE = 300  # seconds
BASE = datetime(2026, 9, 20, 8, 0, 0, tzinfo=timezone.utc)


def iso(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@pytest.fixture()
def client():
    settings = Settings(database_path=":memory:", silence_interval_seconds=SILENCE)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def post(client: TestClient, event_id: str, at: float, severity: str = "info",
         source: str = "dev-1"):
    return client.post(
        "/events",
        json={
            "source": source,
            "event_id": event_id,
            "occurred_at": iso(at),
            "severity": severity,
        },
    )


def test_single_event_opens_segment(client):
    resp = post(client, "e1", 0, "error")
    assert resp.status_code == 201
    body = resp.json()
    assert body["deduplicated"] is False
    segment = body["segment"]
    assert segment["event_count"] == 1
    assert segment["revision"] == 1
    assert segment["max_severity"] == "error"
    assert segment["start_at"] == iso(0)
    assert segment["end_at"] == iso(0)


def test_duplicate_submission_is_idempotent(client):
    first = post(client, "e1", 0, "error")
    second = post(client, "e1", 0, "error")
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    segment = second.json()["segment"]
    assert segment["id"] == first.json()["segment_id"]
    assert segment["event_count"] == 1
    assert segment["revision"] == 1


def test_events_within_silence_share_segment(client):
    post(client, "e1", 0)
    resp = post(client, "e2", SILENCE)  # exactly at the boundary -> same segment
    segment = resp.json()["segment"]
    assert segment["event_count"] == 2
    assert segment["start_at"] == iso(0)
    assert segment["end_at"] == iso(SILENCE)
    assert segment["revision"] == 2


def test_gap_larger_than_silence_opens_new_segment(client):
    first = post(client, "e1", 0).json()
    second = post(client, "e2", SILENCE + 1).json()
    assert second["segment_id"] != first["segment_id"]
    assert second["segment"]["event_count"] == 1


def test_sources_are_segmented_independently(client):
    a = post(client, "e1", 0, source="dev-a").json()
    b = post(client, "e2", 10, source="dev-b").json()
    assert a["segment_id"] != b["segment_id"]


def test_late_event_extends_start_and_bumps_revision(client):
    seg_id = post(client, "e1", 100).json()["segment_id"]
    before = client.get(f"/segments/{seg_id}").json()
    resp = post(client, "e2", 100 - SILENCE)  # late, out of order
    after = resp.json()["segment"]
    assert after["id"] == seg_id
    assert after["start_at"] == iso(100 - SILENCE)
    assert after["end_at"] == iso(100)
    assert after["revision"] == before["revision"] + 1


def test_late_event_raises_max_severity(client):
    seg_id = post(client, "e1", 0, "info").json()["segment_id"]
    resp = post(client, "e2", 60, "critical")
    segment = resp.json()["segment"]
    assert segment["id"] == seg_id
    assert segment["max_severity"] == "critical"
    assert segment["revision"] == 2


def test_lower_severity_does_not_lower_max(client):
    post(client, "e1", 0, "critical")
    resp = post(client, "e2", 60, "debug")
    assert resp.json()["segment"]["max_severity"] == "critical"


def test_late_event_inside_segment_keeps_boundaries(client):
    seg_id = post(client, "e1", 0, "warning").json()["segment_id"]
    post(client, "e2", 200, "warning")
    resp = post(client, "e3", 100, "info")  # lands inside [0, 200]
    segment = resp.json()["segment"]
    assert segment["id"] == seg_id
    assert segment["start_at"] == iso(0)
    assert segment["end_at"] == iso(200)
    assert segment["max_severity"] == "warning"
    assert segment["event_count"] == 3
    assert segment["revision"] == 3


def test_late_event_bridges_and_merges_segments(client):
    first = post(client, "e1", 0, "error").json()
    second = post(client, "e2", 600, "info").json()  # gap 600 > silence
    assert first["segment_id"] != second["segment_id"]

    merged = post(client, "e3", 300, "critical").json()  # bridges both
    segment = merged["segment"]
    assert segment["id"] == first["segment_id"]  # oldest segment survives
    assert segment["start_at"] == iso(0)
    assert segment["end_at"] == iso(600)
    assert segment["event_count"] == 3
    assert segment["max_severity"] == "critical"

    # The merged-away segment is gone; the survivor reflects the merge.
    assert client.get(f"/segments/{second['segment_id']}").status_code == 404
    survivor = client.get(f"/segments/{first['segment_id']}").json()
    assert survivor["event_count"] == 3


def test_duplicate_after_merge_reports_surviving_segment(client):
    keep = post(client, "e1", 0).json()["segment_id"]
    gone = post(client, "e2", 600).json()["segment_id"]
    post(client, "e3", 300)
    resp = post(client, "e2", 600)  # duplicate of an event from the merged segment
    assert resp.status_code == 200
    assert resp.json()["segment_id"] == keep != gone
    assert resp.json()["segment"]["event_count"] == 3


def test_get_segment_detail(client):
    seg_id = post(client, "e1", 0, "error").json()["segment_id"]
    post(client, "e2", 120, "info")
    detail = client.get(f"/segments/{seg_id}").json()
    assert detail == {
        "id": seg_id,
        "source": "dev-1",
        "start_at": iso(0),
        "end_at": iso(120),
        "max_severity": "error",
        "event_count": 2,
        "revision": 2,
    }


def test_get_missing_segment_is_404(client):
    assert client.get("/segments/999").status_code == 404


def test_list_segments_filter_by_source(client):
    post(client, "e1", 0, source="dev-a")
    post(client, "e2", 0, source="dev-b")
    listed = client.get("/segments", params={"source": "dev-a"}).json()
    assert len(listed) == 1
    assert listed[0]["source"] == "dev-a"


def test_severity_is_case_insensitive_and_validated(client):
    resp = post(client, "e1", 0, "ERROR")
    assert resp.status_code == 201
    assert resp.json()["segment"]["max_severity"] == "error"
    assert post(client, "e2", 0, "nope").status_code == 422


def test_naive_timestamp_treated_as_utc(client):
    resp = client.post(
        "/events",
        json={
            "source": "dev-1",
            "event_id": "e1",
            "occurred_at": "2026-09-20T08:00:00",
            "severity": "info",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["segment"]["start_at"] == iso(0)


def test_revision_unchanged_by_duplicate_after_updates(client):
    seg_id = post(client, "e1", 0, "info").json()["segment_id"]
    post(client, "e2", 100, "error")
    before = client.get(f"/segments/{seg_id}").json()
    post(client, "e1", 0, "info")  # duplicate
    after = client.get(f"/segments/{seg_id}").json()
    assert after == before
