"""HTTP API surface."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response, status

from .config import Settings
from .db import Database
from .models import EventIn, EventResult, SegmentOut, segment_from_row
from .service import submit_event


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = Database(settings.database_path)
        yield
        app.state.db.close()

    app = FastAPI(
        title="Alert Aggregation Service",
        version="1.0.0",
        summary="Groups device alerts into incident segments per source.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    @app.post(
        "/events",
        status_code=status.HTTP_201_CREATED,
        response_model=EventResult,
        tags=["events"],
    )
    def post_event(event: EventIn, request: Request, response: Response) -> EventResult:
        """Ingest one alert event.

        Returns 201 when the event was newly recorded, 200 when it was a
        duplicate of an already stored (source, event_id) pair.
        """
        result = submit_event(
            request.app.state.db,
            settings.silence_interval_seconds,
            source=event.source,
            event_id=event.event_id,
            occurred_at=event.occurred_at.timestamp(),
            severity=event.severity,
        )
        if result.deduplicated:
            response.status_code = status.HTTP_200_OK
        return EventResult(
            segment_id=result.segment["id"],
            deduplicated=result.deduplicated,
            segment=segment_from_row(result.segment),
        )

    @app.get("/segments/{segment_id}", response_model=SegmentOut, tags=["segments"])
    def get_segment(segment_id: int, request: Request) -> SegmentOut:
        """Fetch one segment: boundaries, max severity, count, revision."""
        row = request.app.state.db.query_one(
            "SELECT * FROM segments WHERE id = ?", (segment_id,)
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="segment not found"
            )
        return segment_from_row(row)

    @app.get("/segments", response_model=list[SegmentOut], tags=["segments"])
    def list_segments(
        request: Request,
        source: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[SegmentOut]:
        """List segments, newest first, optionally filtered by source."""
        if source is None:
            rows = request.app.state.db.query(
                "SELECT * FROM segments ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = request.app.state.db.query(
                "SELECT * FROM segments WHERE source = ? ORDER BY id DESC LIMIT ?",
                (source, limit),
            )
        return [segment_from_row(row) for row in rows]

    return app


app = create_app()
