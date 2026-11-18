"""Chatters' pronouns for the chat overlay (ADR-0039), looked up by the bot so no browser calls a third party.

The source is alejo.io's pronoun service, the one the Twitch Chat Pronouns extension and Chatterino's and
FFZ's add-ons read. It publishes no API reference; what this relies on is what it answers (2026-11-18):

    GET /v1/pronouns         -> {<id>: {name, subject, object, singular}}, one table
    GET /v1/users/{login}    -> {channel_id, channel_login, pronoun_id, alt_pronoun_id}, or 404 for nobody

Only a chatter's login is sent. Answers are cached; a slow or failing service makes a lookup `unknown`,
never an error, and an `unknown` is not cached, so the next ask tries again.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import aiohttp
import structlog

log = structlog.get_logger(__name__)

BASE_URL = "https://api.pronouns.alejo.io/v1"
TIMEOUT_S = 3.0  # per upstream request
TABLE_TTL_S = 24 * 3600.0
FOUND_TTL_S = 3600.0  # someone with pronouns set, or with none
CACHE_SIZE = 10_000
CONCURRENCY = 8  # upstream requests at once, across every caller

Status = Literal["set", "none", "unknown"]

#: GET `path` under the service → (HTTP status, parsed JSON or None). Raises on a network failure.
Fetch = Callable[[str], Awaitable[tuple[int, Any]]]


@dataclass(frozen=True, slots=True)
class Pronouns:
    login: str
    status: Status  # set: `display` holds them; none: they set none; unknown: the service didn't answer
    display: str | None  # "She/Her", "He/Him · They/Them"; a singular one ("Any") stands alone


def _show(entry: dict[str, Any]) -> str:
    subject, obj = str(entry.get("subject") or ""), str(entry.get("object") or "")
    return subject if entry.get("singular") or not obj else f"{subject}/{obj}"


class AiohttpFetch:
    """The real upstream: one session, a short timeout, a client name that says who asks."""

    def __init__(self, base_url: str = BASE_URL, timeout_s: float = TIMEOUT_S) -> None:
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None

    async def __call__(self, path: str) -> tuple[int, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout, headers={"User-Agent": "doomtp-bot"})
        async with self._session.get(f"{self.base_url}{path}") as response:
            if response.status != 200:
                return response.status, None
            return 200, await response.json(content_type=None)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class PronounService:
    def __init__(self, fetch: Fetch, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.fetch = fetch
        self.clock = clock
        self._table: tuple[float, dict[str, Any]] | None = None
        self._cache: OrderedDict[str, tuple[float, Pronouns]] = OrderedDict()
        self._slots = asyncio.Semaphore(CONCURRENCY)

    async def lookup(self, logins: Sequence[str]) -> list[Pronouns]:
        """Each login's pronouns, in the order asked. Never raises for the service's sake."""
        wanted = list(dict.fromkeys(login.lower().lstrip("@") for login in logins))
        found = {login: hit for login in wanted if (hit := self._cached(login)) is not None}
        missing = [login for login in wanted if login not in found]
        if missing:
            table = await self._load_table()
            for result in await asyncio.gather(*(self._one(login, table) for login in missing)):
                found[result.login] = result
        return [found[login] for login in wanted]

    def _cached(self, login: str) -> Pronouns | None:
        hit = self._cache.get(login)
        if hit is None:
            return None
        if self.clock() - hit[0] >= FOUND_TTL_S:
            del self._cache[login]
            return None
        self._cache.move_to_end(login)
        return hit[1]

    def _remember(self, result: Pronouns) -> Pronouns:
        if result.status != "unknown":
            self._cache[result.login] = (self.clock(), result)
            self._cache.move_to_end(result.login)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return result

    async def _get(self, path: str) -> tuple[int, Any] | None:
        """The service's answer, or None when it couldn't be had in time."""
        try:
            async with self._slots:
                return await asyncio.wait_for(self.fetch(path), TIMEOUT_S)
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            log.warning("pronouns.upstream_failed", path=path, error=repr(exc))
            return None

    async def _load_table(self) -> dict[str, Any] | None:
        if self._table is not None and self.clock() - self._table[0] < TABLE_TTL_S:
            return self._table[1]
        answer = await self._get("/pronouns")
        if answer is None or answer[0] != 200 or not isinstance(answer[1], dict):
            return self._table[1] if self._table is not None else None  # a stale table beats none
        self._table = (self.clock(), answer[1])
        return answer[1]

    async def _one(self, login: str, table: dict[str, Any] | None) -> Pronouns:
        if table is None:
            return Pronouns(login, "unknown", None)
        answer = await self._get(f"/users/{login}")
        if answer is None or answer[0] not in (200, 404):
            return Pronouns(login, "unknown", None)
        if answer[0] == 404 or not isinstance(answer[1], dict):
            return self._remember(Pronouns(login, "none", None))
        ids = (answer[1].get("pronoun_id"), answer[1].get("alt_pronoun_id"))
        names = [_show(table[i]) for i in ids if isinstance(i, str) and isinstance(table.get(i), dict)]
        return self._remember(Pronouns(login, "set", " · ".join(names)) if names else Pronouns(login, "none", None))
