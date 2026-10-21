"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from vex_platform.jobs import JobRuntime

from doomtp_bot import __version__
from doomtp_bot.api import v2
from doomtp_bot.api.routes import (
    auth,
    backfill,
    bot,
    commands,
    data,
    filters,
    health,
    http_hosts,
    language,
    log,
    manage,
    session,
    site,
    triggers,
    variables,
)
from doomtp_bot.api.sessions import AdminAuth, LoginLimiter, ReadLimiter, parse_networks
from doomtp_bot.api.v2_users import parse_origins
from doomtp_bot.config import LOCAL_NETWORKS
from doomtp_bot.core.health import HealthRegistry
from doomtp_bot.twitch.auth import TwitchAuth

# The editor bundle and the railroad diagrams, which the web site (doomtp-web) loads from here (ADR-0016).
STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    health_registry: HealthRegistry,
    twitch_auth: TwitchAuth | None = None,
    *,
    runtime: Any = None,
    policy: Any = None,
    services: dict[str, Any] | None = None,
    admin_password: str | None = None,
    admin_password_networks: str = LOCAL_NETWORKS,
    jobs: JobRuntime | None = None,
    user_lookup_origins: str = "",
    secure_cookies: bool = False,
) -> FastAPI:
    """With `jobs`, the job runtime, the app also serves /api/v2 (ADR-0027). `user_lookup_origins` is
    USER_LOOKUP_ORIGINS: the sites on other origins that may read /api/v2/twitch/users (ADR-0035).
    `secure_cookies` marks every cookie `Secure` whatever the request's scheme (PUBLIC_BASE_URL is https)."""
    app = FastAPI(title="doomtp-bot", version=__version__, docs_url="/docs", redoc_url=None)
    app.state.health = health_registry
    app.state.twitch_auth = twitch_auth
    app.state.runtime = runtime  # the language API parses and explains with the live registry
    app.state.policy = policy
    app.state.secure_cookies = secure_cookies
    app.state.admin_auth = AdminAuth(password=admin_password)
    app.state.admin_password_networks = parse_networks(admin_password_networks)
    app.state.login_limiter = LoginLimiter()  # failed JSON logins, per client address
    app.state.public_read_limiter = ReadLimiter()  # public chat log reads, per client address (ADR-0026)
    app.state.user_lookup_origins = parse_origins(user_lookup_origins)
    for name, service in (services or {}).items():  # customcmds, packs, triggers, filters
        setattr(app.state, name, service)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(language.router)
    # /api/v1, one router per resource; no two of their routes can match the same request
    for module in (data, backfill, filters, triggers, variables, http_hosts, log):
        app.include_router(module.router)
    app.include_router(manage.router)
    app.include_router(commands.router)
    app.include_router(bot.router)
    app.include_router(session.router)
    app.include_router(site.router)
    if jobs is not None:
        v2.mount(app, jobs)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
