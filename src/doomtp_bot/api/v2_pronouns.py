"""/api/v2/channels/{login}/pronouns: chatters' pronouns for the chat overlay (ADR-0039).

Under the live feed's terms (the log's access rule): anyone while the channel's public log is on, its
moderators always. The bot asks the pronoun service itself and caches the answers (`pronouns.py`), so the
overlay never calls a third party. The service failing makes a chatter `unknown`, never an error.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field
from vex_platform.api import ApiError, ApiModel, Page

from doomtp_bot.api.access import Caller
from doomtp_bot.api.routes.log import LOG_READ
from doomtp_bot.api.v2_log import _channel

MAX_USERS = 50
_LOGIN = r"^@?[A-Za-z0-9_]{1,25}$"
_USERS = Query(
    min_length=1,
    max_length=MAX_USERS,
    description=f"A chatter's login; repeat for several, at most {MAX_USERS}",
)


class ChatterPronouns(ApiModel):  # type: ignore[misc]  # vex-platform ships no type information
    login: str
    status: Literal["set", "none", "unknown"] = Field(
        description="set: `display` holds them. none: they set none. unknown: the pronoun service didn't "
        "answer in time; ask again later"
    )
    display: str | None = Field(description='"She/Her", "He/Him · They/Them", or "Any" for a singular one')


def pronouns_router() -> APIRouter:
    router = APIRouter(tags=["log"])

    @router.get(
        "/channels/{login}/pronouns",
        response_model=Page[ChatterPronouns],
        summary="Chatters' pronouns, for the chat overlay",
    )
    async def channel_pronouns(
        request: Request,
        login: str,
        user: Annotated[list[Annotated[str, Field(pattern=_LOGIN)]], _USERS],
        caller: Caller = LOG_READ,
    ) -> Page[Any]:
        """The pronouns each chatter set with the Twitch pronouns extensions, in the order asked (duplicates
        once). Cached by the bot for an hour; a chatter the service couldn't be asked about comes back
        `unknown` and is asked again next time. One page: `next_cursor` is always null."""
        _channel(request, login)
        service = getattr(request.app.state, "pronouns", None)
        if service is None:
            raise ApiError(503, "unavailable", "pronoun lookups aren't available")
        found = await service.lookup(user)
        items = [{"login": p.login, "status": p.status, "display": p.display} for p in found]
        return Page[Any](items=items, next_cursor=None)

    return router
