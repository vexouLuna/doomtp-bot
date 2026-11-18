"""Alternate prefixes (spec §2.1.1): extra signs that forward chosen commands to the channel's own sign.

Only at the very start of a chat line, and only when the command name is in the alternate's list (or the
list is "all"). The line is rewritten to the main sign before parsing, so expressions, triggers, help and
the editor only ever see the one sign.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from doomtp_bot.lang.parser import VARIATION_SELECTOR, after_prefix, is_ws, preprocess_line

MAX_ALT_PREFIXES = 5
MAX_ALT_COMMANDS = 50


@dataclass(frozen=True, slots=True)
class AltPrefix:
    prefix: str
    commands: tuple[str, ...] | None  # None: every command ("all")

    def to_json(self) -> dict[str, object]:
        return {"prefix": self.prefix, "commands": "all" if self.commands is None else list(self.commands)}


def same_sign(a: str, b: str) -> bool:
    """Signs that match the same chat text: U+FE0F doesn't count (see `after_prefix`)."""
    return a.replace(VARIATION_SELECTOR, "") == b.replace(VARIATION_SELECTOR, "")


def load_alt_prefixes(raw: str | None) -> tuple[AltPrefix, ...]:
    """The `channels.alt_prefixes` column: a JSON list of `{"prefix", "commands": [...] | "all"}`."""
    found = []
    for item in json.loads(raw or "[]"):
        commands = item["commands"]
        found.append(AltPrefix(item["prefix"], None if commands == "all" else tuple(commands)))
    return tuple(found)


def dump_alt_prefixes(alternates: Sequence[AltPrefix]) -> str:
    return json.dumps([a.to_json() for a in alternates])


def forward(
    text: str,
    prefix: str,
    alternates: Sequence[AltPrefix],
    reply_parent_login: str | Sequence[str] | None = None,
) -> str | None:
    """The line rewritten to `prefix` when it starts with an alternate that forwards its command, else None.

    The result is already pre-processed (spec §2.1 steps 1–3): the reply mention is gone.
    """
    if not alternates:
        return None
    line = preprocess_line(text, reply_parent_login)
    for alt in alternates:
        end = after_prefix(line, 0, alt.prefix)
        if end is None:
            continue
        at = end + 1 if line.startswith("@", end) else end  # `?@ping`: the personal form
        stop = at
        while stop < len(line) and line[stop].isascii() and (line[stop].isalnum() or line[stop] in "_-"):
            stop += 1
        name = line[at:stop].lower()
        # A name, then a space or the end: `?1+2` and `?ping{…}` aren't a command name we can match.
        if not name or not name[0].isalnum() or name.isdigit() or (stop < len(line) and not is_ws(line[stop])):
            continue
        if alt.commands is not None and name not in alt.commands:
            continue
        return prefix + line[end:]
    return None
