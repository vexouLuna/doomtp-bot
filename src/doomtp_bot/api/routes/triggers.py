"""`/api/v1/channels/{login}/triggers`: listeners, events and timers (architecture §7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from doomtp_bot.api import deps
from doomtp_bot.api.access import (
    READ,
    WRITE,
    Caller,
)
from doomtp_bot.api.routes.data import (
    LogLevelName,
    _channel,
    _policy,
)
from doomtp_bot.runtime.spec import LogLevel
from doomtp_bot.triggers.service import (
    MAX_COOLDOWN_S,
    TRIGGER_TYPES,
    PackTriggerError,
    TriggerError,
    TriggerService,
)

router = APIRouter(prefix="/api/v1", tags=["data"])


# ── triggers ────────────────────────────────────────────────────────────────
class TriggerBody(BaseModel):
    type: str
    expr: str = Field(min_length=1)
    match: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None
    run_as_rank: int | None = Field(default=None, ge=0, le=10_000)  # the creator's own rank by default
    log_level: LogLevelName = "output"
    # ADR-0031: seconds before it fires again in the channel, and for the same chatter; 0 is none.
    cooldown_s: int = Field(default=0, ge=0, le=MAX_COOLDOWN_S)
    user_cooldown_s: int = Field(default=0, ge=0, le=MAX_COOLDOWN_S)


class TriggerPatch(BaseModel):
    """Only the fields sent change; the type can't."""

    enabled: bool | None = None
    expr: str | None = Field(default=None, min_length=1)
    match: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None
    run_as_rank: int | None = Field(default=None, ge=0, le=10_000)
    log_level: LogLevelName | None = None
    cooldown_s: int | None = Field(default=None, ge=0, le=MAX_COOLDOWN_S)
    user_cooldown_s: int | None = Field(default=None, ge=0, le=MAX_COOLDOWN_S)


def _trigger_json(trigger: Any) -> dict[str, Any]:
    return {
        "id": trigger.id,
        "type": trigger.type,
        "expr": trigger.expr,
        "match": trigger.match,
        "schedule": trigger.schedule,
        "enabled": trigger.enabled,
        "run_as_rank": trigger.run_as_rank,
        "log_level": trigger.log_level.value,
        "cooldown_s": trigger.cooldown_s,
        "user_cooldown_s": trigger.user_cooldown_s,
        "created_by": trigger.created_by,
        "pack": trigger.pack,  # set on a pack's trigger, which can't be changed here (ADR-0029)
        "pack_key": trigger.pack_key or None,
    }


def _run_as(request: Request, caller: Caller, login: str, wanted: int | None) -> int:
    """A trigger runs at its creator's rank at most, as `!trigger add` sets it (ADR-0026)."""
    rank = caller.rank_in(_policy(request), login)
    if wanted is None:
        return rank
    if wanted > rank:
        raise HTTPException(status_code=403, detail=f"a trigger can't run above your own rank ({rank})")
    return wanted


@router.get("/channels/{login}/triggers")
async def list_triggers(request: Request, login: str, caller: Caller = READ) -> dict[str, Any]:
    settings = _channel(request, login)
    triggers: TriggerService = deps.triggers(request)
    return {"triggers": [_trigger_json(t) for t in triggers.in_channel(settings.channel_id)]}


@router.post("/channels/{login}/triggers", status_code=201)
async def add_trigger(request: Request, login: str, body: TriggerBody, caller: Caller = WRITE) -> dict[str, Any]:
    settings = _channel(request, login)
    triggers: TriggerService = deps.triggers(request)
    if body.type not in TRIGGER_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of: {', '.join(TRIGGER_TYPES)}")
    try:
        trigger = await triggers.add(
            channel_id=settings.channel_id,
            type_=body.type,
            expr=body.expr,
            match=body.match,
            schedule=body.schedule,
            run_as_rank=_run_as(request, caller, settings.login, body.run_as_rank),
            log_level=LogLevel(body.log_level),
            cooldown_s=body.cooldown_s,
            user_cooldown_s=body.user_cooldown_s,
            created_by=caller.actor.user_id,
            prefix=settings.prefix,
            via=caller.actor.via,
        )
    except TriggerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _trigger_json(trigger)


@router.patch("/channels/{login}/triggers/{trigger_id}")
async def patch_trigger(
    request: Request, login: str, trigger_id: int, body: TriggerPatch, caller: Caller = WRITE
) -> dict[str, Any]:
    """Turn a trigger on or off, or change it, checked as a new one is. Audited as `trigger.edit`."""
    settings = _channel(request, login)
    triggers: TriggerService = deps.triggers(request)
    fields = {k for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to change")
    missing = HTTPException(status_code=404, detail=f"no trigger {trigger_id} here")
    if fields - {"enabled"}:
        run_as = None if body.run_as_rank is None else _run_as(request, caller, settings.login, body.run_as_rank)
        try:
            updated = await triggers.update(
                channel_id=settings.channel_id,
                trigger_id=trigger_id,
                expr=body.expr,
                match=body.match,
                schedule=body.schedule,
                run_as_rank=run_as,
                log_level=None if body.log_level is None else LogLevel(body.log_level),
                cooldown_s=body.cooldown_s,
                user_cooldown_s=body.user_cooldown_s,
                actor_user_id=caller.actor.user_id,
                prefix=settings.prefix,
                via=caller.actor.via,
            )
        except PackTriggerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TriggerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise missing
    try:
        toggled = body.enabled is None or await triggers.set_enabled(
            channel_id=settings.channel_id,
            trigger_id=trigger_id,
            enabled=body.enabled,
            actor_user_id=caller.actor.user_id,
            via=caller.actor.via,
        )
    except PackTriggerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not toggled:
        raise missing
    return _trigger_json(next(t for t in triggers.in_channel(settings.channel_id) if t.id == trigger_id))


@router.delete("/channels/{login}/triggers/{trigger_id}")
async def remove_trigger(request: Request, login: str, trigger_id: int, caller: Caller = WRITE) -> dict[str, Any]:
    settings = _channel(request, login)
    triggers: TriggerService = deps.triggers(request)
    try:
        removed = await triggers.remove(
            channel_id=settings.channel_id,
            trigger_id=trigger_id,
            actor_user_id=caller.actor.user_id,
            via=caller.actor.via,
        )
    except PackTriggerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"no trigger {trigger_id} here")
    return {"id": trigger_id, "removed": True}
