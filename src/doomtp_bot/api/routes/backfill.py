"""`/api/v1/channels/{login}/backfill`: chat log backfill jobs (ADR-0024 §5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from doomtp_bot.api import deps
from doomtp_bot.api.access import (
    BROADCASTER_WRITE,
    READ,
    Caller,
    platform_actor,
)
from doomtp_bot.api.routes.data import (
    _channel,
)
from doomtp_bot.clock import now_ms
from doomtp_bot.history.jobs import BackfillJobs, BackfillRefused

router = APIRouter(prefix="/api/v1", tags=["data"])


# ── backfill jobs (ADR-0024 §5) ─────────────────────────────────────────────


class BackfillRequest(BaseModel):
    """A range (`to_ms` defaults to now), or `gaps` for every open gap in the channel's log."""

    from_ms: int | None = Field(default=None, ge=0)
    to_ms: int | None = Field(default=None, ge=0)
    gaps: bool = False


def _backfill(request: Request) -> BackfillJobs:
    return deps.backfill(request)


@router.get("/channels/{login}/backfill")
async def list_backfill_jobs(
    request: Request, login: str, limit: int = Query(20, ge=1, le=100), caller: Caller = READ
) -> dict[str, Any]:
    """Queued and running jobs first, oldest first; then the latest finished ones."""
    settings = _channel(request, login)
    jobs = await _backfill(request).jobs(settings.channel_id, limit=limit)
    return {"enabled": settings.history_backfill, "jobs": [j.to_json() for j in jobs]}


@router.post("/channels/{login}/backfill", status_code=201)
async def queue_backfill(
    request: Request, login: str, body: BackfillRequest, caller: Caller = BROADCASTER_WRITE
) -> dict[str, Any]:
    settings = _channel(request, login)
    queue = _backfill(request)
    if body.gaps == (body.from_ms is not None):
        raise HTTPException(status_code=400, detail="send from_ms (and to_ms), or gaps: true")
    try:
        if body.gaps:
            # The channel's one job for its gaps; one already waiting is answered rather than refused.
            job, _ = await queue.queue_gaps(settings.channel_id, caller.label, actor=platform_actor(caller))
            jobs = [] if job is None else [job]
        else:
            assert body.from_ms is not None
            to_ms = now_ms() if body.to_ms is None else body.to_ms
            job = await queue.queue_range(
                settings.channel_id, body.from_ms, to_ms, caller.label, actor=platform_actor(caller)
            )
            if job is None:
                raise HTTPException(status_code=409, detail="that range is already queued")
            jobs = [job]
    except BackfillRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"jobs": [j.to_json() for j in jobs]}


@router.delete("/channels/{login}/backfill/{job_id}")
async def cancel_backfill(
    request: Request, login: str, job_id: int, caller: Caller = BROADCASTER_WRITE
) -> dict[str, Any]:
    """A queued job is cancelled at once; a running one stops before its next request, and is answered
    still `running` if that takes more than a moment."""
    settings = _channel(request, login)
    job = await _backfill(request).cancel(settings.channel_id, job_id, actor=platform_actor(caller))
    if job is None:
        raise HTTPException(status_code=409, detail=f"job {job_id} isn't queued or running in {settings.login}")
    return job.to_json()
