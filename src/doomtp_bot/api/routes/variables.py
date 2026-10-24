"""`/api/v1`: variables, their storage and the storage limits (ADR-0010, ADR-0019)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from doomtp_bot.api import deps
from doomtp_bot.api.access import (
    ADMIN_READ,
    ADMIN_WRITE,
    READ,
    Caller,
)
from doomtp_bot.api.routes.data import (
    MAX_ROWS,
    _channel,
    _login_of,
    _policy,
)
from doomtp_bot.runtime.variables import (
    LIMIT_COLUMNS,
    MAX_LIST_ITEMS,
    MAX_NAMES_PER_SPACE,
    MAX_QUOTA_BYTES,
    MAX_VALUE_BYTES,
    OWNER_KINDS,
    VAR_NAME_RE,
    Limits,
)
from doomtp_bot.variables.store import LimitOverride

router = APIRouter(prefix="/api/v1", tags=["data"])


# ── variables, logs and the audit trail ─────────────────────────────────────
# The namespaces a channel's page shows: its own, its chatters', and what published commands keep here
# (variable-access-matrix.md §5: all of them are public).
ChannelNamespace = Literal["channel", "channel.chatter", "publisher.channel", "publisher.channel.chatter"]


async def _user_id(request: Request, login: str) -> str:
    """A Twitch login as `!var` takes it, `@` or not. 404 when Twitch knows nobody by that name."""
    found = await deps.twitch(request).resolve_user(login)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no Twitch user named {login.lstrip('@')}")
    return str(found["id"])


async def _logins_for(request: Request, user_ids: set[str], known: dict[str, str]) -> dict[str, str | None]:
    ids = sorted(user_ids)
    found = await asyncio.gather(*(_login_of(request, uid, known) for uid in ids))
    return dict(zip(ids, found, strict=True))


def _checked_name(name: str | None) -> str | None:
    if name is not None and not VAR_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"{name} isn't a variable name")
    return name


@router.get("/channels/{login}/variables")
async def channel_variables(
    request: Request,
    login: str,
    ns: ChannelNamespace = "channel",
    name: str | None = Query(default=None, max_length=32),
    user: str | None = Query(default=None, max_length=32, description="a chatter's login, for *.chatter"),
    publisher: str | None = Query(default=None, max_length=32, description="a command owner's login"),
    limit: int = Query(default=100, ge=1, le=MAX_ROWS),
    offset: int = Query(default=0, ge=0),
    caller: Caller = READ,
) -> dict[str, Any]:
    """The variables kept in this channel, one namespace at a time, as `!var list` reads them: the channel's
    own, each chatter's (`channel.chatter`), and what published commands keep here (`publisher.channel`,
    `publisher.channel.chatter`). Logins come with the ids when they're known."""
    settings = _channel(request, login)
    store = deps.variable_store(request)
    chatter_column = {"channel.chatter": "key2", "publisher.channel.chatter": "key3"}.get(ns)
    match = {"key2": settings.channel_id} if ns.startswith("publisher") else {"key1": settings.channel_id}
    if user is not None:
        if chatter_column is None:
            raise HTTPException(status_code=400, detail=f"{ns} has no per-chatter values")
        match[chatter_column] = await _user_id(request, user)
    if publisher is not None:
        if not ns.startswith("publisher"):
            raise HTTPException(status_code=400, detail=f"{ns} belongs to no command owner")
        match["key1"] = await _user_id(request, publisher)
    entries = await store.rows(ns, match=match, name=_checked_name(name), limit=limit + 1, offset=offset)
    more, entries = len(entries) > limit, entries[:limit]

    def chatter_of(e: Any) -> str | None:
        return getattr(e.key, chatter_column) if chatter_column else None

    def publisher_of(e: Any) -> str | None:
        return e.key.key1 if ns.startswith("publisher") else None

    known = {c.channel_id: c.login for c in _policy(request).channels()}
    ids = {i for e in entries for i in (e.updated_by, chatter_of(e), publisher_of(e)) if i}
    logins = await _logins_for(request, ids, known)
    rows = []
    for e in entries:
        row: dict[str, Any] = {
            "name": e.key.name,
            "value": e.value,
            "updated_at": e.updated_at,
            "updated_by": e.updated_by,
            "updated_by_login": logins.get(e.updated_by) if e.updated_by else None,
        }
        if (chatter := chatter_of(e)) is not None:
            row |= {"chatter_id": chatter, "chatter_login": logins.get(chatter)}
        if (owner := publisher_of(e)) is not None:
            row |= {"publisher_id": owner, "publisher_login": logins.get(owner)}
        rows.append(row)
    return {"namespace": ns, "variables": rows, "has_more": more}


@router.get("/channels/{login}/variables/top")
async def channel_variable_top(
    request: Request,
    login: str,
    name: str = Query(max_length=32),
    count: int = Query(default=5, ge=1, le=25),
    caller: Caller = READ,
) -> dict[str, Any]:
    """`!var top channel.chatter.<name> [count]`: the chatters with the highest number in it."""
    settings = _channel(request, login)
    store = deps.variable_store(request)
    rows = await store.top("channel.chatter", settings.channel_id, "", _checked_name(name) or name, count)
    known = {c.channel_id: c.login for c in _policy(request).channels()}
    logins = await _logins_for(request, {uid for uid, _ in rows}, known)
    return {
        "name": name,
        "top": [
            {"rank": i, "chatter_id": uid, "chatter_login": logins.get(uid), "value": value}
            for i, (uid, value) in enumerate(rows, start=1)
        ],
    }


# ADR-0019
@router.get("/channels/{login}/storage")
async def channel_storage(request: Request, login: str, caller: Caller = READ) -> dict[str, Any]:
    """How much of its quota the channel uses, per namespace, like `!var usage channel`."""
    settings = _channel(request, login)
    store = deps.variable_store(request)
    used = await store.usage("channel", settings.channel_id)
    limits = await store.limits_for("channel", settings.channel_id)
    return {
        "used_bytes": sum(used.values()),
        "namespaces": used,
        **_limits_json(limits),
    }


# ── storage limits (ADR-0019): the same writes as `!admin quota|valuecap|listitems|names` ────
class LimitsBody(BaseModel):
    """Only the fields sent change. On an owner, `null` goes back to the default."""

    quota_bytes: int | None = Field(default=None, ge=0, le=MAX_QUOTA_BYTES)
    value_cap_bytes: int | None = Field(default=None, ge=0, le=MAX_VALUE_BYTES)
    list_items: int | None = Field(default=None, ge=0, le=MAX_LIST_ITEMS)
    names_per_space: int | None = Field(default=None, ge=0, le=MAX_NAMES_PER_SPACE)


def _limits_json(limits: Limits | LimitOverride) -> dict[str, int | None]:
    return {column: getattr(limits, column) for column in LIMIT_COLUMNS}


async def _apply_limits(request: Request, kind: str, owner_id: str, body: LimitsBody, caller: Caller) -> None:
    store = deps.variable_store(request)
    for field in sorted(body.model_fields_set):
        value = getattr(body, field)
        if kind == "*" and value is None:
            raise HTTPException(status_code=422, detail=f"the default {field} can't be null")
        await store.set_limit(kind, owner_id, field, value, actor=caller.actor.user_id, via=caller.actor.via)


@router.get("/variable-limits")
async def variable_limits(request: Request, caller: Caller = ADMIN_READ) -> dict[str, Any]:
    store = deps.variable_store(request)
    defaults = await store.defaults()
    known = {c.channel_id: c.login for c in request.app.state.policy.channels()}
    return {
        "defaults": _limits_json(defaults),
        "overrides": [
            {
                "owner_kind": kind,
                "owner_id": owner_id,
                "owner_login": await _login_of(request, owner_id, known),  # the list shows people, not ids
                **_limits_json(o),
            }
            for kind, owner_id, o in await store.overrides()
        ],
    }


@router.patch("/variable-limits/default")
async def set_default_limits(request: Request, body: LimitsBody, caller: Caller = ADMIN_WRITE) -> dict[str, Any]:
    await _apply_limits(request, "*", "*", body, caller)
    defaults = await deps.variable_store(request).defaults()
    return _limits_json(defaults)


@router.patch("/variable-limits/{kind}/{user}")
async def set_owner_limits(
    request: Request, kind: str, user: str, body: LimitsBody, caller: Caller = ADMIN_WRITE
) -> dict[str, Any]:
    """One channel's, publisher's or chatter's override, by Twitch login."""
    if kind not in OWNER_KINDS:
        raise HTTPException(status_code=404, detail=f"owner kind must be one of {', '.join(OWNER_KINDS)}")
    found = await deps.twitch(request).resolve_user(user)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no Twitch user named {user}")
    await _apply_limits(request, kind, found["id"], body, caller)
    store = deps.variable_store(request)
    own = await store.override(kind, found["id"])
    limits = await store.limits_for(kind, found["id"])
    return {
        "owner_kind": kind,
        "owner_id": found["id"],
        "override": None if own is None else _limits_json(own),
        "effective": _limits_json(limits),
    }
