"""The services a built-in command reaches, by name and type (modules/services.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from doomtp_bot.lang.parser import Context
from doomtp_bot.modules.channels import _connect_link
from doomtp_bot.modules.services import BotServices, service, services
from doomtp_bot.runtime.context import ChannelInfo, CommandContext, ExecContext
from doomtp_bot.runtime.services import Services
from doomtp_bot.runtime.variables import InMemoryVariableStore, VariableSession


def command(wired: Services) -> CommandContext:
    run = ExecContext(
        context=Context.LINE,
        channel=ChannelInfo("100", "somechannel"),
        invoker=None,
        variables=VariableSession(InMemoryVariableStore()),
        services=wired,
    )
    return CommandContext(run, "test")


def test_a_command_reaches_what_the_bot_was_wired_with() -> None:
    wired = BotServices(site_url="https://site.example")
    ctx = command(wired)
    assert services(ctx) is wired
    assert services(ctx).site_url == "https://site.example"


def test_a_missing_service_is_the_wiring_at_fault() -> None:
    with pytest.raises(RuntimeError, match="'policy' is not configured"):
        service(command(BotServices()), "policy")


def test_the_runtime_alone_carries_none_of_the_bots_services() -> None:
    ctx = command(Services())
    assert services(ctx) == BotServices()
    with pytest.raises(RuntimeError):
        service(ctx, "twitch")


def test_join_before_the_bot_has_signed_in_says_so() -> None:
    twitch = SimpleNamespace(bot_id=None, bot_login=None)
    result = _connect_link(command(BotServices(connect_url="https://bot.example/auth/connect")),
                           twitch.bot_id, twitch.bot_login)  # fmt: skip
    assert not result.ok and "isn't signed in" in result.message
