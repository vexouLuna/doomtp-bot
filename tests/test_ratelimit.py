"""The sliding window behind the login and read limiters and `http get`: it must forget old keys, or a
client that rotates addresses grows the process without bound."""

from __future__ import annotations

from doomtp_bot.api.sessions import AdminAuth, LoginLimiter, ReadLimiter
from doomtp_bot.ratelimit import SlidingWindow


def test_the_window_counts_only_recent_events() -> None:
    now = [0.0]
    window = SlidingWindow(60, clock=lambda: now[0])
    window.add("a")
    now[0] = 30
    window.add("a")
    assert window.count("a") == 2 and window.oldest("a") == 0 and window.count("b") == 0
    now[0] = 60
    assert window.count("a") == 1 and window.oldest("a") == 30
    window.clear("a")
    assert window.count("a") == 0 and window.oldest("a") is None


def test_the_window_forgets_keys_whose_events_aged_out() -> None:
    now = [0.0]
    window = SlidingWindow(60, clock=lambda: now[0])
    for i in range(1000):
        window.add(f"2001:db8::{i:x}")
    assert len(window) == 1000
    now[0] = 3600
    window.add("2001:db8::ffff")
    assert len(window) == 1


def test_the_limiters_and_sessions_stay_bounded() -> None:
    now = [0.0]
    reads, logins = ReadLimiter(clock=lambda: now[0]), LoginLimiter(clock=lambda: now[0])
    auth = AdminAuth(password="dummy-password", clock=lambda: now[0])
    for i in range(1000):
        reads.hit(f"2001:db8::{i:x}")
        logins.failed(f"2001:db8::1:{i:x}")
        auth.login()
    now[0] = 24 * 3600
    reads.hit("2001:db8::ffff")
    logins.failed("2001:db8::2:1")
    auth.login()
    assert len(reads._hits) == 1 and len(logins._failures) == 1  # noqa: SLF001 - the point of the test
    assert len(auth._sessions) == 1  # noqa: SLF001


def test_a_read_over_the_limit_is_not_counted() -> None:
    now = [0.0]
    reads = ReadLimiter(reads=2, window_s=60, clock=lambda: now[0])
    assert reads.hit("a") is None and reads.hit("a") is None
    now[0] = 10
    assert reads.hit("a") == 50
    now[0] = 60
    assert reads.hit("a") is None  # the first read aged out; the refused one never counted
