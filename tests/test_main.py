"""`__main__.run` (`app/bot.py`): the bot's wiring, start and shutdown, against a real database (ADR-0004).

`run` builds every service, hands the web app to uvicorn and, when uvicorn returns, shuts everything down.
These tests stand a fake in for uvicorn's `serve`: it looks at the app the bot built, asks it a few things
the way a browser would, and returns, which is how a real shutdown begins. Nothing here talks to Twitch:
without a stored bot token the client never connects.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
import uvicorn
from fastapi import FastAPI

from doomtp_bot import __main__
from doomtp_bot.config import Settings
from doomtp_bot.core.outbox import Outbox
from doomtp_bot.core.schedule import NextStreams
from doomtp_bot.customcmds.system import CoreNotInstalled
from doomtp_bot.modules.services import BotServices
from doomtp_bot.runtime.engine import Runtime
from doomtp_bot.storage import schema
from doomtp_bot.storage.db import Databases
from doomtp_bot.twitch.auth import TwitchAuth
from doomtp_bot.twitch.client import TwitchService
from doomtp_bot.twitch.signin import TwitchSignIn, VexSignIn

Probe = Callable[[FastAPI], Awaitable[None]]


def settings_for(dsn: str, tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": dsn,
        "data_dir": tmp_path,
        "twitch_client_id": None,
        "twitch_client_secret": None,
        "twitch_bot_id": None,
        "public_base_url": "https://bot.example",
        "web_site_url": "https://site.example",
        "admin_password": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]  # pydantic-settings' init-only option


async def run_with(settings: Settings, monkeypatch: pytest.MonkeyPatch, probe: Probe) -> FastAPI:
    """Run the bot until `probe` is done with the app it serves, then let it shut down."""
    served: list[FastAPI] = []

    async def serve(self: uvicorn.Server, sockets: Any = None) -> None:
        served.append(self.config.app)
        await asyncio.sleep(0.05)  # the Twitch start runs as a task beside the server
        await probe(self.config.app)

    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    await __main__.run(settings)
    assert len(served) == 1
    return served[0]


async def get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://bot.example") as http:
        return await http.get(path, follow_redirects=False)


def connections(dsn: str) -> int:
    name = dsn.rsplit("/", 1)[-1]
    with psycopg.connect(dsn.rsplit("/", 1)[0] + "/postgres", autocommit=True) as admin:
        row = admin.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (name,)).fetchone()
    return int(row[0]) if row else 0


async def test_without_twitch_the_bot_serves_its_api_and_shuts_down_cleanly(
    committed_database: tuple[str, Databases], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dsn, dbs = committed_database
    before = connections(dsn)
    seen: dict[str, Any] = {}

    async def probe(app: FastAPI) -> None:
        seen["healthz"] = (await get(app, "/healthz")).status_code
        seen["readyz"] = (await get(app, "/readyz")).json()
        seen["login"] = (await get(app, "/auth/login")).status_code
        seen["connections"] = connections(dsn)

    app = await run_with(settings_for(dsn, tmp_path), monkeypatch, probe)

    assert seen["healthz"] == 200
    components = seen["readyz"]["components"]
    assert components["databases"] == {
        "status": "ok",
        "bot_schema": schema.head("bot"),
        "chatlog_schema": schema.head("chatlog"),
    }
    assert components["chatlog"]["status"] == "ok"
    assert components["twitch"]["status"] == "disabled"
    assert seen["login"] == 503  # Twitch isn't configured
    assert seen["connections"] > before  # the bot held its own while it ran

    # The wiring the commands and the API rely on.
    runtime = app.state.runtime
    assert isinstance(runtime, Runtime)
    services = runtime.services
    assert isinstance(services, BotServices)
    assert services.runtime is runtime and services.registry is runtime.registry
    assert services.policy is app.state.policy
    assert services.customcmds is app.state.customcmds and services.packs is app.state.packs
    assert services.triggers is app.state.triggers and services.filters is app.state.filters
    assert services.channels is app.state.channels and services.variable_store is app.state.variable_store
    assert services.variable_access is app.state.variable_access
    assert services.http_hosts is app.state.http_hosts and services.http is not None
    assert services.backfill is app.state.backfill and services.history is not None
    assert services.explain_reports is app.state.explain_reports
    assert services.chatlog_db is app.state.chatlog_db and services.chatlog_writer is not None
    assert isinstance(services.outbox, Outbox)
    assert services.site_url == "https://site.example"
    assert (services.twitch, services.login_for, services.schedule, services.connect_url) == (None, None, None, None)
    assert app.state.twitch is None and app.state.twitch_auth is None and app.state.twitch_signin is None
    assert app.state.secure_cookies is True  # PUBLIC_BASE_URL is https

    # Shutdown: this start is recorded and ended, and every connection the bot opened is closed.
    async with await dbs.chatlog.execute("SELECT ended_at FROM bot_runs") as cur:
        runs = await cur.fetchall()
    assert len(runs) == 1 and runs[0]["ended_at"] is not None
    assert connections(dsn) == before


async def test_with_twitch_configured_the_sign_ins_and_the_connect_link_are_wired(
    committed_database: tuple[str, Databases], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dsn, _ = committed_database
    started: list[bool] = []
    real_start = TwitchService.start

    async def start(self: TwitchService) -> bool:
        started.append(await real_start(self))  # no stored token: False, and nothing reaches Twitch
        return started[-1]

    monkeypatch.setattr(TwitchService, "start", start)
    seen: dict[str, Any] = {}

    async def probe(app: FastAPI) -> None:
        seen["readyz"] = (await get(app, "/readyz")).json()
        seen["login"] = await get(app, "/auth/login")

    settings = settings_for(
        dsn, tmp_path, twitch_client_id="dummy-client", twitch_client_secret="dummy-secret", twitch_bot_id="999"
    )
    app = await run_with(settings, monkeypatch, probe)

    assert started == [False]  # tried once at start, found no token
    assert seen["readyz"]["components"]["twitch"]["status"] == "degraded"
    assert seen["login"].status_code == 302 and seen["login"].headers["location"].startswith("https://id.twitch.tv/")
    services = app.state.runtime.services
    assert isinstance(app.state.twitch, TwitchService) and services.twitch is app.state.twitch
    assert services.login_for == app.state.twitch.login_for and isinstance(services.schedule, NextStreams)
    assert services.connect_url == "https://bot.example/auth/connect"
    assert isinstance(app.state.twitch_auth, TwitchAuth) and app.state.twitch_auth.expected_bot_id == "999"
    assert isinstance(app.state.twitch_signin, TwitchSignIn)


async def test_the_shared_sign_in_needs_its_url_and_secret(
    committed_database: tuple[str, Databases], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dsn, _ = committed_database

    async def nothing(app: FastAPI) -> None:
        return None

    vex = {"signin_provider": "vex", "vex_auth_url": "https://auth.example", "vex_auth_client_secret": "dummy"}
    app = await run_with(settings_for(dsn, tmp_path, **vex), monkeypatch, nothing)
    assert isinstance(app.state.twitch_signin, VexSignIn)

    without = {"signin_provider": "vex", "vex_auth_url": None}
    app = await run_with(settings_for(dsn, tmp_path, **without), monkeypatch, nothing)
    assert app.state.twitch_signin is None


async def test_a_signed_in_bot_without_the_core_pack_refuses_to_start(
    committed_database: tuple[str, Databases], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dsn, dbs = committed_database
    before = connections(dsn)
    await dbs.bot.execute(
        "INSERT INTO oauth_tokens (identity, user_id, login, access_token, refresh_token, scopes, updated_at)"
        " VALUES ('bot', '999', 'doomtp_bot', 'dummy', 'dummy', '[]', 0)"
    )
    await dbs.bot.commit()

    async def never(app: FastAPI) -> None:
        raise AssertionError("the bot started serving")

    with pytest.raises(CoreNotInstalled):
        await run_with(settings_for(dsn, tmp_path), monkeypatch, never)
    assert connections(dsn) == before  # it closed what it had opened
