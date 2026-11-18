"""/api/v2/channels/{login}/chat/live: a channel's chat as it happens, as server-sent events (ADR-0038).

The same access rule as the log: the channel's moderators, or anyone while its log is public. Open streams
are capped per client address and in all. Events:

- `ready`: once, first, `{"channel": login}`.
- `message`: a chat message, the log's v2 `MessageEntry` (`kind: "message"`), so the log's renderer draws it.
- `notification`: a sub, raid, announcement…, the log's v2 `NotificationEntry`.
- `delete` `{message_id, user_id, at}`, `user_clear` `{user_id, duration_s, at}`, `chat_clear` `{at}`:
  messages to take off screen.
- `end` `{reason}`: the bot closes the stream after it. `log_private` (the channel's public log was
  turned off; public streams only), `too_slow` (the client fell behind) or `shutdown`.

A comment line every `KEEPALIVE_S` keeps proxies from timing the stream out. There are no event ids: a
reconnect starts from now, and `Last-Event-ID` is ignored. The overlay reads the recent log for what came
before.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from vex_platform.api import ApiError

from doomtp_bot.api.access import Caller
from doomtp_bot.api.routes.log import LOG_READ, _is_public
from doomtp_bot.api.routes.session import client_address
from doomtp_bot.api.v2_log import MessageEntry, NotificationEntry, _channel, _entry, _time
from doomtp_bot.chatlog.live import Item, LiveFeed, Subscription

KEEPALIVE_S = 15.0
TICK_S = 1.0  # how often a quiet stream checks whether the bot is stopping or the log went private
RETRY_MS = 3000

_DESCRIPTION = (
    "`text/event-stream`. Events: `ready`, `message` (a log `MessageEntry`), `notification` (a log "
    "`NotificationEntry`), `delete` `{message_id, user_id, at}`, `user_clear` `{user_id, duration_s, at}`, "
    "`chat_clear` `{at}`, and `end` `{reason}` (`log_private`, `too_slow`, `shutdown`) after which the bot "
    "closes the stream. A comment line every 15 s keeps it open. No replay: `Last-Event-ID` is ignored. "
    "429 when this address already has as many streams open as it may."
)


def _data(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """An item in v2's shape: the entries through the log's own models, times as ISO 8601."""
    if event == "message":
        return MessageEntry.model_validate(_entry(data)).model_dump(mode="json")  # type: ignore[no-any-return]
    if event == "notification":
        return NotificationEntry.model_validate(_entry(data)).model_dump(mode="json")  # type: ignore[no-any-return]
    if "at" in data:
        at = _time(data["at"])
        return {**data, "at": None if at is None else at.isoformat().replace("+00:00", "Z")}
    return data


def _frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _stream(
    feed: LiveFeed,
    sub: Subscription,
    login: str,
    allowed: Callable[[], bool],
    stopping: Callable[[], bool],
) -> AsyncIterator[str]:
    try:
        yield f"retry: {RETRY_MS}\n\n"
        yield _frame("ready", {"channel": login})
        quiet_since = time.monotonic()
        while True:
            item: Item | None = None
            with contextlib.suppress(TimeoutError):
                item = await asyncio.wait_for(sub.queue.get(), TICK_S)
            if stopping():
                yield _frame("end", {"reason": "shutdown"})
                return
            if not allowed():
                yield _frame("end", {"reason": "log_private"})
                return
            if item is None:
                if time.monotonic() - quiet_since >= KEEPALIVE_S:
                    yield ": keep-alive\n\n"
                    quiet_since = time.monotonic()
                continue
            event, data = item
            yield _frame(event, _data(event, data))
            quiet_since = time.monotonic()
            if event == "end":
                return
    finally:
        feed.close(sub)  # a client that goes away cancels this generator, which frees its slot here


def live_router() -> APIRouter:
    router = APIRouter(tags=["log"])

    @router.get(
        "/channels/{login}/chat/live",
        summary="A channel's chat as it happens (server-sent events)",
        description=_DESCRIPTION,
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}, "description": "The event stream"}},
    )
    async def live_chat(request: Request, login: str, caller: Caller = LOG_READ) -> StreamingResponse:
        settings = _channel(request, login)
        feed: LiveFeed | None = getattr(request.app.state, "live", None)
        if feed is None:
            raise ApiError(503, "unavailable", "the live feed isn't running")
        sub = feed.open(settings.channel_id, client_address(request))
        if sub is None:
            raise ApiError(
                429, "too_many_streams", "too many live streams open from here", headers={"Retry-After": "30"}
            )
        policy = request.app.state.policy
        channel_id = settings.channel_id

        def allowed() -> bool:  # checked again all along: the log can go private mid-stream
            if not _is_public(caller):
                return True
            now = policy.channel_settings(channel_id)
            return now is not None and now.public_log and now.log_enabled

        state = request.app.state

        def stopping() -> bool:  # set by the bot once uvicorn serves; uvicorn waits for open streams to end
            flag = getattr(state, "stopping", None)
            return bool(flag and flag())

        return StreamingResponse(
            _stream(feed, sub, settings.login, allowed, stopping),
            media_type="text/event-stream",
            # `X-Accel-Buffering: no` asks an nginx-style proxy not to buffer the stream.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
