"""`format` and the formatting accessors (ADR-0037 G11)."""

from datetime import UTC, datetime

import pytest

from doomtp_bot.runtime import formats
from doomtp_bot.runtime.ops import ExprError
from doomtp_bot.runtime.result import Code, ErrorCode
from tests.runtime.helpers import make_runtime, run

MOMENT = 1761255000  # 2025-10-23 21:30:00 UTC, a Thursday


def test_time_uses_moment_tokens_in_the_channel_timezone() -> None:
    assert formats.time(MOMENT, "DD MMM HH:mm", "UTC") == "23 Oct 21:30"
    assert formats.time(MOMENT, "dddd D MMMM YYYY, hh:mm A [at] YY", "Europe/Lisbon") == (
        "Thursday 23 October 2025, 10:30 PM at 25"
    )
    assert formats.time("2025-10-23T21:30:00Z", None, "UTC") == "2025-10-23 21:30"
    assert formats.time(MOMENT, None, "Not/AZone") == "2025-10-23 21:30"  # an unknown zone is UTC


def test_numbers() -> None:
    assert formats.number(1234567.891, "2") == "1,234,567.89"
    assert formats.number(1234567) == "1,234,567"
    assert formats.number("-1234.5") == "-1,234.5"
    assert formats.percent(0.4567, "1") == "45.7%"
    assert formats.duration(90061) == "1d 1h 1m 1s"
    assert [formats.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 111)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "111th",
    ]  # fmt: skip


def test_ago() -> None:
    now = datetime.fromtimestamp(MOMENT, tz=UTC)
    assert formats.ago(MOMENT - 3725, "UTC", now) == "1h 2m ago"
    assert formats.ago(MOMENT + 60, "UTC", now) == "in 1m"


@pytest.mark.parametrize(("kind", "value"), [("number", "lots"), ("time", "tuesday"), ("ordinal", 1.5)])
def test_a_value_of_the_wrong_kind_is_a_type_error(kind: str, value: object) -> None:
    with pytest.raises(ExprError) as caught:
        formats.apply(kind, value, None, "UTC", datetime.now(tz=UTC))
    assert caught.value.code == ErrorCode.E_TYPE


async def test_the_command_and_the_accessors_agree() -> None:
    r = await run(make_runtime(), "!format number 1234567.891 2")
    assert (r.send, r.result.data) == ("1,234,567.89", "1,234,567.89")
    r = await run(make_runtime(), f"!echo {{{MOMENT}:time(DD MMM HH:mm)}}, {{0.5:percent}}, {{22:ordinal}}")
    assert r.send == "23 Oct 21:30, 50%, 22nd"
    assert (await run(make_runtime(), "!format number lots")).result.code == ErrorCode.E_TYPE
    assert (await run(make_runtime(), "!format colour 1")).result.code == Code.USAGE


async def test_duration_stays_the_cast_and_human_formats_it() -> None:
    """`:duration` reads `1h30m` as seconds (the cast); `:human` is a duration's formatter."""
    r = await run(make_runtime(), "!echo 1h30m | echo {_:duration} {5400:human}")
    assert r.send == "5400 1h 30m"


async def test_bot_uptime_duration_formats_through_echo() -> None:
    """`{$bot.uptime:duration}` formats like `format duration`, with `/me` and the emoji intact."""
    r = await run(make_runtime(), "!echo /me ❤️‍🩹 {$bot.uptime:duration}")
    assert r.send is not None and r.send.startswith("/me ❤️‍🩹 ") and r.send.endswith("s")
    assert not r.send.rsplit(" ", 1)[1].isdigit()


@pytest.mark.parametrize("kind", formats.KINDS)
@pytest.mark.parametrize(
    ("value", "option"),
    [
        (MOMENT, "DD MMM HH:mm"),
        (MOMENT, None),
        (0.4567, "1"),
        (64801, None),
        (90061, "2"),
        (22, None),
    ],
)
async def test_every_accessor_matches_the_format_command(kind: str, value: float, option: str | None) -> None:
    """`{x:kind(option)}` and `format kind x option` say the same, or fail the same way (ADR-0037 amendment)."""
    if kind not in ("time", "number", "percent"):
        option = None  # only these take an option; the parser rejects `:ago(x)`, the command ignores it
    suffix = f"({option})" if option is not None else ""
    pipe = await run(make_runtime(), f"!echo {{{value}:{kind}{suffix}}}")
    command = await run(make_runtime(), f"!format {kind} {value} {option or ''}".strip())
    assert pipe.result.code == command.result.code
    if command.result.code == Code.OK:
        assert pipe.send == command.send


async def test_format_duration_reads_duration_text() -> None:
    """`format duration 1h30m` reads the text as the `:duration` cast does (ADR-0037 amendment)."""
    assert (await run(make_runtime(), "!format duration 1h30m")).send == "1h 30m"
    assert (await run(make_runtime(), "!format duration 2h")).send == "2h"
    assert (await run(make_runtime(), "!format duration soon")).result.code == ErrorCode.E_TYPE
