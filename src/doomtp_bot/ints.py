"""Integers from chat text, where `str.isdigit()` and `int()` disagree (`"²"`, `"٣"`, 5000 digits)."""

from __future__ import annotations

import re

_INT = re.compile(r"[+-]?[0-9]{1,18}")


def ascii_int(text: str, *, signed: bool = False) -> int | None:
    """The int `text` spells in ASCII digits (at most 18, with an optional sign when `signed`), else None."""
    if _INT.fullmatch(text) is None or (not signed and not text[0].isdigit()):
        return None
    return int(text)
