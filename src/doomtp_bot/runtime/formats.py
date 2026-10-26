"""Formatting numbers and times (ADR-0037 G11): the `format` command and the `:time`, `:ago`, `:number`,
`:percent` and `:ordinal` accessors share these. A value that isn't a number or a time is E_TYPE (232)."""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from doomtp_bot.runtime.ops import ExprError, as_number, human
from doomtp_bot.runtime.result import ErrorCode
from doomtp_bot.runtime.values import render, render_float

KINDS = ("time", "ago", "duration", "number", "percent", "ordinal")
DEFAULT_PATTERN = "YYYY-MM-DD HH:mm"

# Moment-style tokens, longest first; `[text]` is literal. Not strftime: `%` reads as modulo (ADR-0037).
_TOKENS = re.compile(r"\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|DD|D|dddd|ddd|HH|H|hh|mm|ss|A")
_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _type_error(kind: str, value: Any, wants: str) -> ExprError:
    return ExprError(ErrorCode.E_TYPE, f"{kind} needs {wants}, not {render(value) or 'nothing'}")


def _number(kind: str, value: Any) -> int | float:
    number = as_number(value)
    if number is None:
        raise _type_error(kind, value, "a number")
    return number


def _moment(kind: str, value: Any, zone: str) -> datetime:
    """Epoch seconds or ISO 8601 text, in the channel's timezone (UTC for an unknown one)."""
    number = as_number(value)
    try:
        if number is not None:
            moment = datetime.fromtimestamp(number, tz=UTC)
        elif isinstance(value, str):
            moment = datetime.fromisoformat(value.strip())
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
        else:
            raise ValueError
    except (ValueError, OverflowError, OSError):
        raise _type_error(kind, value, "epoch seconds or an ISO 8601 time") from None
    with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
        moment = moment.astimezone(ZoneInfo(zone))
    return moment


def _token(m: re.Match[str], t: datetime) -> str:
    match m.group(0):
        case "YYYY":
            return f"{t.year:04d}"
        case "YY":
            return f"{t.year % 100:02d}"
        case "MMMM":
            return _MONTHS[t.month - 1]
        case "MMM":
            return _MONTHS[t.month - 1][:3]
        case "MM":
            return f"{t.month:02d}"
        case "DD":
            return f"{t.day:02d}"
        case "D":
            return str(t.day)
        case "dddd":
            return _DAYS[t.weekday()]
        case "ddd":
            return _DAYS[t.weekday()][:3]
        case "HH":
            return f"{t.hour:02d}"
        case "H":
            return str(t.hour)
        case "hh":
            return f"{(t.hour % 12) or 12:02d}"
        case "mm":
            return f"{t.minute:02d}"
        case "ss":
            return f"{t.second:02d}"
        case "A":
            return "AM" if t.hour < 12 else "PM"
    return m.group(1)  # [literal]


def time(value: Any, pattern: str | None, zone: str) -> str:
    moment = _moment("time", value, zone)
    return _TOKENS.sub(lambda m: _token(m, moment), pattern or DEFAULT_PATTERN)


def ago(value: Any, zone: str, now: datetime) -> str:
    seconds = int((now - _moment("ago", value, zone)).total_seconds())
    return f"{human(seconds)} ago" if seconds >= 0 else f"in {human(-seconds)}"


def duration(value: Any) -> str:
    """Every unit, where `:human` keeps the two largest: 90061 → `1d 1h 1m 1s`."""
    seconds = _number("duration", value)
    if seconds < 0:
        raise _type_error("duration", value, "a number of seconds")
    rest, parts = int(seconds), list[str]()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if rest >= size or (unit == "s" and not parts):
            parts.append(f"{rest // size}{unit}")
            rest %= size
    return " ".join(parts)


def _decimals(kind: str, decimals: str | None) -> int | None:
    if decimals is None or decimals == "":
        return None
    if not decimals.isdigit() or int(decimals) > 10:
        raise ExprError(ErrorCode.E_TYPE, f"{kind} takes 0 to 10 decimals, not {decimals}")
    return int(decimals)


def number(value: Any, decimals: str | None = None) -> str:
    """Thousands separated by commas: 1234567.891 with 2 decimals → `1,234,567.89`."""
    x, places = _number("number", value), _decimals("number", decimals)
    if places is not None:
        return f"{x:,.{places}f}"
    whole, dot, fraction = (str(x) if isinstance(x, int) else render_float(x)).partition(".")
    return f"{int(whole):,}" + dot + fraction if whole.lstrip("-").isdigit() else whole + dot + fraction


def percent(value: Any, decimals: str | None = None) -> str:
    """A fraction as a percentage: 0.4567 with 1 decimal → `45.7%`."""
    x = _number("percent", value) * 100
    return f"{x:.{_decimals('percent', decimals) or 0}f}%"


def ordinal(value: Any) -> str:
    x = _number("ordinal", value)
    if x != int(x):
        raise _type_error("ordinal", value, "a whole number")
    n = int(x)
    suffix = "th" if 10 <= abs(n) % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(abs(n) % 10, "th")
    return f"{n}{suffix}"


def apply(kind: str, value: Any, option: str | None, zone: str, now: datetime) -> str:
    """One of KINDS. `option` is the pattern for `time` and the decimals for `number` and `percent`."""
    match kind:
        case "time":
            return time(value, option, zone)
        case "ago":
            return ago(value, zone, now)
        case "duration":
            return duration(value)
        case "number":
            return number(value, option)
        case "percent":
            return percent(value, option)
        case "ordinal":
            return ordinal(value)
    raise ValueError(kind)
