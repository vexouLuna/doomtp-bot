"""The services the routes use, by name and type, in one place.

`create_app` puts them on `app.state`, which FastAPI types as `Any`. Each accessor here reads one,
answers 503 when the bot runs without it, and returns it typed, so mypy checks every route's use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from doomtp_bot.api.keys import ApiKeyService
    from doomtp_bot.api.sessions import AdminAuth
    from doomtp_bot.channels.capabilities import CapabilityProbe
    from doomtp_bot.channels.manager import ChannelManager
    from doomtp_bot.customcmds.packs import PackService
    from doomtp_bot.customcmds.service import CustomCommandService
    from doomtp_bot.filters.service import FilterService
    from doomtp_bot.history.jobs import BackfillJobs
    from doomtp_bot.policy.service import PolicyService
    from doomtp_bot.runtime.engine import Runtime
    from doomtp_bot.storage.db import Connection
    from doomtp_bot.triggers.service import TriggerService
    from doomtp_bot.twitch.client import TwitchService
    from doomtp_bot.variables.access import VariableAccessPolicy
    from doomtp_bot.variables.store import PostgresVariableStore
    from doomtp_bot.webfetch.hosts import HostStore


def required(request: Request, name: str) -> Any:
    found = getattr(request.app.state, name, None)
    if found is None:
        raise HTTPException(status_code=503, detail=f"{name} isn't available")
    return found


def admin_auth(request: Request) -> AdminAuth:
    return cast("AdminAuth", required(request, "admin_auth"))


def api_keys(request: Request) -> ApiKeyService:
    return cast("ApiKeyService", required(request, "api_keys"))


def backfill(request: Request) -> BackfillJobs:
    return cast("BackfillJobs", required(request, "backfill"))


def bot_db(request: Request) -> Connection:
    return cast("Connection", required(request, "bot_db"))


def capabilities(request: Request) -> CapabilityProbe:
    return cast("CapabilityProbe", required(request, "capabilities"))


def channels(request: Request) -> ChannelManager:
    return cast("ChannelManager", required(request, "channels"))


def chatlog_db(request: Request) -> Connection:
    return cast("Connection", required(request, "chatlog_db"))


def customcmds(request: Request) -> CustomCommandService:
    return cast("CustomCommandService", required(request, "customcmds"))


def filters(request: Request) -> FilterService:
    return cast("FilterService", required(request, "filters"))


def http_hosts(request: Request) -> HostStore:
    return cast("HostStore", required(request, "http_hosts"))


def packs(request: Request) -> PackService:
    return cast("PackService", required(request, "packs"))


def policy(request: Request) -> PolicyService:
    return cast("PolicyService", required(request, "policy"))


def runtime(request: Request) -> Runtime:
    return cast("Runtime", required(request, "runtime"))


def triggers(request: Request) -> TriggerService:
    return cast("TriggerService", required(request, "triggers"))


def twitch(request: Request) -> TwitchService:
    return cast("TwitchService", required(request, "twitch"))


def variable_access(request: Request) -> VariableAccessPolicy:
    return cast("VariableAccessPolicy", required(request, "variable_access"))


def variable_store(request: Request) -> PostgresVariableStore:
    return cast("PostgresVariableStore", required(request, "variable_store"))
