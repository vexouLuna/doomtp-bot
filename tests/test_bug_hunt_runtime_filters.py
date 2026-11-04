"""Regressions for the runtime, filter, trigger and script findings of the bug hunt."""

from __future__ import annotations

from pathlib import Path

import pytest

from doomtp_bot.filters.normalize import normalize
from doomtp_bot.ints import ascii_int
from doomtp_bot.runtime.formats import number
from doomtp_bot.triggers.cron import CronError, parse_cron
from scripts.backup import _without_password, rotate


@pytest.mark.parametrize("text", ["²", "٣", "", "x", "1" * 19, "+5", "-5"])
def test_ascii_int_refuses_what_isdigit_would_let_through(text: str) -> None:
    assert ascii_int(text) is None


def test_ascii_int_reads_plain_digits_and_signed_on_request() -> None:
    assert ascii_int("42") == 42
    assert ascii_int("-5", signed=True) == -5


def test_a_collapsed_run_maps_back_to_its_last_repeat() -> None:
    folded = normalize("niiiice")
    assert folded.text == "nice"
    assert folded.span(0, 4) == (0, 7)


def test_number_keeps_the_sign_of_a_negative_fraction() -> None:
    assert number(-0.5) == "-0.5"
    assert number(-1234.5) == "-1,234.5"


def test_cron_rejects_a_huge_range_without_building_it() -> None:
    with pytest.raises(CronError):
        parse_cron("0-999999999999 * * * *")


def test_cron_weekday_seven_is_sunday_inside_a_range() -> None:
    assert parse_cron("0 0 * * 5-7").weekday >= {5, 6, 0}


def test_cron_step_wildcard_day_is_unrestricted() -> None:
    cron = parse_cron("0 0 */2 * *")
    assert cron.restricted_day is False


@pytest.mark.parametrize("field", ["*/²", "²", "٣"])
def test_cron_rejects_non_ascii_digits(field: str) -> None:
    with pytest.raises(CronError):
        parse_cron(f"{field} * * * *")


def test_backup_password_moves_out_of_the_dsn() -> None:
    dsn, password = _without_password("postgresql://bot:s%40cret@db:5432/doomtp")
    assert "s%40cret" not in dsn
    assert "cret" not in dsn
    assert password == "s@cret"
    assert _without_password("postgresql://bot@db/doomtp") == ("postgresql://bot@db/doomtp", None)


def test_rotation_never_deletes_the_newest_even_with_keep_zero(tmp_path: Path) -> None:
    for stamp in ("20260101T000000Z", "20260102T000000Z"):
        (tmp_path / f"bot-{stamp}.dump").write_bytes(b"x")
    rotate(tmp_path, "bot", keep=0)
    assert [p.name for p in tmp_path.iterdir()] == ["bot-20260102T000000Z.dump"]
