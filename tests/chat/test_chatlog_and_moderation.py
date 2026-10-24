"""Chat log writer (append-only, batched) and the moderation index."""

from __future__ import annotations

from doomtp_bot.chatlog.writer import ChatLogWriter
from doomtp_bot.core.events import (
    Badge,
    ChatCleared,
    ChatMessage,
    MessageDeleted,
    ModerationAction,
    UserMessagesCleared,
)
from doomtp_bot.moderation.index import ModerationIndex
from doomtp_bot.storage.db import Databases


def msg(
    mid: str, user: str = "u1", login: str = "alice", text: str = "hi", at: int = 1000, channel: str = "c1"
) -> ChatMessage:
    return ChatMessage(
        message_id=mid, channel_id=channel, channel_login="doomtp", user_id=user, user_login=login,
        display_name=login.title(), text=text, sent_at=at, received_at=at + 5, badges=(Badge("subscriber", "3"),),
    )  # fmt: skip


async def rows(dbs: Databases, sql: str) -> list[tuple[object, ...]]:
    async with await dbs.chatlog.execute(sql) as cur:
        return [tuple(r.values()) for r in await cur.fetchall()]


async def test_writer_batches_and_is_idempotent(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog, flush_interval=0.01)
    writer.start()
    await writer.message(msg("m1"), is_command=True)
    await writer.message(msg("m1"))  # EventSub redelivery
    await writer.message(msg("m2", text="the doom slayer"))
    await writer.stop()
    assert await rows(dbs, "SELECT message_id, is_command, raw->'badges' FROM messages ORDER BY message_id") == [
        ("m1", True, [{"set_id": "subscriber", "id": "3", "info": ""}]),
        ("m2", False, [{"set_id": "subscriber", "id": "3", "info": ""}]),
    ]
    assert (
        await rows(dbs, "SELECT message_id FROM messages WHERE tsv @@ websearch_to_tsquery('simple', 'slayer')") != []
    )


async def test_one_bad_row_does_not_lose_the_batch(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog)
    await writer.message(msg("m1"))
    await writer.message(msg("m2", channel=None))  # type: ignore[arg-type]  # violates NOT NULL
    await writer.message(msg("m3"))
    await writer.stop()
    assert await rows(dbs, "SELECT message_id FROM messages ORDER BY message_id") == [("m1",), ("m3",)]


async def test_users_are_keyed_by_id_and_renames_are_kept(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog)
    await writer.message(msg("m1", login="alice", at=1000))
    await writer.message(msg("m2", login="alice_2026", at=2000))
    await writer.stop()  # not started: drains synchronously
    assert await rows(dbs, "SELECT user_id, login, first_seen, last_seen FROM users") == [
        ("u1", "alice_2026", 1000, 2000)
    ]
    assert await rows(dbs, "SELECT login FROM user_names ORDER BY seen_from") == [("alice",), ("alice_2026",)]


async def test_moderation_flags_never_delete_rows(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog)
    await writer.message(msg("m1", user="u1", at=1000))
    await writer.message(msg("m2", user="u1", at=2000))
    await writer.message(msg("m3", user="u2", login="bob", at=2500))
    await writer.moderation(MessageDeleted("c1", "m1", "u1", at=1500))
    await writer.moderation(UserMessagesCleared("c1", "u1", at=2200))
    await writer.message(msg("m4", user="u1", at=3000))  # after the timeout: not flagged
    await writer.moderation(ChatCleared("c1", at=2600))
    await writer.stop()
    assert await rows(dbs, "SELECT message_id, deleted_at, cleared_at FROM messages ORDER BY message_id") == [
        ("m1", 1500, 2200),
        ("m2", None, 2200),
        ("m3", None, 2600),
        ("m4", None, None),
    ]
    assert await rows(dbs, "SELECT type FROM mod_events ORDER BY id") == [
        ("delete",),
        ("user_clear",),
        ("chat_clear",),
    ]


async def test_a_moderation_event_offered_again_is_stored_once(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog)
    for source in ("eventsub", "ivr-logs", "ivr-logs"):  # a redelivery, then two backfills over the same gap
        await writer.moderation(MessageDeleted("c1", "m1", "u1", at=1500, source=source))
        await writer.moderation(UserMessagesCleared("c1", "u1", at=2200, duration_s=600, source=source))
        await writer.moderation(ChatCleared("c1", at=2600, source=source))
    await writer.moderation(MessageDeleted("c1", "m2", "u1", at=1500))  # another message, same moment
    await writer.moderation(UserMessagesCleared("c1", "u1", at=9000, duration_s=600))  # a second timeout later
    await writer.moderation(UserMessagesCleared("c1", "u1", at=9500, duration_s=60))  # and another, shorter
    await writer.stop()
    assert await rows(dbs, "SELECT type, message_id, source, at FROM mod_events ORDER BY id") == [
        ("delete", "m1", "eventsub", 1500),
        ("user_clear", None, "eventsub", 2200),
        ("chat_clear", None, "eventsub", 2600),
        ("delete", "m2", "eventsub", 1500),
        ("user_clear", None, "eventsub", 9000),
        ("user_clear", None, "eventsub", 9500),
    ]


async def test_a_timeout_seen_live_and_by_backfill_is_stored_once(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog)
    # Backfill's IRC line first, then EventSub's action for it a moment later with the moderator.
    await writer.moderation(UserMessagesCleared("c1", "u1", at=5000, duration_s=600, source="ivr-logs"))
    await writer.moderation(ModerationAction("c1", "timeout", "mod1", "u1", at=5400, duration_s=600))
    # EventSub first this time; the backfilled line comes later and adds nothing.
    await writer.moderation(ModerationAction("c1", "ban", "mod1", "u2", at=7000))
    await writer.moderation(UserMessagesCleared("c1", "u2", at=6500, source="ivr-logs"))
    # Not the same action: other durations, or too far apart.
    await writer.moderation(UserMessagesCleared("c1", "u1", at=5500, duration_s=60, source="ivr-logs"))
    await writer.moderation(ModerationAction("c1", "timeout", "mod1", "u1", at=8000, duration_s=600))
    await writer.stop()
    assert await rows(
        dbs, "SELECT type, target_user_id, moderator_user_id, source, at, duration_s FROM mod_events ORDER BY id"
    ) == [
        ("timeout", "u1", "mod1", "ivr-logs", 5000, 600),
        ("ban", "u2", "mod1", "eventsub", 7000, None),
        ("user_clear", "u1", None, "ivr-logs", 5500, 60),
        ("timeout", "u1", "mod1", "eventsub", 8000, 600),
    ]


async def test_sessions_runs_and_outbound(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog)
    await writer.start_session("c1")
    await writer.start_session("c1")  # idempotent while open
    await writer.command_run(
        channel_id="c1", user_id="u1", trigger_type="chat", trigger_id="m1", expr="!ping",
        resolved=[{"index": 1, "name": "ping"}], code=0, message="pong", duration_ms=3, cancelled_reason=None,
    )  # fmt: skip
    await writer.outbound(
        channel_id="c1", text_sent="pong", text_prefilter="pong", twitch_message_id="t1", dropped_reason=None
    )
    await writer.end_all_sessions("shutdown")
    await writer.stop()
    assert await rows(dbs, "SELECT channel_id, end_reason FROM log_sessions") == [("c1", "shutdown")]
    assert await rows(dbs, "SELECT expr, code FROM command_runs") == [("!ping", 0)]
    assert await rows(dbs, "SELECT text_sent, twitch_message_id FROM outbound_msgs") == [("pong", "t1")]


async def test_a_restart_ends_sessions_when_the_bot_last_heard_chat(dbs: Databases) -> None:
    writer = ChatLogWriter(dbs.chatlog)
    await writer.start_session("c1")
    await writer.end_all_sessions("restart", at=1234)
    await writer.start_session("c1")  # a new session once the client is back, so the gap between is filled
    await writer.stop()
    assert await rows(dbs, "SELECT ended_at, end_reason FROM log_sessions ORDER BY id") == [
        (1234, "restart"),
        (None, None),
    ]


async def test_stale_sessions_closed_at_last_message_before_next_session(dbs: Databases) -> None:
    await dbs.chatlog.execute(
        """
        INSERT INTO log_sessions (channel_id, started_at) VALUES ('c1', 1000);  -- killed process
        INSERT INTO log_sessions (channel_id, started_at) VALUES ('c1', 5000);  -- killed again
        INSERT INTO log_sessions (channel_id, started_at) VALUES ('c2', 7000);  -- nothing logged
        INSERT INTO log_sessions (channel_id, started_at, ended_at, end_reason) VALUES ('c3', 1, 2, 'shutdown');
        """
    )
    writer = ChatLogWriter(dbs.chatlog)
    for mid, at in (("a", 1500), ("b", 4000), ("c", 6000)):
        await writer.message(msg(mid, at=at, channel="c1"))  # received_at = at + 5
    await writer.stop()
    assert await writer.close_stale_sessions() == 3
    assert await rows(dbs, "SELECT channel_id, started_at, ended_at, end_reason FROM log_sessions ORDER BY id") == [
        ("c1", 1000, 4005, "unclean_shutdown"),
        ("c1", 5000, 6005, "unclean_shutdown"),
        ("c2", 7000, 7000, "unclean_shutdown"),
        ("c3", 1, 2, "shutdown"),
    ]


def test_moderation_index() -> None:
    index = ModerationIndex(clock_ms=lambda: 10_000)
    assert not index.is_invalidated("c1", "m1", "u1", 1000)
    index.record(MessageDeleted("c1", "m1", "u1", at=1100))
    assert index.is_invalidated("c1", "m1", "u1", 1000)
    index.record(UserMessagesCleared("c1", "u2", at=2000))
    assert index.is_invalidated("c1", "mX", "u2", 1999)  # sent before the timeout
    assert not index.is_invalidated("c1", "mY", "u2", 2001)  # sent after it
    assert not index.is_invalidated("c2", "mZ", "u2", 1000)  # other channel
    index.record(ChatCleared("c1", at=3000))
    assert index.checker("c1", "mQ", "u9", 2999)()
