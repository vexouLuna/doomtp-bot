"""Alternate prefixes (spec §2.1.1): only line-start, only listed commands, rewritten to the main sign."""

from __future__ import annotations

import pytest

from doomtp_bot.lang.altprefix import AltPrefix, dump_alt_prefixes, forward, load_alt_prefixes
from doomtp_bot.modules.core_admin import main_prefix_clash, validate_alt_prefixes

LISTED = (AltPrefix("?", ("ping", "so")),)
ALL = (AltPrefix("$", None),)


@pytest.mark.parametrize(
    ("text", "alternates", "expected"),
    [
        ("?ping", LISTED, "!ping"),
        ("?PING now", LISTED, "!PING now"),
        ("?so @bob", LISTED, "!so @bob"),
        ("?@ping", LISTED, "!@ping"),  # the personal form
        ("  ?ping  ", LISTED, "!ping"),
        ("?echo hi", LISTED, None),  # not in the list
        ("$echo hi", ALL, "!echo hi"),
        ("$anything", ALL, "!anything"),
        ("say ?ping", LISTED, None),  # mid-line
        ("( ?ping )", LISTED, None),  # only the very start of the line
        ("$1+2", ALL, None),  # an expression line, not a command name
        ("$100", ALL, None),
        ("?ping{x}", LISTED, None),
        ("? ping", LISTED, None),  # an ASCII sign takes no gap, like the main one
        ("?ping", (), None),
    ],
)
def test_forward(text: str, alternates: tuple[AltPrefix, ...], expected: str | None) -> None:
    assert forward(text, "!", alternates) == expected


def test_emoji_alternate_allows_a_gap_and_the_reply_mention_goes() -> None:
    alt = (AltPrefix("\U0001f3dc", ("ping",)),)
    assert forward("\U0001f3dc️ ping", "!", alt) == "!ping"
    assert forward("@Bob ?ping", "!", LISTED, ("Bob",)) == "!ping"


def test_the_column_round_trips() -> None:
    both = LISTED + ALL
    assert load_alt_prefixes(dump_alt_prefixes(both)) == both
    assert load_alt_prefixes("[]") == ()


def test_collisions_and_prefix_rules_are_refused() -> None:
    assert validate_alt_prefixes("!", LISTED + ALL) is None
    assert "already the prefix" in (validate_alt_prefixes("!", (AltPrefix("!", None),)) or "")
    assert "already an alternate" in (validate_alt_prefixes("!", (AltPrefix("?", None), AltPrefix("?", ("a",)))) or "")
    emoji = "\U0001f3dc"
    assert validate_alt_prefixes(emoji, (AltPrefix(emoji + "️", None),)) is not None  # same sign
    assert "alternate prefix" in (validate_alt_prefixes("!", (AltPrefix("a", None),)) or "")  # main-sign rules
    assert validate_alt_prefixes("!", (AltPrefix("?", ()),)) is not None  # empty list: say "all"
    assert validate_alt_prefixes("!", (AltPrefix("?", ("no spaces",)),)) is not None
    assert validate_alt_prefixes("!", tuple(AltPrefix(s, None) for s in "?$%^~;")) is not None  # too many
    assert main_prefix_clash("?", LISTED) is not None and main_prefix_clash("!", LISTED) is None
