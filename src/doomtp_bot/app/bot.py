"""The whole bot in one process (ADR-0004): every service built, wired, served and shut down.

`Bot.build` makes the pieces in dependency order, one stage per method, and `Bot.serve` runs the web API
until uvicorn returns, then stops everything in reverse. The Twitch client's start, restart and
broadcaster handling are methods here too, because they reach most of the other pieces.
`tests/test_main.py` covers the wiring, the start and the shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog
import uvicorn
from fastapi import FastAPI
from vex_platform.jobs import JobRuntime, Registry

from doomtp_bot import __version__
from doomtp_bot.api.app import create_app
from doomtp_bot.api.keys import ApiKeyService
from doomtp_bot.app.dispatch import Dispatcher
from doomtp_bot.audit.log import TABLE as AUDIT_TABLE
from doomtp_bot.audit.log import copy_legacy_audit
from doomtp_bot.channels.capabilities import MODERATE, CapabilityProbe, granted_by
from doomtp_bot.channels.manager import ChannelManager
from doomtp_bot.chatlog.queries import latest_badges
from doomtp_bot.chatlog.writer import ChatLogWriter
from doomtp_bot.config import Settings
from doomtp_bot.core.bot_events import BotEvents, BotRuns, LastRun
from doomtp_bot.core.events import Event
from doomtp_bot.core.health import ComponentHealth, HealthRegistry, Status
from doomtp_bot.core.links import BotBadges
from doomtp_bot.core.outbox import Outbox, SendResult
from doomtp_bot.core.schedule import NextStreams
from doomtp_bot.core.streams import StreamPoller, StreamStatus
from doomtp_bot.customcmds.packs import PackService
from doomtp_bot.customcmds.resolution import CustomCommandLoader, SystemResolver
from doomtp_bot.customcmds.service import CustomCommandService
from doomtp_bot.customcmds.system import CoreNotInstalled, require_core
from doomtp_bot.filters.service import FilterService
from doomtp_bot.history.backfill import BackfillService
from doomtp_bot.history.enrich import Enricher, TwitchCdn
from doomtp_bot.history.jobs import BackfillJobs
from doomtp_bot.history.jobs import register as register_backfill
from doomtp_bot.history.provider import IvrLogsProvider
from doomtp_bot.moderation.automod import AutoMod
from doomtp_bot.moderation.index import ModerationIndex
from doomtp_bot.modules import builtin_registry
from doomtp_bot.modules.services import BotServices
from doomtp_bot.policy.repository import Actor
from doomtp_bot.policy.roles import MODERATOR_RANK
from doomtp_bot.policy.service import PolicyService
from doomtp_bot.runtime.engine import Runtime
from doomtp_bot.runtime.explain import ReportStore
from doomtp_bot.runtime.registry import CommandRegistry
from doomtp_bot.runtime.resolver import BuiltinResolver
from doomtp_bot.storage.db import Databases, schema_revision
from doomtp_bot.triggers.runner import TriggerRunner
from doomtp_bot.triggers.service import TriggerService
from doomtp_bot.triggers.timers import ChatActivity, TimerScheduler
from doomtp_bot.twitch.auth import AuthorizedAccount, TwitchAuth, TwitchOAuthHttp
from doomtp_bot.twitch.client import TwitchService
from doomtp_bot.twitch.signin import TwitchSignIn, TwitchSignInHttp, VexAuthHttp, VexSignIn
from doomtp_bot.twitch.tokens import StoredToken, TokenStore, broadcaster_identity
from doomtp_bot.variables.access import VariableAccessPolicy
from doomtp_bot.variables.store import PostgresVariableStore
from doomtp_bot.watchers import default_watchers
from doomtp_bot.webfetch.fetcher import HttpFetcher
from doomtp_bot.webfetch.hosts import HostStore

log = structlog.get_logger("doomtp_bot")

# How long to wait before starting the Twitch client again after it stopped on its own (ADR-0001).
FIRST_RETRY_S = 5.0
MAX_RETRY_S = 300.0
# How long a stop waits for a running job: past the history service's request timeout (30s).
JOBS_SHUTDOWN_S = 60.0


class _NoSender:
    async def send_chat(self, channel_id: str, text: str, reply_to: str | None) -> SendResult:
        return SendResult(None, "not_connected")


class Bot:
    # storage and the state the commands change
    dbs: Databases
    health: HealthRegistry
    policy: PolicyService
    store: PostgresVariableStore
    access: VariableAccessPolicy
    content_filter: FilterService
    http_hosts: HostStore
    customcmds: CustomCommandService
    packs: PackService
    triggers: TriggerService
    # chat in and out
    history: IvrLogsProvider
    activity: ChatActivity
    writer: ChatLogWriter
    moderation: ModerationIndex
    twitch: TwitchService | None
    streams: StreamStatus
    channels: ChannelManager
    probe: CapabilityProbe
    explain_reports: ReportStore
    emote_cdn: TwitchCdn
    jobs: JobRuntime
    backfill_queue: BackfillJobs
    # commands
    services: BotServices
    registry: CommandRegistry
    system: SystemResolver
    runtime: Runtime
    bot_badges: BotBadges
    outbox: Outbox
    trigger_runner: TriggerRunner
    timers: TimerScheduler
    dispatcher: Dispatcher | None
    # the bot's own events and the Twitch client's life
    bot_runs: BotRuns
    last_run: LastRun | None
    bot_events: BotEvents
    poller: StreamPoller | None
    # the web side
    auth: TwitchAuth | None
    signin: TwitchSignIn | VexSignIn | None
    app: FastAPI

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dispatcher = None  # events that arrive before it exists are dropped (`sink`)
        self.retry_in = 0.0  # grows while Twitch keeps refusing, so an outage isn't met with a reconnect storm
        self.down_since: int | None = None  # when the bot last heard chat before Twitch dropped it
        self.restart_cause: str | None = None  # why the client was started again: the watchdog's finding, or the error

    @classmethod
    async def build(cls, settings: Settings) -> Bot:
        bot = cls(settings)
        await bot._open_storage()
        await bot._load_state()
        await bot._open_chat()
        bot._make_twitch()
        await bot._make_channels_and_jobs()
        await bot._make_runtime()
        bot._make_outbox()
        bot._make_dispatch()
        await bot._record_this_run()
        bot._make_sign_ins()
        bot._register_health()
        bot.app = bot._make_app()
        return bot

    # ── building, in order ─────────────────────────────────────────────────
    async def _open_storage(self) -> None:
        self.dbs = await Databases.open(self.settings.database_dsn())
        copied = await copy_legacy_audit(self.dbs.bot)  # what an image from before ADR-0027 wrote
        if copied:
            log.info("audit.legacy_copied", rows=copied)
        self.health = HealthRegistry()

    async def _load_state(self) -> None:
        """Policy, variables, the filter, the hosts `http get` may fetch, custom commands and packs."""
        dbs = self.dbs
        self.policy = policy = PolicyService(dbs.bot, bot_owner_ids=self.settings.bot_owner_ids)
        await policy.reload()
        self.store = PostgresVariableStore(dbs.bot)
        self.access = VariableAccessPolicy(policy, dbs.bot)
        await self.access.reload()
        self.content_filter = FilterService(
            dbs.bot,
            slur_level=lambda channel_id: (s := policy.channel_settings(channel_id)) and s.slur_filter or "base",
        )
        await self.content_filter.reload()
        self.http_hosts = HostStore(dbs.bot)  # the hosts `http get` may fetch (ADR-0020)
        await self.http_hosts.reload()
        self.customcmds = CustomCommandService(
            dbs.bot, on_grants_changed=self.access.reload, filters=self.content_filter
        )
        self.packs = PackService(dbs.bot, self.customcmds)
        try:
            if not await require_core(self.packs):
                log.warning(
                    "bot.core_pending",
                    detail="the core pack can't be installed until the bot is signed in: sign it in at"
                    " /auth/login, run scripts/starter_pack.py and restart",
                )
        except CoreNotInstalled:
            await dbs.close()
            raise

    async def _open_chat(self) -> None:
        """Triggers, the chat log and the moderation index."""
        self.history = IvrLogsProvider(self.settings.ivr_logs_url)
        self.triggers = TriggerService(self.dbs.bot, filters=self.content_filter, scope=self.policy)
        await self.triggers.reload()
        self.packs.on_published = self.triggers.reload  # a pack's triggers follow where it is published (ADR-0029)
        self.activity = ChatActivity()
        self.writer = ChatLogWriter(self.dbs.chatlog)
        stale = await self.writer.close_stale_sessions()
        if stale:
            log.warning("chatlog.unclean_shutdown_detected", sessions_closed=stale)
        self.writer.start()
        self.moderation = ModerationIndex()

    def _make_twitch(self) -> None:
        settings, secret = self.settings, self.settings.client_secret()
        self.twitch = None
        if settings.twitch_client_id and secret:
            self.twitch = TwitchService(
                client_id=settings.twitch_client_id,
                client_secret=secret,
                tokens=TokenStore(self.dbs.bot),
                sink=self.sink,
                on_stopped=self.restart_twitch,
                expected_bot_id=settings.twitch_bot_id,
            )

    async def _make_channels_and_jobs(self) -> None:
        """Channels, the capability probe, and backfill as jobs (ADR-0024 §5, ADR-0027)."""
        settings, twitch = self.settings, self.twitch
        self.streams = StreamStatus()
        self.channels = ChannelManager(self.policy, twitch, self.writer, default_prefix=settings.default_prefix)
        self.probe = CapabilityProbe(policy=self.policy, channels=self.channels, prober=twitch)
        # `!explain` links its full report only where chat can open it (architecture §4.4).
        self.explain_reports = ReportStore(settings.public_base_url if settings.public_web_ui else None)
        self.channels.on_joined = self.probe.probe  # a channel is probed as soon as its subscriptions are up
        self.emote_cdn = TwitchCdn()
        enricher = Enricher(self.dbs.chatlog, twitch, self.emote_cdn)  # what backfilled lines lack (ADR-0024 §3)
        backfill = BackfillService(
            conn=self.dbs.chatlog, writer=self.writer, provider=self.history, policy=self.policy, enricher=enricher
        )
        job_registry = Registry()
        register_backfill(job_registry, backfill)
        # Jobs keep their own pool, on the `jobs` schema (ADR-0027); a backfill still writes through `chatlog`.
        self.jobs = JobRuntime(
            job_registry,
            settings.database_dsn(),
            audit_table=AUDIT_TABLE,
            concurrency=1,
            max_concurrency=1,
            worker_name="doomtp-bot",
            shutdown_timeout=JOBS_SHUTDOWN_S,
        )
        await self.jobs.open()
        self.backfill_queue = BackfillJobs(backfill, self.jobs)  # `!backfill` and the API queue jobs
        await self.backfill_queue.adopt_legacy()  # what an image from before ADR-0027 left queued

    async def _make_runtime(self) -> None:
        """The services the commands reach, the command registry and the runtime that runs them."""
        twitch = self.twitch
        self.services = services = BotServices(
            policy=self.policy,
            variable_store=self.store,
            channels=self.channels,
            customcmds=self.customcmds,
            packs=self.packs,
            filters=self.content_filter,
            triggers=self.triggers,
            variable_access=self.access,
            history=self.history,  # the backfill command names the service before anything is sent to it
            backfill=self.backfill_queue,
            explain_reports=self.explain_reports,
            site_url=self.settings.web_site_url,  # `!help` links the channel's command page there
            chatlog_db=self.dbs.chatlog,  # logsearch reads the log through chatlog/queries.py
            chatlog_writer=self.writer,  # `http` logs each request it makes (ADR-0020)
            http_hosts=self.http_hosts,
            http=HttpFetcher(self.http_hosts),  # fetches nothing until an admin allows a host
        )
        if twitch is not None:
            services.twitch, services.login_for, services.schedule = twitch, twitch.login_for, NextStreams(twitch)
        self.registry = registry = builtin_registry()
        # The system packs' members are sentinels, loaded once: the pack script's changes need a restart.
        self.system = system = await SystemResolver.load(BuiltinResolver(registry), self.packs)
        self.runtime = Runtime(
            registry,
            policy=self.policy,
            resolver=system,
            callbacks=self.policy,
            store=self.store,
            access=self.access,
            resolve_user=twitch.resolve_user if twitch else None,
            custom=CustomCommandLoader(self.customcmds, self.packs),
            services=services,
        )
        self.triggers.parser_params = self.runtime.parser_params  # expressions are checked the way they will run
        # What a pack's own trigger may call besides its pack's commands: what runs in every channel (ADR-0032).
        self.triggers.everywhere = lambda name: registry.get(name) is not None or name in system.public

    def _make_outbox(self) -> None:
        self.bot_badges = BotBadges()
        policy = self.policy
        self.outbox = Outbox(
            self.twitch or _NoSender(),
            self.writer,
            rate_for=self.rate_for,
            hold_ms_for=self.hold_ms_for,
            content_filter=self.content_filter.apply,
            links_allowed_for=self.links_allowed_for,
            enabled_for=lambda channel_id: (s := policy.channel_settings(channel_id)) is None or s.bot_enabled,
            on_banned=self.channels.leave_banned,  # a 403 on a send means banned there (architecture §10)
        )
        self.services.outbox = self.outbox  # `mecho` sends its lines itself, one message each

    def _make_dispatch(self) -> None:
        """Triggers, timers and the dispatcher every event goes through."""
        self.trigger_runner = TriggerRunner(
            runtime=self.runtime, policy=self.policy, outbox=self.outbox, streams=self.streams, writer=self.writer
        )
        self.timers = TimerScheduler(
            triggers=self.triggers,
            runner=self.trigger_runner,
            policy=self.policy,
            activity=self.activity,
            streams=self.streams,
        )
        twitch = self.twitch
        self.dispatcher = Dispatcher(
            runtime=self.runtime,
            policy=self.policy,
            writer=self.writer,
            outbox=self.outbox,
            moderation=self.moderation,
            channels=self.channels,
            triggers=self.triggers,
            trigger_runner=self.trigger_runner,
            activity=self.activity,
            streams=self.streams,
            automod=AutoMod(policy=self.policy, filters=self.content_filter, moderator=twitch) if twitch else None,
            customcmds=self.customcmds,
            bot_badges=self.bot_badges,
            watchers=default_watchers(),
        )

    async def _record_this_run(self) -> None:
        """The bot's own events as triggers (ADR-0034): this start is recorded first, so the next one can
        tell how long the bot was down and what version it was."""
        assert self.dispatcher is not None
        self.bot_runs = BotRuns(self.dbs.chatlog)
        self.last_run = await self.bot_runs.begin()
        self.bot_runs.start()
        channels = self.channels
        self.bot_events = BotEvents(
            self.dispatcher.bot_event, lambda: [c.channel_id for c in channels.active_channels()]
        )
        self.probe.on_moderator = self.bot_events.moderator_changed
        channels.on_join = self.bot_events.joined
        self.poller = (
            StreamPoller(source=self.twitch, channels=channels, status=self.streams, sink=self.dispatcher.handle)
            if self.twitch is not None
            else None
        )

    def _make_sign_ins(self) -> None:
        """The bot's own Twitch sign-in and broadcasters' (ADR-0007), and people's for the web admin."""
        settings, secret = self.settings, self.settings.client_secret()
        self.auth = None
        if self.twitch is not None and settings.twitch_client_id and secret:
            self.auth = TwitchAuth(
                client_id=settings.twitch_client_id,
                redirect_uri=settings.public_base_url.rstrip("/") + "/auth/callback",
                tokens=self.twitch.tokens,
                http=TwitchOAuthHttp(settings.twitch_client_id, secret),
                on_bot_authorized=self.on_bot_authorized,
                on_broadcaster_authorized=self.on_broadcaster_authorized,
                expected_bot_id=settings.twitch_bot_id,
            )
            # `!join` links here: connecting is how a broadcaster adds the bot (ADR-0007).
            self.services.connect_url = settings.public_base_url.rstrip("/") + "/auth/connect"
        # People signing in to the web admin (ADR-0017). Needs only the app's credentials, not the bot's token;
        # or, with SIGNIN_PROVIDER=vex, this bot's client registration in vex-auth (ADR-0023).
        self.signin = None
        admin_callback = settings.public_base_url.rstrip("/") + "/auth/admin/callback"
        if settings.signin_provider == "vex":
            auth_secret = settings.vex_auth_secret()
            if settings.vex_auth_url and auth_secret:
                self.signin = VexSignIn(
                    auth_url=settings.vex_auth_url,
                    client_id=settings.vex_auth_client_id,
                    redirect_uri=admin_callback,
                    http=VexAuthHttp(
                        settings.vex_auth_internal_url or settings.vex_auth_url,
                        settings.vex_auth_client_id,
                        auth_secret,
                    ),
                    policy=self.policy,
                )
            else:
                log.warning("signin.not_configured", provider="vex", missing="VEX_AUTH_URL or its secret")
        elif settings.twitch_client_id and secret:
            self.signin = TwitchSignIn(
                client_id=settings.twitch_client_id,
                redirect_uri=admin_callback,
                http=TwitchSignInHttp(settings.twitch_client_id, secret),
                policy=self.policy,
            )

    def _register_health(self) -> None:
        self.health.register("databases", self.db_check)
        self.health.register("chatlog", self.chatlog_check)
        self.health.register("twitch", self.twitch_check)

    def _make_app(self) -> FastAPI:
        settings = self.settings
        return create_app(
            self.health,
            self.auth,
            runtime=self.runtime,
            policy=self.policy,
            services={
                "customcmds": self.customcmds,
                "packs": self.packs,
                "triggers": self.triggers,
                "filters": self.content_filter,
                "health": self.health,
                "channels": self.channels,
                "twitch": self.twitch,
                "variable_access": self.access,
                "capabilities": self.probe,
                "variable_store": self.store,
                "http_hosts": self.http_hosts,
                "bot_db": self.dbs.bot,
                "chatlog_db": self.dbs.chatlog,
                "api_keys": ApiKeyService(self.dbs.bot),
                "streams": self.streams,  # {channel.live} and friends in /explain runs
                "backfill": self.backfill_queue,
                "explain_reports": self.explain_reports,
                "twitch_signin": self.signin,
            },
            admin_password=settings.admin_password_value(),
            admin_password_networks=settings.admin_password_networks,
            jobs=self.jobs,
            user_lookup_origins=settings.user_lookup_origins,
            secure_cookies=settings.secure_cookies,
        )

    # ── running ────────────────────────────────────────────────────────────
    async def serve(self) -> None:
        """Serve the API until uvicorn returns (it owns SIGINT/SIGTERM), then shut everything down."""
        settings = self.settings
        server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=settings.web_host,
                port=settings.web_port,
                log_config=None,
                lifespan="on",
                # Client addresses (the login limiter's key) and https come from X-Forwarded-* only when the
                # request arrives from one of these proxies.
                proxy_headers=True,
                forwarded_allow_ips=settings.web_forwarded_allow_ips,
            )
        )
        log.info("bot.start", version=__version__, api=f"http://{settings.web_host}:{settings.web_port}")
        self.timers.start()
        twitch_start = asyncio.create_task(self.start_twitch())
        try:
            await server.serve()
        finally:
            await self._shut_down(twitch_start)

    async def _shut_down(self, twitch_start: asyncio.Task[None]) -> None:
        await self.timers.stop()
        await self.probe.stop()
        if self.poller is not None:
            await self.poller.stop()
        await self.jobs.close()  # a running backfill stops before its next request, and resumes at the next start
        await self.history.close()
        await self.emote_cdn.close()
        twitch_start.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await twitch_start
        if self.dispatcher is not None:
            await self.dispatcher.drain()
        await self.writer.end_all_sessions("shutdown")
        await self.bot_runs.end()
        if self.twitch is not None:
            await self.twitch.stop()
        await self.writer.stop()
        await self.dbs.close()
        log.info("bot.stop")

    async def sink(self, event: Event) -> None:
        """Where the Twitch client hands every event."""
        if self.dispatcher is not None:
            await self.dispatcher.handle(event)

    # ── what the outbox asks per channel ───────────────────────────────────
    def rate_for(self, channel_id: str) -> tuple[int, float]:
        """Moderator send limits when the channel tier or the bot's role there allows them."""
        settings = self.policy.channel_settings(channel_id)
        elevated = settings is not None and settings.tier in ("moderator", "full")
        twitch = self.twitch
        if not elevated and twitch is not None and twitch.bot_id is not None:
            chatter = self.policy.build_chatter(channel_id, twitch.bot_id, twitch.bot_login or "")
            elevated = chatter.rank >= MODERATOR_RANK
        return (90, 30.0) if elevated else (20, 30.0)

    def links_allowed_for(self, channel_id: str) -> bool:
        """Links stay clickable where the bot is a moderator or VIP, or in its own channel (ADR-0019)."""
        twitch = self.twitch
        if twitch is None or twitch.bot_id is None:
            return False
        if channel_id == twitch.bot_id or self.bot_badges.may_link(channel_id):
            return True
        settings = self.policy.channel_settings(channel_id)
        if settings is not None and MODERATE in settings.capabilities:
            return True
        return self.policy.build_chatter(channel_id, twitch.bot_id, twitch.bot_login or "").rank >= MODERATOR_RANK

    def hold_ms_for(self, channel_id: str) -> int:
        settings = self.policy.channel_settings(channel_id)
        return settings.reply_hold_ms if settings else 0

    # ── health ─────────────────────────────────────────────────────────────
    async def db_check(self) -> ComponentHealth:
        return ComponentHealth(
            Status.OK,
            {
                "bot_schema": await schema_revision(self.dbs.bot),
                "chatlog_schema": await schema_revision(self.dbs.chatlog),
            },
        )

    async def chatlog_check(self) -> ComponentHealth:
        writer = self.writer
        status = Status.OK if writer.queue_depth < 5_000 else Status.DEGRADED
        return ComponentHealth(status, {"queue_depth": writer.queue_depth, "last_flush_ms": writer.last_flush_ms})

    async def twitch_check(self) -> ComponentHealth:
        if self.twitch is None:
            return ComponentHealth(Status.DISABLED, {"reason": "TWITCH_CLIENT_ID / secret not set"})
        return await self.twitch.health()

    # ── the Twitch client's life ───────────────────────────────────────────
    async def start_twitch(self) -> None:
        twitch = self.twitch
        if twitch is None:
            return
        try:
            if await twitch.start() and twitch.bot_id and twitch.bot_login:
                self.retry_in = 0.0
                await self.channels.ensure_home(twitch.bot_id, twitch.bot_login)
                self.bot_badges.load(await latest_badges(self.dbs.chatlog, twitch.bot_id))  # VIP status (ADR-0019)
                await self.channels.subscribe_all()
                await self.probe.probe_all()  # tier per channel, before anything checks a capability
                await self.restore_broadcasters()  # full-tier events, where a broadcaster connected
                await self.bot_events.online(  # `bot_online` (ADR-0034)
                    self.last_run, down_since_ms=self.down_since, cause=self.restart_cause
                )
                self.probe.start()
                if self.poller is not None:
                    await self.poller.poll()
                    self.poller.start()
                queued = await self.backfill_queue.queue_startup()  # gaps since the last run (ADR-0008)
                if queued:
                    log.info("history.startup_backfill", channels=len(queued))
                if not self.jobs.running:
                    await self.jobs.start()  # first queues again what the last stop cut short
        except Exception:
            log.exception("twitch.start_failed")

    async def restart_twitch(self) -> None:
        """The client stopped by itself: EventSub gave up, or the token went bad. Start it again, waiting
        longer each time (ADR-0001). Coming back re-runs the startup path, so subscriptions, capabilities
        and the backfill of whatever was missed while it was deaf are all done again (ADR-0008)."""
        self.retry_in = min(max(self.retry_in * 2, FIRST_RETRY_S), MAX_RETRY_S)
        twitch = self.twitch
        if twitch is not None:
            self.down_since = twitch.heard_at
            self.restart_cause = twitch.last_error
        log.warning("twitch.restarting", in_s=self.retry_in, cause=self.restart_cause)
        # The log stopped when the bot last heard chat, not now: ending the sessions there leaves a gap
        # that the startup backfill fills once the client is back.
        await self.writer.end_all_sessions("restart", at=twitch.heard_at if twitch is not None else None)
        await asyncio.sleep(self.retry_in)
        await self.start_twitch()

    async def restore_broadcasters(self) -> None:
        """Give the client the broadcaster tokens we hold, and re-subscribe their events (ADR-0007)."""
        twitch = self.twitch
        if twitch is None:
            return
        for stored in await twitch.tokens.broadcasters():
            if stored.user_id == twitch.bot_id:  # granted by the bot's own sign-in, which has no callback of its own
                await self.probe.grant(stored.user_id, granted_by(stored.scopes))
            settings_of = self.policy.channel_settings(stored.user_id)
            capabilities = set(settings_of.capabilities) if settings_of else set()
            await self.use_broadcaster(stored.user_id, stored.login, stored, capabilities)

    async def use_broadcaster(
        self, channel_id: str, login: str, stored: StoredToken | None, capabilities: set[str]
    ) -> None:
        """Subscribe to a channel's own events with the broadcaster's token, and notice when it's gone."""
        twitch = self.twitch
        if twitch is None:
            return
        if stored is None or not stored.refresh_token:
            await self.forget_broadcaster(channel_id, login, "no usable token is stored")
            return
        if not await twitch.use_broadcaster_token(stored.user_id, stored.access_token, stored.refresh_token):
            await self.forget_broadcaster(channel_id, login, "Twitch would not take the token")
            return
        events = await twitch.subscribe_broadcaster(channel_id, capabilities)
        if events.unauthorized:
            await self.forget_broadcaster(channel_id, login, "Twitch refused the grant")
            return
        log.info("twitch.broadcaster_ready", channel=login, has=sorted(capabilities), failed=events.failed)

    async def forget_broadcaster(self, channel_id: str, login: str, why: str) -> None:
        """The grant is gone — taken back on Twitch, or never usable. Fall back to what the bot earned
        by itself, and drop the token rather than keep asking with it (ADR-0007 item 5)."""
        if self.twitch is None:
            return
        await self.twitch.tokens.forget(broadcaster_identity(channel_id))
        kept = await self.probe.revoke_full(channel_id)
        log.warning("twitch.broadcaster_grant_gone", channel=login, why=why, has=sorted(kept))

    async def on_broadcaster_authorized(self, account: AuthorizedAccount) -> None:
        """A broadcaster connected their channel: join it, grant what they gave, subscribe (ADR-0007)."""
        twitch = self.twitch
        if twitch is None:
            return
        # The broadcaster asked for the bot themselves: a deliberate rejoin, even after a ban.
        await self.channels.join(account.user_id, account.login, Actor(None, "web"), rejoin=True)
        capabilities = await self.probe.grant(account.user_id, granted_by(account.scopes))
        stored = await twitch.tokens.get(broadcaster_identity(account.user_id))
        await self.use_broadcaster(account.user_id, account.login, stored, set(capabilities))

    async def on_bot_authorized(self, account: AuthorizedAccount) -> None:
        log.info("twitch.authorized", bot=account.login, scopes=list(account.scopes))
        asyncio.create_task(self.start_twitch())  # noqa: RUF006 - fire and forget; errors are logged inside
