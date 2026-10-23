"""Entry point: settings, logging and the instance lock, then the bot itself (`app/bot.py`, ADR-0004)."""

from __future__ import annotations

import asyncio
import sys

import structlog

from doomtp_bot.app.bot import Bot
from doomtp_bot.config import Settings
from doomtp_bot.core.instance_lock import InstanceLock, InstanceLockError
from doomtp_bot.customcmds.system import CoreNotInstalled
from doomtp_bot.log import configure_logging
from doomtp_bot.storage.db import configure_event_loop
from doomtp_bot.storage.schema import SchemaMismatch

log = structlog.get_logger("doomtp_bot")


async def run(settings: Settings) -> None:
    bot = await Bot.build(settings)
    await bot.serve()


def main() -> None:
    if sys.argv[1:2] == ["db"]:
        # Migrations (ADR-0022). No instance lock: the migrate step runs while the old bot is still up.
        from doomtp_bot.storage.schema import cli

        sys.exit(cli(sys.argv[2:]))
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    if settings.forwarding_undoes_password_networks():
        # Any client could claim an address in ADMIN_PASSWORD_NETWORKS through X-Forwarded-For.
        log.error(
            "bot.forwarded_allow_ips_open",
            detail="WEB_FORWARDED_ALLOW_IPS is * while ADMIN_PASSWORD is limited to ADMIN_PASSWORD_NETWORKS;"
            " name the reverse proxy's address instead",
        )
        sys.exit(1)
    lock = InstanceLock(settings.lock_path)
    try:
        lock.acquire()
    except InstanceLockError as exc:
        log.error("bot.already_running", detail=str(exc))
        sys.exit(1)
    try:
        configure_event_loop()
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except CoreNotInstalled as exc:
        log.error("bot.core_not_installed", detail=str(exc))
        sys.exit(1)
    except SchemaMismatch as exc:
        log.error("bot.schema_mismatch", detail=str(exc))
        sys.exit(1)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
