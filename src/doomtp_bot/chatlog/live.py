"""The live chat feed's hub (ADR-0038): what the dispatcher sees, handed to each open stream at once.

The chat log writer batches, so the database is no low-latency source; the dispatcher is the one place
every chat event passes through, and it calls `LiveFeed` beside the writer. Nothing here is stored: a
stream gets what happens while it is open, and nothing before.

Each stream has a bounded queue. One that falls behind is dropped, not buffered without bound: its queue
is emptied and it gets one `end` event (`too_slow`), so it can reconnect.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from doomtp_bot.chatlog import legacy, timeline
from doomtp_bot.core.events import ChatCleared, ChatMessage, ChatNotification, MessageDeleted, UserMessagesCleared

PER_ADDRESS, TOTAL, QUEUE_SIZE = 4, 500, 256

#: (event name, data). Data is in the v1 timeline's shape, times in ms: the API turns it into v2's.
Item = tuple[str, dict[str, Any]]


class Subscription:
    def __init__(self, channel_id: str, address: str, size: int) -> None:
        self.channel_id = channel_id
        self.address = address
        self.queue: asyncio.Queue[Item] = asyncio.Queue(size)
        self.dropped = False
        self.closed = False

    def put(self, item: Item) -> None:
        if self.dropped:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(("end", {"reason": "too_slow"}))


class LiveFeed:
    def __init__(self, *, per_address: int = PER_ADDRESS, total: int = TOTAL, queue_size: int = QUEUE_SIZE) -> None:
        self.per_address = per_address
        self.total = total
        self.queue_size = queue_size
        self._subs: dict[str, set[Subscription]] = {}
        self._by_address: Counter[str] = Counter()

    def open(self, channel_id: str, address: str) -> Subscription | None:
        """A new stream's slot, or None when `address` (or the bot) has as many open as it may."""
        if self._by_address[address] >= self.per_address or self.count() >= self.total:
            return None
        sub = Subscription(channel_id, address, self.queue_size)
        self._subs.setdefault(channel_id, set()).add(sub)
        self._by_address[address] += 1
        return sub

    def close(self, sub: Subscription) -> None:
        """Free the slot. Safe to call twice."""
        if sub.closed:
            return
        sub.closed = True
        subs = self._subs.get(sub.channel_id, set())
        subs.discard(sub)
        if not subs:
            self._subs.pop(sub.channel_id, None)
        self._by_address[sub.address] -= 1
        if self._by_address[sub.address] <= 0:
            del self._by_address[sub.address]

    def count(self, channel_id: str | None = None) -> int:
        if channel_id is not None:
            return len(self._subs.get(channel_id, ()))
        return sum(len(s) for s in self._subs.values())

    def _publish(self, channel_id: str, event: str, data: dict[str, Any]) -> None:
        for sub in tuple(self._subs.get(channel_id, ())):
            sub.put((event, data))

    # ── from the dispatcher ────────────────────────────────────────────────
    def message(self, msg: ChatMessage, *, is_command: bool) -> None:
        if not self._subs.get(msg.channel_id):
            return  # nobody listening: don't build the entry
        if msg.raw_event is not None:
            raw_format, raw = "eventsub", msg.raw_event
        elif msg.raw_line is not None:
            raw_format, raw = "irc", {"line": msg.raw_line}
        else:
            raw_format, raw = "legacy", legacy.message(msg)
        row = {
            "message_id": msg.message_id, "sent_at": msg.sent_at, "user_id": msg.user_id,
            "user_login": msg.user_login, "text": msg.text, "is_self": msg.is_self, "is_command": is_command,
            "source": msg.source, "received_at": msg.received_at, "deleted_at": None, "cleared_at": None,
            "raw_format": raw_format, "raw": raw, "enrichment": msg.enrichment,
        }  # fmt: skip
        self._publish(msg.channel_id, "message", timeline.message_entry(row))

    def notification(self, n: ChatNotification) -> None:
        if not self._subs.get(n.channel_id):
            return
        if n.raw_event is not None:
            raw_format, raw = "eventsub", n.raw_event
        elif n.raw_line is not None:
            raw_format, raw = "irc", {"line": n.raw_line}
        else:
            raw_format, raw = "legacy", legacy.notification(n)
        row = {
            "id": n.id, "sent_at": n.sent_at, "user_id": n.user_id, "user_login": None, "display_name": None,
            "type": n.type, "source": n.source, "raw_format": raw_format, "raw": raw, "enrichment": None,
        }  # fmt: skip
        self._publish(n.channel_id, "notification", timeline.notification_entry(row))

    def removal(self, event: MessageDeleted | UserMessagesCleared | ChatCleared) -> None:
        """A message gone: one deleted, a user's cleared (timeout or ban), or the whole chat cleared."""
        match event:
            case MessageDeleted():
                data = {"message_id": event.message_id, "user_id": event.target_user_id, "at": event.at}
                self._publish(event.channel_id, "delete", data)
            case UserMessagesCleared():
                data = {"user_id": event.target_user_id, "duration_s": event.duration_s, "at": event.at}
                self._publish(event.channel_id, "user_clear", data)
            case ChatCleared():
                self._publish(event.channel_id, "chat_clear", {"at": event.at})
