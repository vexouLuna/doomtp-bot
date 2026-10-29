"""`/api/v1`: the chat log, command runs and the audit trail. A channel's log is open to its
moderators, and to anyone while its `public_log` setting is on (ADR-0026)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from doomtp_bot.api import deps
from doomtp_bot.api.access import (
    API_ACTOR,
    PERSONAL_READ,
    READ,
    Caller,
    authenticate,
    check_area,
)
from doomtp_bot.api.routes.data import (
    MAX_ROWS,
    _channel,
    _login_of,
    _policy,
)
from doomtp_bot.api.routes.session import client_address
from doomtp_bot.audit.log import read_audit
from doomtp_bot.chatlog import queries, timeline

router = APIRouter(prefix="/api/v1", tags=["data"])


# ── the chat log: moderators, or anyone while it is public (ADR-0026) ──────
PUBLIC_READER = Caller("public", API_ACTOR, role="user", channels=frozenset())


def _is_public(caller: Caller) -> bool:
    return caller is PUBLIC_READER


async def _log_reader(request: Request) -> Caller:
    """A moderator of the channel, as for `READ`; failing that, anyone at all while the channel's log is
    public (`public_log` and `log_enabled`), a limited number of reads a minute per address."""
    login = request.path_params["login"]
    try:
        caller = await authenticate(request, "read")
        check_area(caller, "channel", login)
        return caller
    except HTTPException:
        settings = _policy(request).channel_by_login(login)
        if settings is None or not (settings.public_log and settings.log_enabled):
            raise
    wait = request.app.state.public_read_limiter.hit(client_address(request))
    if wait is not None:
        raise HTTPException(
            status_code=429, detail="too many reads; try again later", headers={"Retry-After": str(wait)}
        )
    return PUBLIC_READER


# Read by the test that walks every route.
_log_reader.area = "channel"  # type: ignore[attr-defined]
_log_reader.min_rank = None  # type: ignore[attr-defined]
_log_reader.public = True  # type: ignore[attr-defined]
LOG_READ = Depends(_log_reader)


class CommandRunRow(BaseModel):
    user_id: str | None
    trigger_type: str
    expr: str
    code: int
    message: str | None
    duration_ms: int
    cancelled_reason: str | None
    at: int = Field(description="ms since the epoch")


class CommandRunList(BaseModel):
    runs: list[CommandRunRow]


class MessageSearch(BaseModel):
    """Hits are message rows; their fields are listed in `/api/v2/channels/{login}/log`."""

    query: str
    messages: list[dict[str, Any]]


class LogPage(BaseModel):
    """`entries` are messages, notifications or moderation events, told apart by `kind`."""

    channel_id: str
    order: str
    entries: list[dict[str, Any]]
    next: str | None


class LogSession(BaseModel):
    started_at: int
    ended_at: int | None
    end_reason: str | None


class BackfillRun(BaseModel):
    model_config = ConfigDict(extra="allow")  # the stored run row, as it is
    complete: bool
    inserted: int
    error: str | None


class CoverageGap(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: int = Field(serialization_alias="from", validation_alias="from")
    to: int
    reason: str
    backfill: BackfillRun | None


class LogCoverage(BaseModel):
    since: int
    until: int
    sessions: list[LogSession]
    gaps: list[CoverageGap]
    complete: bool


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="allow")  # `before` and `after` are whatever the change stored
    id: int
    channel_id: str | None
    actor_user_id: str | None
    via: str
    action: str
    target: str | None
    at: int = Field(description="ms since the epoch")
    channel_login: str | None
    actor_login: str | None


class AuditPage(BaseModel):
    entries: list[AuditEntry]
    next: int | None


@router.get("/channels/{login}/runs", response_model=CommandRunList, summary="A channel's recent command runs")
async def command_runs(
    request: Request,
    login: str,
    limit: int = Query(default=50, ge=1, le=MAX_ROWS),
    caller: Caller = READ,
) -> dict[str, Any]:
    settings = _channel(request, login)
    conn = deps.chatlog_db(request)
    async with await conn.execute(
        "SELECT user_id, trigger_type, expr, code, message, duration_ms, cancelled_reason, at"
        " FROM command_runs WHERE channel_id = %s ORDER BY at DESC LIMIT %s",
        (settings.channel_id, limit),
    ) as cur:
        return {"runs": [dict(row) for row in await cur.fetchall()]}


# architecture §3.3
@router.get("/channels/{login}/messages", response_model=MessageSearch, summary="Search a channel's chat log")
async def search_messages(
    request: Request,
    login: str,
    q: str = Query(min_length=1, max_length=queries.MAX_QUERY_CHARS),
    limit: int = Query(default=50, ge=1, le=MAX_ROWS),
    caller: Caller = LOG_READ,
) -> dict[str, Any]:
    """Full-text search over the channel's log. The public see no removed messages."""
    settings = _channel(request, login)
    rows = await queries.search_messages(
        deps.chatlog_db(request), settings.channel_id, q, limit=limit, visible_only=_is_public(caller)
    )
    return {"query": q, "messages": rows}


# A list default must be a module-level singleton (ruff B008); repeat `kind=` for several, all by default.
_KINDS_QUERY = Query(default=None, description="message, notification or moderation; repeat for several")


# ADR-0025, ADR-0026
@router.get("/channels/{login}/log", response_model=LogPage, summary="A channel's log as one timeline")
async def channel_log(
    request: Request,
    login: str,
    since: int | None = Query(default=None, ge=0, description="ms since the epoch, inclusive"),
    until: int | None = Query(default=None, ge=0, description="ms since the epoch, exclusive"),
    order: timeline.Order = "desc",
    kind: list[timeline.Kind] | None = _KINDS_QUERY,
    user: str | None = Query(default=None, min_length=1, max_length=40, description="a login, old ones too"),
    q: str | None = Query(default=None, min_length=1, max_length=queries.MAX_QUERY_CHARS),
    hide_removed: bool = False,
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=100, ge=1, le=MAX_ROWS),
    caller: Caller = LOG_READ,
) -> dict[str, Any]:
    """The channel's log as one timeline of messages, notifications and moderation, a page at a time.
    Pass `next` back as `cursor`, with the same filters, for the page after. The public get
    messages and notifications only; removed messages are in, flagged, unless `hide_removed`."""
    settings = _channel(request, login)
    kinds = list(kind or timeline.KINDS)
    if _is_public(caller):
        kinds = [k for k in kinds if k != "moderation"]
        if not kinds:
            raise HTTPException(status_code=403, detail=f"only a moderator can read {login}'s moderation log")
    conn = deps.chatlog_db(request)
    user_ids = None if user is None else await timeline.user_ids_for(conn, user)
    try:
        after = None if cursor is None else timeline.Cursor.decode(cursor)
        entries, following = await timeline.read(
            conn, settings.channel_id, kinds=kinds, since=since, until=until, cursor=after,
            order=order, limit=limit, user_ids=user_ids, query=q, hide_removed=hide_removed,
        )  # fmt: skip
    except timeline.CursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "channel_id": settings.channel_id,
        "order": order,
        "entries": entries,
        "next": None if following is None else following.encode(),
    }


# ADR-0025
@router.get(
    "/channels/{login}/log/coverage", response_model=LogCoverage, summary="When the bot was listening, and the gaps"
)
async def channel_log_coverage(
    request: Request,
    login: str,
    since: int = Query(ge=0, description="ms since the epoch"),
    until: int | None = Query(default=None, ge=0, description="ms since the epoch; now by default"),
    caller: Caller = LOG_READ,
) -> dict[str, Any]:
    """When the bot was listening between `since` and `until`, and which holes backfill filled."""
    if until is not None and until <= since:
        raise HTTPException(status_code=422, detail="until must be after since")
    settings = _channel(request, login)
    return await timeline.coverage(deps.chatlog_db(request), settings.channel_id, since, until)


# ADR-0026
@router.get("/audit", response_model=AuditPage, summary="The audit trail of changes")
async def audit(
    request: Request,
    channel: str | None = Query(default=None, max_length=40),
    actor: str | None = Query(default=None, max_length=40, description="a login, or `me`"),
    action: str | None = Query(default=None, max_length=64, description="`cc.edit`, or `cc.` for every cc one"),
    before: int | None = Query(default=None, ge=1, description="the `next` of the page before"),
    limit: int = Query(default=50, ge=1, le=MAX_ROWS),
    caller: Caller = PERSONAL_READ,
) -> dict[str, Any]:
    """The newest changes first, a page at a time: pass `next` back as `before`. A moderator sees the
    channels they manage; anyone signed in sees their own changes everywhere with `actor=me`."""
    conn, policy = deps.bot_db(request), _policy(request)
    known = {c.channel_id: c.login for c in policy.channels()}
    own = actor == "me"
    actor_id: str | None = None
    if own:
        if caller.user_id is None:
            raise HTTPException(status_code=400, detail="actor=me needs a Twitch sign-in")
        actor_id = caller.user_id
    elif actor is not None:
        user = await deps.twitch(request).resolve_user(actor)
        if user is None:
            raise HTTPException(status_code=404, detail=f"no Twitch user named {actor}")
        actor_id = user["id"]
    scope: dict[str, Any] = {}
    if channel is not None:
        if not own:
            check_area(caller, "channel", channel)
        scope["channel_id"] = _channel(request, channel).channel_id
    elif not caller.is_admin and not own:
        if caller.role == "user":
            # Someone who manages no channel sees only their own changes.
            if caller.user_id is None or actor_id not in (None, caller.user_id):
                return {"entries": [], "next": None}
            actor_id = caller.user_id
        else:
            scope["channel_ids"] = [c.channel_id for c in policy.channels() if caller.manages(c.login)]
    found = await read_audit(conn, limit=limit, before_id=before, actor_user_id=actor_id, action=action, **scope)
    for entry in found:
        entry["channel_login"] = known.get(entry.get("channel_id") or "")
        entry["actor_login"] = await _login_of(request, entry.get("actor_user_id"), known)
    return {"entries": found, "next": found[-1]["id"] if len(found) == limit else None}
