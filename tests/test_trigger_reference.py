"""The public reference of triggers and `$` fields (triggers/reference.py) stays complete and true."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from doomtp_bot.lang.parser import BOT_FIELDS
from doomtp_bot.runtime.values import MISSING
from doomtp_bot.triggers.reference import (
    BOT_FIELD_DOCS,
    BOT_ROOT_DOCS,
    EVENTS,
    NOTICE_TRIGGER_TYPE,
    ROOTS,
    trigger_payload,
)
from doomtp_bot.triggers.service import TRIGGER_TYPES
from doomtp_bot.twitch import mapping
from scripts.reference_docs import DOC, generated
from tests.chat.test_eventsub_contract import parsed
from tests.runtime.helpers import ALICE, CHANNEL, make_runtime

TYPES: dict[str, type] = {"str": str, "int": int, "bool": bool, "list": list, "map": dict}
#: Payload keys that are the bot's own bookkeeping, not fields a trigger is meant to read.
UNDOCUMENTED = {"system_message", "text", "chatter", "detail"}


def _leaves(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """`{"user": {"id": 1}}` → `{"user.id": 1}`. A None stays a leaf: the key is there, its value missing."""
    out: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            out |= _leaves(item, f"{prefix}{key}.")
        else:
            out[f"{prefix}{key}"] = item
    return out


def test_the_docs_tables_are_the_generated_ones() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert generated(text) == text, "docs/namespaces.md is behind: uv run python scripts/reference_docs.py"


def test_every_field_has_a_known_type_and_a_description() -> None:
    fields = [f for doc in ROOTS.values() for f in doc.fields] + [f for e in EVENTS.values() for f in e.variables]
    assert {f.type for f in fields} <= set(TYPES)
    assert all(f.description for f in fields)


@pytest.mark.parametrize("root", ["$chatter", "$channel", "$bot", "$now"])
def test_the_bot_fields_are_what_the_runtime_gives(root: str) -> None:
    runtime = make_runtime()
    channel = dataclasses.replace(CHANNEL, live=True, title="t", game="g", viewers=3, started_at=1.0)
    ctx = runtime.make_context(channel=channel, invoker=ALICE)
    given = runtime.executor.bot_fields(ctx, root[1:])
    assert given is not None
    documented = {f.name: f.type for f in ROOTS[root].fields if "." not in f.name}
    # next_stream is asked of Helix on its own (Executor.next_stream), so it isn't in this dict.
    assert set(given) == set(documented) - {"next_stream"}
    wrong = {k: v for k, v in given.items() if v is not MISSING and not isinstance(v, TYPES[documented[k]])}
    assert wrong == {}


NOTICES = {
    "raid": {"detail": {"user": {"id": "5", "login": "r", "display": "R"}, "viewer_count": 4}},
    "sub": {"chatter": {"id": "7", "login": "sam"}, "detail": {"tier": "1000", "months": 1}},
    "resub": {
        "chatter": {"id": "7", "login": "sam"},
        "detail": {"tier": "1000", "cumulative_months": 14, "streak_months": 3, "prime": False, "gift": False},
        "text": "hi",
    },
    "sub_gift": {
        "chatter": {"id": "7", "login": "sam"},
        "detail": {"tier": "1000", "months": 1, "recipient": {"id": "8", "login": "kim"}},
    },
}


def _payloads() -> list[tuple[str, dict[str, Any]]]:
    recorded = [
        ("follow", mapping.follow(parsed("channel.follow")).payload),
        ("redemption", mapping.redemption(parsed("channel.channel_points_custom_reward_redemption.add")).payload),
        ("cheer", mapping.cheer(parsed("channel.cheer")).payload),
    ]
    built = [(NOTICE_TRIGGER_TYPE.get(n, n), trigger_payload(n, p)) for n, p in NOTICES.items()]
    return recorded + built


@pytest.mark.parametrize(("type_", "payload"), _payloads(), ids=lambda v: v if isinstance(v, str) else "")
def test_each_event_payload_is_what_its_table_says(type_: str, payload: dict[str, Any]) -> None:
    given = {k: v for k, v in _leaves(payload).items() if k.split(".")[0] not in UNDOCUMENTED}
    documented = {f.name: f.type for f in EVENTS[type_].variables}
    assert set(given) <= set(documented)
    assert set(documented) - set(given) <= {"text"}  # resub's and cheer's message sit under `text`
    wrong = {k: v for k, v in given.items() if v is not None and not isinstance(v, TYPES[documented[k]])}
    assert wrong == {}


def test_every_trigger_type_and_bot_field_is_documented() -> None:
    assert tuple(EVENTS) == TRIGGER_TYPES
    assert {r: set(f) for r, f in BOT_FIELDS.items()} == {r: set(f) for r, f in BOT_FIELD_DOCS.items()}
    assert set(BOT_ROOT_DOCS) == set(BOT_FIELDS)


def test_a_raid_reads_as_the_docs_say() -> None:
    detail = {"user": {"id": "500", "login": "raider", "display": "Raider"}, "viewer_count": 42}
    payload = trigger_payload("raid", {"chatter": {"id": "500", "login": "raider"}, "detail": detail})
    assert payload["viewers"] == 42 and payload["user"] == {"id": "500", "name": "raider", "display": "Raider"}


def test_subs_and_gifts_read_as_the_docs_say() -> None:
    chatter = {"id": "7", "login": "sam"}
    resub = trigger_payload(
        "resub", {"chatter": chatter, "detail": {"tier": "1000", "cumulative_months": 14, "months": 1}}
    )
    assert (resub["months"], resub["tier"], resub["user"]["name"]) == (14, "1000", "sam")
    gift = trigger_payload(
        "sub_gift", {"chatter": None, "detail": {"tier": "2000", "recipient": {"id": "8", "login": "kim"}}}
    )
    assert gift["user"] is None and gift["recipient"]["name"] == "kim" and NOTICE_TRIGGER_TYPE["sub_gift"] == "gift_sub"


def test_notices_built_flat_are_left_alone() -> None:
    follow = {"user": {"id": "1", "name": "a", "display": "A"}}
    assert trigger_payload("follow", follow) is follow
