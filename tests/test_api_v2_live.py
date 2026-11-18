"""/api/v2/channels/{login}/chat/live: the live chat feed as server-sent events (ADR-0038)."""
# ruff: noqa: F811  (the imported fixtures are parameters here, which ruff reads as redefinitions)

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from doomtp_bot.api import v2_live
from doomtp_bot.api.keys import ApiKeyService
from doomtp_bot.api.routes.log import _log_reader
from doomtp_bot.api.v2_live import live_router
from doomtp_bot.api.v2_log import MessageEntry
from doomtp_bot.chatlog.live import LiveFeed
from doomtp_bot.core.events import (
    Badge,
    ChatCleared,
    ChatMessage,
    ChatNotification,
    MessageDeleted,
    UserMessagesCleared,
)
from doomtp_bot.policy.repository import Actor
from tests.test_api_data import (  # noqa: F401  (fixtures)
    CHANNEL_ID,
    CHANNEL_LOGIN,
    app_and_keys,
    auth,
    client,
    write_key,
)

LIVE = f"/api/v2/channels/{CHANNEL_LOGIN}/chat/live"
T = 1_763_000_000_000


def chat(mid: str, text: str = "hi Kappa", **kw: Any) -> ChatMessage:
    return ChatMessage(
        message_id=mid, channel_id=CHANNEL_ID, channel_login=CHANNEL_LOGIN, user_id="400", user_login="alice",
        display_name="Alice", text=text, sent_at=T, received_at=T + 5, badges=(Badge("vip", "1"),),
        raw_event={
            "message_id": mid, "chatter_user_id": "400", "chatter_user_login": "alice", "chatter_user_name": "Alice",
            "color": "#FF0000", "badges": [{"set_id": "vip", "id": "1", "info": ""}],
            "message": {"text": text, "fragments": [{"type": "text", "text": "hi "},
                                                    {"type": "emote", "text": "Kappa", "emote": {"id": "25"}}]},
            "message_type": "text",
        },
        **kw,
    )  # fmt: skip


class Stream:
    """One SSE request driven straight through the ASGI app: httpx's test transport would wait for the
    whole body, which a live stream never finishes."""

    def __init__(self, app: Any, path: str, headers: dict[str, str] | None = None) -> None:
        self.status: int | None = None
        self._started = asyncio.Event()
        self.headers: dict[str, str] = {}
        self._buffer = ""
        self._chunks: asyncio.Queue[str] = asyncio.Queue()
        self._gone = asyncio.Event()
        self._asked = False
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "client": ("127.0.0.1", 5000), "server": ("test", 80),
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }  # fmt: skip
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

    async def _receive(self) -> dict[str, Any]:
        if not self._asked:
            self._asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._gone.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self._started.set()
            self.headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            await self._chunks.put(message.get("body", b"").decode())
            if not message.get("more_body"):
                await self._chunks.put("\0")  # the end of the response

    async def started(self) -> int:
        await asyncio.wait_for(self._started.wait(), 5)
        assert self.status is not None
        return self.status

    async def event(self) -> tuple[str, Any]:
        """The next event (comments skipped), or ("<closed>", None) once the response ends."""
        while True:
            while "\n\n" in self._buffer:
                frame, self._buffer = self._buffer.split("\n\n", 1)
                fields = dict(line.split(": ", 1) for line in frame.split("\n") if not line.startswith(":"))
                if "event" in fields:
                    return fields["event"], json.loads(fields["data"])
            chunk = await asyncio.wait_for(self._chunks.get(), 5)
            if chunk == "\0":
                return "<closed>", None
            self._buffer += chunk

    async def disconnect(self) -> None:
        self._gone.set()
        await asyncio.wait_for(self.task, 5)


@pytest.fixture
def feed(app_and_keys: tuple[Any, ApiKeyService], monkeypatch: pytest.MonkeyPatch) -> LiveFeed:
    monkeypatch.setattr(v2_live, "TICK_S", 0.05)
    found = LiveFeed(per_address=2)
    app_and_keys[0].state.live = found
    return found


async def settle(feed: LiveFeed, channel: str = CHANNEL_ID, count: int = 1) -> None:
    for _ in range(200):
        if feed.count(channel) == count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} streams, found {feed.count(channel)}")


def test_the_live_route_is_the_logs_reader() -> None:
    """Same access rule as the log: its moderators, or anyone while the log is public (ADR-0026)."""
    for route in live_router().routes:
        calls = [d.call for d in route.dependant.dependencies]  # type: ignore[attr-defined]
        assert calls == [_log_reader], route.path  # type: ignore[attr-defined]


async def test_each_event_reaches_a_public_stream_in_the_logs_shape(
    app_and_keys: tuple[Any, ApiKeyService], feed: LiveFeed
) -> None:
    stream = Stream(app_and_keys[0], LIVE)
    assert await stream.started() == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["x-accel-buffering"] == "no" and stream.headers["cache-control"] == "no-cache"
    assert await stream.event() == ("ready", {"channel": CHANNEL_LOGIN})

    feed.message(chat("m-1"), is_command=False)
    kind, message = await stream.event()
    assert kind == "message" and set(message) == set(MessageEntry.model_fields)
    assert message["kind"] == "message" and message["id"] == "m-1" and message["at"].endswith("Z")
    assert message["user"] == {"id": "400", "login": "alice", "display_name": "Alice"}
    assert message["color"] == "#FF0000" and message["fragments"][1]["emote_id"] == "25"

    feed.notification(ChatNotification("n-1", CHANNEL_ID, "400", "sub", {"tier": "1000"}, T))
    kind, notice = await stream.event()
    assert (kind, notice["kind"], notice["type"]) == ("notification", "notification", "sub")

    feed.removal(MessageDeleted(CHANNEL_ID, "m-1", "400", T + 10))
    feed.removal(UserMessagesCleared(CHANNEL_ID, "400", T + 20, duration_s=60))
    feed.removal(ChatCleared(CHANNEL_ID, T + 30))
    kind, deleted = await stream.event()
    assert kind == "delete" and deleted["message_id"] == "m-1" and deleted["user_id"] == "400"
    assert deleted["at"].endswith("Z")
    kind, cleared = await stream.event()
    assert (kind, cleared["user_id"], cleared["duration_s"]) == ("user_clear", "400", 60)
    assert (await stream.event())[0] == "chat_clear"

    await stream.disconnect()
    assert feed.count() == 0  # a client that goes away frees its slot


async def test_another_channels_chat_stays_out(app_and_keys: tuple[Any, ApiKeyService], feed: LiveFeed) -> None:
    stream = Stream(app_and_keys[0], LIVE)
    await stream.event()
    other = ChatMessage("x-1", "999", "elsewhere", "1", "a", "A", "hi", T, T)
    feed.message(other, is_command=False)
    feed.message(chat("m-2"), is_command=True)
    kind, message = await stream.event()
    assert (kind, message["id"], message["is_command"]) == ("message", "m-2", True)
    await stream.disconnect()


async def test_a_private_log_has_no_public_stream_and_closing_it_ends_one(
    app_and_keys: tuple[Any, ApiKeyService], client: httpx.AsyncClient, write_key: str, feed: LiveFeed
) -> None:
    app = app_and_keys[0]
    stream = Stream(app, LIVE)
    await stream.event()
    await settle(feed)
    await app.state.policy.mutate(
        lambda repo: repo.set_channel_field(CHANNEL_ID, "public_log", False, Actor(None, "test"))
    )
    assert await stream.event() == ("end", {"reason": "log_private"})  # without waiting for chat
    assert (await stream.event())[0] == "<closed>"
    await stream.disconnect()
    assert feed.count() == 0

    assert (await client.get(LIVE)).status_code == 401
    moderator = Stream(app, LIVE, auth(write_key))  # the channel's moderators still may
    assert await moderator.started() == 200
    feed.message(chat("m-3"), is_command=False)
    assert (await moderator.event())[0] == "ready"
    assert (await moderator.event())[1]["id"] == "m-3"
    await moderator.disconnect()


async def test_streams_are_capped_per_address(
    app_and_keys: tuple[Any, ApiKeyService], client: httpx.AsyncClient, feed: LiveFeed
) -> None:
    first, second = Stream(app_and_keys[0], LIVE), Stream(app_and_keys[0], LIVE)
    await first.event()
    await second.event()
    refused = await client.get(LIVE)  # httpx's test client comes from 127.0.0.1 too
    assert refused.status_code == 429 and refused.json()["code"] == "too_many_streams"
    assert refused.headers["retry-after"] == "30"
    await first.disconnect()
    third = Stream(app_and_keys[0], LIVE)
    assert (await third.event())[0] == "ready"
    await second.disconnect()
    await third.disconnect()
    assert feed.count() == 0


async def test_a_slow_stream_is_dropped_not_buffered(app_and_keys: tuple[Any, ApiKeyService]) -> None:
    feed = LiveFeed(queue_size=3)
    sub = feed.open(CHANNEL_ID, "1.2.3.4")
    assert sub is not None
    for n in range(10):
        feed.message(chat(f"m-{n}"), is_command=False)
    assert sub.dropped and sub.queue.qsize() == 1
    assert sub.queue.get_nowait() == ("end", {"reason": "too_slow"})


async def test_the_bot_stopping_ends_streams(app_and_keys: tuple[Any, ApiKeyService], feed: LiveFeed) -> None:
    app = app_and_keys[0]
    stream = Stream(app, LIVE)
    await stream.event()
    app.state.stopping = lambda: True
    try:
        assert await stream.event() == ("end", {"reason": "shutdown"})
        await stream.disconnect()
    finally:
        del app.state.stopping
    assert feed.count() == 0


def test_the_hub_counts_and_frees_slots() -> None:
    feed = LiveFeed(per_address=1, total=2)
    a = feed.open("c", "a")
    assert a is not None and feed.open("c", "a") is None  # per address
    b = feed.open("d", "b")
    assert b is not None and feed.open("c", "z") is None  # in all
    feed.close(a)
    feed.close(a)  # twice is harmless
    assert feed.count() == 1 and feed.open("c", "a") is not None
