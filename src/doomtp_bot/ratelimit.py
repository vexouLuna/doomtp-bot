"""A sliding window of events per key, shared by the login and read limiters and `http get`.

Keys are client addresses, channels or hosts, and a client can make up as many addresses as it likes, so
the window forgets a key once all its events have aged out. That sweep runs at most once per window, so
the keys kept are the ones seen in about the last two windows, whatever the traffic before that.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class SlidingWindow:
    def __init__(self, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.window_s = window_s
        self.clock = clock
        self._events: dict[str, deque[float]] = {}
        self._swept_at = clock()

    def __len__(self) -> int:
        """How many keys are remembered."""
        return len(self._events)

    def _recent(self, key: str, now: float) -> deque[float] | None:
        events = self._events.get(key)
        if events is None:
            return None
        while events and now - events[0] >= self.window_s:
            events.popleft()
        if not events:
            del self._events[key]
            return None
        return events

    def _sweep(self, now: float) -> None:
        if now - self._swept_at < self.window_s:
            return
        self._swept_at = now
        for key in [k for k, events in self._events.items() if now - events[-1] >= self.window_s]:
            del self._events[key]

    def count(self, key: str) -> int:
        """Events for `key` inside the window."""
        events = self._recent(key, self.clock())
        return len(events) if events is not None else 0

    def oldest(self, key: str) -> float | None:
        """When the oldest event for `key` still inside the window happened, or None without one."""
        events = self._recent(key, self.clock())
        return events[0] if events is not None else None

    def add(self, key: str) -> None:
        now = self.clock()
        self._sweep(now)
        self._events.setdefault(key, deque()).append(now)

    def clear(self, key: str) -> None:
        self._events.pop(key, None)
