"""`/api/v1/channels/{login}/filters`: the badword filter, as `!filter` changes it (architecture §9)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from doomtp_bot.api import deps
from doomtp_bot.api.access import (
    READ,
    WRITE,
    Caller,
)
from doomtp_bot.api.routes.data import (
    _channel,
)
from doomtp_bot.filters.matcher import FilterError
from doomtp_bot.filters.service import FilterService
from doomtp_bot.policy.roles import GLOBAL

router = APIRouter(prefix="/api/v1", tags=["data"])


# ── filters ─────────────────────────────────────────────────────────────────
FilterKind = Literal["word", "wildcard", "regex", "allow"]
FilterAction = Literal["mask", "replace", "tag", "block"]


class FilterBody(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    kind: FilterKind = "word"
    action: FilterAction = "mask"
    category: str = Field(default="", max_length=40)
    replacement: str = Field(default="", max_length=200)


class FilterPatch(BaseModel):
    """Only the fields sent change."""

    enabled: bool | None = None
    pattern: str | None = Field(default=None, min_length=1, max_length=200)
    kind: FilterKind | None = None
    action: FilterAction | None = None
    category: str | None = Field(default=None, max_length=40)
    replacement: str | None = Field(default=None, max_length=200)


def filter_json(e: Any) -> dict[str, Any]:
    return {
        "id": e.id,
        "pattern": e.pattern,
        "kind": e.kind,
        "action": e.action,
        "category": e.category,
        "replacement": e.replacement,
        "enabled": e.enabled,
        "global": e.channel_id == GLOBAL,
    }


def _where(scope: str) -> str:
    return "everywhere" if scope == GLOBAL else "here"


@router.get("/channels/{login}/filters")
async def list_filters(request: Request, login: str, caller: Caller = READ) -> dict[str, Any]:
    settings = _channel(request, login)
    filters: FilterService = deps.filters(request)
    return {"filters": [filter_json(e) for e in filters.entries_for(settings.channel_id)]}


@router.post("/channels/{login}/filters", status_code=201)
async def add_filter(request: Request, login: str, body: FilterBody, caller: Caller = WRITE) -> dict[str, Any]:
    settings = _channel(request, login)
    return await add_filter_to(request, settings.channel_id, body, caller)


async def add_filter_to(request: Request, scope: str, body: FilterBody, caller: Caller) -> dict[str, Any]:
    filters: FilterService = deps.filters(request)
    try:
        entry = await filters.add(
            channel_id=scope,
            pattern=body.pattern,
            kind=body.kind,
            action=body.action,
            category=body.category,
            replacement=body.replacement,
            actor_user_id=caller.actor.user_id,
            via=caller.actor.via,
        )
    except FilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return filter_json(entry)


@router.patch("/channels/{login}/filters/{entry_id}")
async def patch_filter(
    request: Request, login: str, entry_id: int, body: FilterPatch, caller: Caller = WRITE
) -> dict[str, Any]:
    """Turn an entry on or off, or change it, checked as a new one is. Audited as `filter.edit`."""
    settings = _channel(request, login)
    return await patch_filter_in(request, settings.channel_id, entry_id, body, caller)


async def patch_filter_in(
    request: Request, scope: str, entry_id: int, body: FilterPatch, caller: Caller
) -> dict[str, Any]:
    filters: FilterService = deps.filters(request)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    enabled = fields.pop("enabled", None)
    if not fields and enabled is None:
        raise HTTPException(status_code=400, detail="nothing to change")
    missing = HTTPException(status_code=404, detail=f"no filter {entry_id} {_where(scope)}")
    if fields:
        try:
            entry = await filters.update(
                channel_id=scope,
                entry_id=entry_id,
                fields=fields,
                actor_user_id=caller.actor.user_id,
                via=caller.actor.via,
            )
        except FilterError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if entry is None:
            raise missing
    if enabled is not None and not await filters.set_enabled(
        channel_id=scope,
        entry_id=entry_id,
        enabled=enabled,
        actor_user_id=caller.actor.user_id,
        via=caller.actor.via,
    ):
        raise missing
    found = next(e for e in filters.entries_for(scope) if e.id == entry_id)
    return filter_json(found)


@router.delete("/channels/{login}/filters/{entry_id}")
async def remove_filter(request: Request, login: str, entry_id: int, caller: Caller = WRITE) -> dict[str, Any]:
    settings = _channel(request, login)
    return await remove_filter_from(request, settings.channel_id, entry_id, caller)


async def remove_filter_from(request: Request, scope: str, entry_id: int, caller: Caller) -> dict[str, Any]:
    filters: FilterService = deps.filters(request)
    if not await filters.remove(
        channel_id=scope,
        entry_id=entry_id,
        actor_user_id=caller.actor.user_id,
        via=caller.actor.via,
    ):
        raise HTTPException(status_code=404, detail=f"no filter {entry_id} {_where(scope)}")
    return {"id": entry_id, "removed": True}
