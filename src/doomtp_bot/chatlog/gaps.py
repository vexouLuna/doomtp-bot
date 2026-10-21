"""Where the chat log has holes: the time between one log session's end and the next one's start."""

from __future__ import annotations

from collections.abc import Sequence

MIN_GAP_MS = 5_000  # shorter interruptions aren't worth a request


def gaps_between(sessions: Sequence[tuple[int, int | None]]) -> list[tuple[int, int]]:
    """`(from, to)` between each session's end and the next one's start, for sessions ordered by start.

    The one rule both the bot's backfill and `scripts/coverage.py` go by.
    """
    found: list[tuple[int, int]] = []
    for (_, ended_at), (next_start, _) in zip(sessions, sessions[1:], strict=False):
        if ended_at is None:
            continue  # an unclosed session is closed at startup (chatlog.close_stale_sessions)
        if next_start - int(ended_at) >= MIN_GAP_MS:
            found.append((int(ended_at), next_start))
    return found
