"""The built-in slur list: what the bot itself must never say, so a channel's commands can't get it banned.

Only slurs, never general profanity: "fuck" or "cum" pass. `base` applies in every channel and nothing
turns it off; a channel's `slur_filter` setting can only add `strict` or `strictest` on top. These run
after the channel's own filters and apart from them, so a channel's allow entries can't let a slur through.
Outgoing messages only (FilterService.apply): AutoMod and save-time checks use the channel's own lists.

Hand-picked, because no maintained slurs-only list exists. Cross-checked against
github.com/dsojevic/profanity-list (MIT, its racial/lgbtq/religious tags) and Wikipedia's
"List of ethnic slurs" and "List of LGBTQ slang". Words are matched like `word` filter entries, so
leetspeak, accents, spacing and repeated letters are caught, and a plural "s" is added for each.
"""

from __future__ import annotations

from typing import Literal

from doomtp_bot.filters.matcher import ChannelFilter, FilterEntry
from doomtp_bot.patterns import compile_pattern

Level = Literal["base", "strict", "strictest"]
LEVELS: tuple[Level, ...] = ("base", "strict", "strictest")

WORDS: dict[Level, tuple[str, ...]] = {
    "base": (
        "nigger", "nigga", "niggah", "faggot", "fag", "kike", "chink", "spic", "spick", "wetback", "gook",
        "tranny", "trannie", "sand nigger", "beaner", "raghead", "towelhead", "jigaboo", "porch monkey",
        "zipperhead", "paki",
    ),
    "strict": (
        "retard", "coon", "dyke", "shemale", "golliwog", "darkie", "pikey", "wog", "slant eye",
        "camel jockey",
    ),
    "strictest": ("negro", "spaz", "tard", "mong", "lesbo", "homo", "poof", "ching chong", "honky"),
}  # fmt: skip
# Normalization folds "nigger" to "niger": keep the country, matched as typed so "n1ger" still hits.
# Allow entries match normalized text, which would let "nigger" through too, so these are raw regexes.
ALLOW = (r"\bniger\b",)


def _build(level: Level) -> ChannelFilter:
    words = [w for lvl in LEVELS[: LEVELS.index(level) + 1] for w in WORDS[lvl]]
    entries = [FilterEntry(-i, "", p, "word", "mask", "slur") for i, p in enumerate(
        (form for w in words for form in (w, w + "s")), start=1)]  # fmt: skip
    compiled = ChannelFilter(entries)
    compiled.allowed += [(compile_pattern(p), "raw") for p in ALLOW]
    return compiled


FILTERS: dict[str, ChannelFilter] = {level: _build(level) for level in LEVELS}


def slur_filter(level: str) -> ChannelFilter:
    """The filter for a channel's level. Anything unknown gets `base`, never nothing."""
    return FILTERS.get(level, FILTERS["base"])
