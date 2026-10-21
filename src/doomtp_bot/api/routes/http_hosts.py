"""`/api/v1/http-hosts`: the hosts `http get` may fetch, as `!admin http` changes them (ADR-0020)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from doomtp_bot.api import deps
from doomtp_bot.api.access import (
    ADMIN_READ,
    ADMIN_WRITE,
    Caller,
)
from doomtp_bot.webfetch.fetcher import Secret
from doomtp_bot.webfetch.hosts import MAX_LIMIT, HostError, HostStore

router = APIRouter(prefix="/api/v1", tags=["data"])


# ── the hosts `http get` may fetch (ADR-0020): the same writes as `!admin http` ──────────────
# A secret goes in here and never comes back out: listings show its kind and name only.
class HostBody(BaseModel):
    plain_http: bool = False


class SecretBody(BaseModel):
    kind: Literal["query", "header"]
    name: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=512)


class HttpLimitsBody(BaseModel):
    channel_per_minute: int | None = Field(default=None, ge=0, le=MAX_LIMIT)
    host_per_minute: int | None = Field(default=None, ge=0, le=MAX_LIMIT)


def _hosts(request: Request) -> HostStore:
    return deps.http_hosts(request)


@router.get("/http-hosts")
async def http_hosts(request: Request, caller: Caller = ADMIN_READ) -> dict[str, Any]:
    hosts = _hosts(request)
    return {"hosts": [e.public() for e in hosts.entries()], "limits": hosts.limits().as_dict()}


@router.put("/http-hosts/{pattern}")
async def allow_http_host(
    request: Request, pattern: str, body: HostBody, caller: Caller = ADMIN_WRITE
) -> dict[str, Any]:
    try:
        entry = await _hosts(request).allow(
            pattern, plain_http=body.plain_http, actor=caller.actor.user_id, via=caller.actor.via
        )
    except HostError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return entry.public()


@router.delete("/http-hosts/{pattern}")
async def deny_http_host(request: Request, pattern: str, caller: Caller = ADMIN_WRITE) -> dict[str, Any]:
    try:
        removed = await _hosts(request).deny(pattern, actor=caller.actor.user_id, via=caller.actor.via)
    except HostError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not removed:
        raise HTTPException(status_code=404, detail=f"{pattern} isn't on the list")
    return {"pattern": pattern.lower(), "status": "removed"}


@router.put("/http-hosts/{pattern}/secret")
async def set_http_secret(
    request: Request, pattern: str, body: SecretBody, caller: Caller = ADMIN_WRITE
) -> dict[str, Any]:
    secret = Secret(body.kind, body.name, body.value)
    try:
        entry = await _hosts(request).set_secret(pattern, secret, actor=caller.actor.user_id, via=caller.actor.via)
    except HostError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return entry.public()


@router.delete("/http-hosts/{pattern}/secret")
async def clear_http_secret(request: Request, pattern: str, caller: Caller = ADMIN_WRITE) -> dict[str, Any]:
    try:
        entry = await _hosts(request).set_secret(pattern, None, actor=caller.actor.user_id, via=caller.actor.via)
    except HostError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return entry.public()


@router.patch("/http-limits")
async def set_http_limits(request: Request, body: HttpLimitsBody, caller: Caller = ADMIN_WRITE) -> dict[str, int]:
    limits = await _hosts(request).set_limits(
        channel_per_minute=body.channel_per_minute,
        host_per_minute=body.host_per_minute,
        actor=caller.actor.user_id,
        via=caller.actor.via,
    )
    return limits.as_dict()
