"""The services a run carries (`ExecContext.services`).

The runtime reads only these few itself. The built-in commands need far more (policy, custom commands,
variables, triggers...), but those packages import the runtime, so the runtime can't name them without
the two importing each other (tests/test_import_graph.py). `modules/services.py` extends this class with
them instead, and the bot builds that one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doomtp_bot.core.schedule import NextStreams
    from doomtp_bot.runtime.engine import Runtime
    from doomtp_bot.runtime.registry import CommandRegistry


@dataclass
class Services:
    runtime: Runtime | None = None  # set by the Runtime that carries them
    registry: CommandRegistry | None = None  # likewise
    schedule: NextStreams | None = None  # `$channel.next_stream`, from Twitch's schedule
