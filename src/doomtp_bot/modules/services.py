"""The services the built-in commands use, by name and type (the runtime's `Services`, extended).

Everything is optional: a test, or a bot without Twitch, wires only what it uses. `service()` is for a
command that can't do without one, and raises when the bot wasn't wired with it: that is a bug in the
wiring, not something the chatter typed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, overload

from doomtp_bot.runtime.services import Services

if TYPE_CHECKING:
    from doomtp_bot.channels.manager import ChannelManager
    from doomtp_bot.chatlog.writer import ChatLogWriter
    from doomtp_bot.core.outbox import Outbox
    from doomtp_bot.customcmds.packs import PackService
    from doomtp_bot.customcmds.service import CustomCommandService
    from doomtp_bot.filters.service import FilterService
    from doomtp_bot.history.jobs import BackfillJobs
    from doomtp_bot.history.provider import HistoryProvider
    from doomtp_bot.policy.service import PolicyService
    from doomtp_bot.runtime.context import CommandContext
    from doomtp_bot.runtime.engine import Runtime
    from doomtp_bot.runtime.explain import ReportStore
    from doomtp_bot.runtime.registry import CommandRegistry
    from doomtp_bot.storage.db import Connection
    from doomtp_bot.triggers.service import TriggerService
    from doomtp_bot.twitch.client import TwitchService
    from doomtp_bot.variables.access import VariableAccessPolicy
    from doomtp_bot.variables.store import PostgresVariableStore
    from doomtp_bot.webfetch.fetcher import HttpFetcher
    from doomtp_bot.webfetch.hosts import HostStore


@dataclass
class BotServices(Services):
    policy: PolicyService | None = None
    customcmds: CustomCommandService | None = None
    packs: PackService | None = None
    triggers: TriggerService | None = None
    filters: FilterService | None = None
    channels: ChannelManager | None = None
    variable_store: PostgresVariableStore | None = None
    variable_access: VariableAccessPolicy | None = None
    http: HttpFetcher | None = None  # `http get` (ADR-0020)
    http_hosts: HostStore | None = None
    backfill: BackfillJobs | None = None  # `!backfill` queues jobs here (ADR-0024 §5)
    history: HistoryProvider | None = None
    twitch: TwitchService | None = None
    outbox: Outbox | None = None  # `mecho` sends its lines itself, one message each
    chatlog_db: Connection | None = None  # logsearch reads the log through chatlog/queries.py
    chatlog_writer: ChatLogWriter | None = None  # `http` logs each request it makes
    explain_reports: ReportStore | None = None
    login_for: Callable[[str], Awaitable[str | None]] | None = None
    connect_url: str | None = None  # where `!join` sends a broadcaster (ADR-0007)
    site_url: str | None = None  # the web site `!help` links to


def services(ctx: CommandContext) -> BotServices:
    """The run's services. A run wired with the runtime's bare `Services` (a test of the runtime alone)
    has none of the bot's."""
    found = ctx.exec.services
    return found if isinstance(found, BotServices) else BotServices()


# fmt: off
@overload
def service(ctx: CommandContext, name: Literal["runtime"]) -> Runtime: ...
@overload
def service(ctx: CommandContext, name: Literal["registry"]) -> CommandRegistry: ...
@overload
def service(ctx: CommandContext, name: Literal["policy"]) -> PolicyService: ...
@overload
def service(ctx: CommandContext, name: Literal["customcmds"]) -> CustomCommandService: ...
@overload
def service(ctx: CommandContext, name: Literal["packs"]) -> PackService: ...
@overload
def service(ctx: CommandContext, name: Literal["triggers"]) -> TriggerService: ...
@overload
def service(ctx: CommandContext, name: Literal["filters"]) -> FilterService: ...
@overload
def service(ctx: CommandContext, name: Literal["channels"]) -> ChannelManager: ...
@overload
def service(ctx: CommandContext, name: Literal["variable_store"]) -> PostgresVariableStore: ...
@overload
def service(ctx: CommandContext, name: Literal["variable_access"]) -> VariableAccessPolicy: ...
@overload
def service(ctx: CommandContext, name: Literal["http"]) -> HttpFetcher: ...
@overload
def service(ctx: CommandContext, name: Literal["http_hosts"]) -> HostStore: ...
@overload
def service(ctx: CommandContext, name: Literal["twitch"]) -> TwitchService: ...
@overload
def service(ctx: CommandContext, name: Literal["chatlog_db"]) -> Connection: ...
# fmt: on
def service(ctx: CommandContext, name: str) -> Any:
    """A service the command can't do without."""
    found = getattr(services(ctx), name)
    if found is None:
        raise RuntimeError(f"service {name!r} is not configured")
    return found
