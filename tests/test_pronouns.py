"""Chatters' pronouns (ADR-0039): the bot asks the pronoun service, caches it, and never fails chat for it."""
# ruff: noqa: F811  (the imported fixtures are parameters here, which ruff reads as redefinitions)

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from doomtp_bot import pronouns
from doomtp_bot.api.keys import ApiKeyService
from doomtp_bot.api.routes.log import _log_reader
from doomtp_bot.api.v2_pronouns import pronouns_router
from doomtp_bot.policy.repository import Actor
from doomtp_bot.pronouns import Pronouns, PronounService
from tests.test_api_data import (  # noqa: F401  (fixtures)
    CHANNEL_ID,
    CHANNEL_LOGIN,
    app_and_keys,
    auth,
    client,
    write_key,
)

TABLE = {
    "hehim": {"name": "hehim", "subject": "He", "object": "Him", "singular": False},
    "theythem": {"name": "theythem", "subject": "They", "object": "Them", "singular": False},
    "any": {"name": "any", "subject": "Any", "object": "", "singular": True},
}
USERS = {
    "alice": {"channel_login": "alice", "pronoun_id": "hehim", "alt_pronoun_id": "theythem"},
    "bob": {"channel_login": "bob", "pronoun_id": "any", "alt_pronoun_id": None},
    "carol": {"channel_login": "carol", "pronoun_id": "gone", "alt_pronoun_id": None},  # not in the table
}


class FakeUpstream:
    def __init__(self) -> None:
        self.asked: list[str] = []
        self.down = False  # every request fails
        self.slow: set[str] = set()  # paths that never answer in time
        self.status: dict[str, int] = {}

    async def __call__(self, path: str) -> tuple[int, Any]:
        self.asked.append(path)
        if self.down:
            raise pronouns.aiohttp.ClientConnectionError("down")
        if path in self.slow:
            await asyncio.sleep(60)
        if path in self.status:
            return self.status[path], None
        if path == "/pronouns":
            return 200, TABLE
        login = path.removeprefix("/users/")
        return (200, USERS[login]) if login in USERS else (404, None)


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_lookups_read_the_table_and_cache_the_answers() -> None:
    up, clock = FakeUpstream(), Clock()
    service = PronounService(up, clock=clock)
    found = await service.lookup(["Alice", "@bob", "carol", "dave", "alice"])
    assert found == [
        Pronouns("alice", "set", "He/Him · They/Them"),
        Pronouns("bob", "set", "Any"),  # a singular one stands alone
        Pronouns("carol", "none", None),  # an id the table doesn't know
        Pronouns("dave", "none", None),  # 404: never set any
    ]
    assert sorted(up.asked) == ["/pronouns", "/users/alice", "/users/bob", "/users/carol", "/users/dave"]
    up.asked.clear()
    await service.lookup(["alice", "dave"])
    assert up.asked == []  # cached, both the found and the none
    clock.now = pronouns.FOUND_TTL_S
    await service.lookup(["alice"])
    assert up.asked == ["/users/alice"]  # the hour is up; the table lasts a day


async def test_a_failing_service_answers_unknown_and_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pronouns, "TIMEOUT_S", 0.05)
    up = FakeUpstream()
    service = PronounService(up)
    up.down = True
    assert await service.lookup(["alice"]) == [Pronouns("alice", "unknown", None)]  # no table: all unknown
    up.down = False
    up.slow.add("/users/bob")
    up.status["/users/carol"] = 503
    found = await service.lookup(["alice", "bob", "carol"])
    assert [p.status for p in found] == ["set", "unknown", "unknown"]  # a timeout, a 5xx
    up.slow.clear()
    up.status.clear()
    up.asked.clear()
    found = await service.lookup(["alice", "bob", "carol"])
    assert [p.status for p in found] == ["set", "set", "none"]  # unknowns weren't cached
    assert sorted(up.asked) == ["/users/bob", "/users/carol"]


async def test_a_stale_table_is_kept_when_the_service_fails_to_refresh_it() -> None:
    up, clock = FakeUpstream(), Clock()
    service = PronounService(up, clock=clock)
    await service.lookup(["alice"])
    clock.now = pronouns.TABLE_TTL_S + 1
    up.status["/pronouns"] = 500
    assert await service.lookup(["bob"]) == [Pronouns("bob", "set", "Any")]


# ── the route ─────────────────────────────────────────────────────────────
PRONOUNS = f"/api/v2/channels/{CHANNEL_LOGIN}/pronouns"


@pytest.fixture
def upstream(app_and_keys: tuple[Any, ApiKeyService]) -> FakeUpstream:
    up = FakeUpstream()
    app_and_keys[0].state.pronouns = PronounService(up)
    return up


def test_the_route_is_the_logs_reader() -> None:
    """The live feed's terms: its moderators, or anyone while the log is public (ADR-0026)."""
    for route in pronouns_router().routes:
        calls = [d.call for d in route.dependant.dependencies]  # type: ignore[attr-defined]
        assert calls == [_log_reader], route.path  # type: ignore[attr-defined]


async def test_the_public_get_pronouns_in_v2s_shape(client: httpx.AsyncClient, upstream: FakeUpstream) -> None:
    response = await client.get(PRONOUNS, params=[("user", "alice"), ("user", "dave")])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {"login": "alice", "status": "set", "display": "He/Him · They/Them"},
            {"login": "dave", "status": "none", "display": None},
        ],
        "next_cursor": None,
    }
    upstream.down = True
    gone = await client.get(PRONOUNS, params={"user": "zed"})
    assert gone.status_code == 200 and gone.json()["items"][0]["status"] == "unknown"


async def test_bad_asks_are_refused(
    client: httpx.AsyncClient, app_and_keys: tuple[Any, ApiKeyService], write_key: str, upstream: FakeUpstream
) -> None:
    for params in ({}, {"user": "no/slashes"}, {"user": "x" * 26}, [("user", f"u{n}") for n in range(51)]):
        bad = await client.get(PRONOUNS, params=params)
        assert (bad.status_code, bad.json()["code"]) == (422, "invalid"), params
    assert upstream.asked == []
    missing = await client.get("/api/v2/channels/nobody/pronouns", params={"user": "a"}, headers=auth(write_key))
    assert (missing.status_code, missing.json()["code"]) == (404, "unknown_channel")

    await app_and_keys[0].state.policy.mutate(
        lambda repo: repo.set_channel_field(CHANNEL_ID, "public_log", False, Actor(None, "test"))
    )
    assert (await client.get(PRONOUNS, params={"user": "alice"})).status_code == 401
    mod = await client.get(PRONOUNS, params={"user": "alice"}, headers=auth(write_key))
    assert mod.status_code == 200  # moderators still may


async def test_without_the_service_it_is_unavailable(
    client: httpx.AsyncClient, app_and_keys: tuple[Any, ApiKeyService]
) -> None:
    app_and_keys[0].state.pronouns = None
    response = await client.get(PRONOUNS, params={"user": "alice"})
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")
