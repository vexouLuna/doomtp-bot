"""`/api/v1`: reading and changing what the bot knows (architecture §11).

Nothing here talks to the database on its own. Every read comes from the same snapshot the runtime uses,
and every write goes through the same service a chat command would call, so the audit log records it the
same way, only with the caller's `via`.

Three ways in:
  * **no auth** for what the public pages already show: a channel's commands and its publications;
  * **`Authorization: Bearer dtb_…`** with the `read` or `write` scope, for scripts and dashboards;
  * **the session cookie**, so the admin UI can use these endpoints. Cookies are sent by the
    browser whether or not the page meant to, so a cookie-authenticated *write* also needs the session's
    CSRF token in `X-CSRF-Token`. A key doesn't: it is never sent automatically.

A moderator's session reaches the `READ`/`WRITE` routes of its own channels only, and `BROADCASTER_*`
ones only in their own channel (or with a custom role as high); `ADMIN_*` routes are for admins
(`api/access.py`, ADR-0017, ADR-0026). A channel's chat log is public while its `public_log` setting is on.
Writes are audited as the caller: `via="api"`, or `via="web"` with the user's id for a signed-in user.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from doomtp_bot.api import deps
from doomtp_bot.api.access import (
    ADMIN_WRITE,
    BROADCASTER_WRITE,
    PERSONAL_WRITE,
    RANK_NAMES,
    READ,
    WRITE,
    WRITE_OWN,
    Caller,
    check_area,
    check_setting_role,
    current_session,
)
from doomtp_bot.channels.manager import ChannelBanned
from doomtp_bot.customcmds.packs import custom_modules
from doomtp_bot.customcmds.params import to_params
from doomtp_bot.customcmds.resolution import MODULE as CUSTOM_MODULE
from doomtp_bot.customcmds.resolution import is_custom, published_specs, returns, system_specs
from doomtp_bot.customcmds.service import CustomCommandService
from doomtp_bot.policy.roles import BOT_ADMIN_RANK, GLOBAL
from doomtp_bot.policy.service import PolicyService
from doomtp_bot.policy.settings import CHANNEL_SETTINGS
from doomtp_bot.policy.snapshot import ChannelSettings
from doomtp_bot.runtime.spec import CommandSpec

router = APIRouter(prefix="/api/v1", tags=["data"])

MAX_ROWS = 500


# ── plumbing ────────────────────────────────────────────────────────────────
def _policy(request: Request) -> PolicyService:
    return deps.policy(request)


def _channel(request: Request, login: str) -> ChannelSettings:
    settings = _policy(request).channel_by_login(login)
    if settings is not None:
        return settings
    raise HTTPException(status_code=404, detail=f"no channel named {login}")


# ── channels ────────────────────────────────────────────────────────────────


def _channel_json(settings: ChannelSettings) -> dict[str, Any]:
    return {
        "channel_id": settings.channel_id,
        "login": settings.login,
        "active": settings.active,
        "status": settings.status,
        # Left after Twitch refused a message with 403. Only a join with `rejoin` brings it back.
        "banned": settings.status == "banned",
        "tier": settings.tier,
        "capabilities": sorted(settings.capabilities),
        "prefix": settings.prefix,
        "timezone": settings.timezone,
        "bot_enabled": settings.bot_enabled,
        "slur_filter": settings.slur_filter,
        "log_enabled": settings.log_enabled,
        "history_backfill": settings.history_backfill,
        "public_log": settings.public_log,
        "quiet_errors": settings.quiet_errors,
        "cc_edit_notice": settings.cc_edit_notice,
        "reply_hold_ms": settings.reply_hold_ms,
        "automod": {"action": settings.automod_action, "timeout_s": settings.automod_timeout_s},
        "roles": {
            "channel_var_write": settings.channel_var_write_role,
            "grant_min": settings.grant_min_role,
            "publish_min": settings.publish_min_role,
            "create_min": settings.create_min_role,
            "var_admin": settings.var_admin_role,
        },
    }


class AutomodJson(BaseModel):
    action: str
    timeout_s: int


class ChannelRolesJson(BaseModel):
    channel_var_write: str
    grant_min: str
    publish_min: str
    create_min: str
    var_admin: str


class ChannelJson(BaseModel):
    """A channel and its settings. Times are in ms since the epoch."""

    channel_id: str
    login: str
    active: bool
    status: str
    banned: bool
    tier: str
    capabilities: list[str]
    prefix: str
    timezone: str
    bot_enabled: bool
    slur_filter: str
    log_enabled: bool
    history_backfill: bool
    public_log: bool
    quiet_errors: bool
    cc_edit_notice: bool
    reply_hold_ms: int
    automod: AutomodJson
    roles: ChannelRolesJson


class ChannelList(BaseModel):
    channels: list[ChannelJson]


class ChannelPatch(BaseModel):
    prefix: str | None = Field(default=None, max_length=16)
    quiet_errors: bool | None = None
    cc_edit_notice: bool | None = None
    bot_enabled: bool | None = None
    # No value turns the base list off: the lowest level is `base` (filters/slurs.py).
    slur_filter: Literal["base", "strict", "strictest"] | None = None
    log_enabled: bool | None = None
    history_backfill: bool | None = None
    public_log: bool | None = None
    reply_hold_ms: int | None = Field(default=None, ge=0, le=5000)
    timezone: str | None = None
    automod_action: Literal["off", "delete", "timeout"] | None = None
    automod_timeout_s: int | None = Field(default=None, ge=1, le=1_209_600)
    channel_var_write_role: str | None = None
    grant_min_role: str | None = None
    publish_min_role: str | None = None
    create_min_role: str | None = None
    var_admin_role: str | None = None


class JoinRequest(BaseModel):
    login: str = Field(min_length=1, max_length=40)
    # Needed to come back to a channel the bot left because it was banned there (architecture §10).
    rejoin: bool = False


class Enabled(BaseModel):
    enabled: bool


@router.get("/channels", response_model=ChannelList)
async def list_channels(request: Request, caller: Caller = READ) -> dict[str, Any]:
    channels = [c for c in _policy(request).channels() if caller.manages(c.login)]
    return {"channels": [_channel_json(c) for c in sorted(channels, key=lambda c: c.login)]}


@router.get("/channels/{login}", response_model=ChannelJson)
async def get_channel(request: Request, login: str, caller: Caller = READ) -> dict[str, Any]:
    return _channel_json(_channel(request, login))


@router.post("/channels", status_code=201)
async def join_channel(request: Request, body: JoinRequest, caller: Caller = ADMIN_WRITE) -> dict[str, Any]:
    """Join a channel by login. The same path `!join` takes, including the EventSub subscriptions."""
    channels, twitch = deps.channels(request), deps.twitch(request)
    user = await twitch.resolve_user(body.login)
    if user is None:
        raise HTTPException(status_code=404, detail=f"no Twitch user named {body.login}")
    try:
        failed = await channels.join(user["id"], user["name"], caller.actor, rejoin=body.rejoin)
    except ChannelBanned as exc:
        raise HTTPException(status_code=409, detail=f"{exc}; send rejoin=true to join anyway") from exc
    return {"login": user["name"], "channel_id": user["id"], "failed_subscriptions": failed}


@router.delete("/channels/{login}")
async def part_channel(request: Request, login: str, caller: Caller = BROADCASTER_WRITE) -> dict[str, Any]:
    """Leave the channel, as `!part` does: the broadcaster's call, or an admin's."""
    settings = _channel(request, login)
    await deps.channels(request).part(settings.channel_id, caller.actor)
    return {"login": settings.login, "status": "parted"}


# ADR-0026
@router.post("/me/channel", status_code=201)
async def join_own_channel(request: Request, caller: Caller = PERSONAL_WRITE) -> dict[str, Any]:
    """Add the bot to the signed-in user's own channel, the way `!join` does. Signing in with
    Twitch proved the channel is theirs. The session manages it from now on, without waiting for a refresh."""
    if caller.user_id is None or caller.login is None:
        raise HTTPException(status_code=400, detail="sign in with Twitch to add the bot to your channel")
    login = caller.login.lower()
    try:
        failed = await deps.channels(request).join(caller.user_id, login, caller.actor)
    except ChannelBanned as exc:
        raise HTTPException(status_code=409, detail=f"{exc}; only an admin can rejoin it") from exc
    session = await current_session(request)
    if session is not None and session.channels is not None:
        session.channels = session.channels | {login}
        if session.role == "user":
            session.role = "moderator"
    return {"login": login, "channel_id": caller.user_id, "failed_subscriptions": failed}


@router.patch("/channels/{login}", response_model=ChannelJson)
async def patch_channel(request: Request, login: str, body: ChannelPatch, caller: Caller = WRITE) -> dict[str, Any]:
    settings = _channel(request, login)
    policy = _policy(request)
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in CHANNEL_SETTINGS}
    if not changes:
        raise HTTPException(status_code=400, detail=f"nothing to change; fields: {', '.join(CHANNEL_SETTINGS)}")
    # Each field needs the rank chat asks for it; one field too high refuses the whole patch (ADR-0026).
    rank = caller.rank_in(policy, settings.login)
    refused = sorted(k for k in changes if CHANNEL_SETTINGS[k] > rank)
    if refused:
        who = RANK_NAMES.get(max(CHANNEL_SETTINGS[k] for k in refused), "an admin")
        raise HTTPException(status_code=403, detail=f"only {who} can change {', '.join(refused)}")

    async def write(repo: Any) -> None:
        for column, value in changes.items():
            await repo.set_channel_field(settings.channel_id, column, value, caller.actor)

    await policy.mutate(write)  # one snapshot reload for the whole patch
    return _channel_json(_channel(request, settings.login))


async def _module_specs(request: Request, channel_id: str) -> dict[str, tuple[CommandSpec, str]]:
    """Every module `!module` knows in a channel: the built-in ones, then the packs published there or
    globally and `custom` (ADR-0012). For `GLOBAL`, the packs published globally."""
    specs: dict[str, tuple[CommandSpec, str]] = {
        c.spec.module: (c.spec, "builtin") for c in deps.runtime(request).registry.all()
    }
    services = request.app.state
    found = await custom_modules(channel_id, getattr(services, "packs", None), getattr(services, "customcmds", None))
    for name, kind in found.items():
        specs.setdefault(name, (CommandSpec(name="", module=name, summary=""), kind))
    return specs


async def toggle_module(
    request: Request, scope: str, module: str, enabled: bool | None, caller: Caller
) -> dict[str, Any]:
    """`!module enable|disable|reset` in `scope` (a channel, or `GLOBAL`); `None` goes back to inheriting."""
    specs = await _module_specs(request, scope)
    if module not in specs:
        raise HTTPException(status_code=404, detail=f"no module named {module}")
    if not specs[module][0].toggleable:
        raise HTTPException(status_code=400, detail=f"{module} can't be turned off")
    await _policy(request).mutate(lambda repo: repo.set_module_toggle(scope, module, enabled, caller.actor))
    return {"module": module, "enabled": enabled}


# ADR-0012
@router.get("/channels/{login}/modules")
async def list_modules(request: Request, login: str, caller: Caller = READ) -> dict[str, Any]:
    """Each module, whether it is on here, and whether it can be turned off at all.

    `kind` says where it comes from: `builtin`, a `pack` published here or globally (its name is its
    module name), or `custom`, which holds the commands published one by one and appears when
    there are any. `enabled` follows the rules chat applies: this channel's toggle, then the global one,
    then on. Turn any of them off with `PUT …/modules/{module}`, as `!module disable` does in chat.
    """
    settings, policy = _channel(request, login), _policy(request)
    specs = await _module_specs(request, settings.channel_id)
    return {
        "modules": [
            {
                "module": m,
                "enabled": policy.is_enabled(settings.channel_id, s),
                "toggleable": s.toggleable,
                "kind": kind,
            }
            for m, (s, kind) in sorted(specs.items())
        ]
    }


class IgnoreBody(BaseModel):
    login: str = Field(min_length=1, max_length=40)
    everywhere: bool = False  # every channel, as `ignore add <user> global` does in chat
    reason: str | None = Field(default=None, max_length=200)


def _check_bot_wide(caller: Caller) -> None:
    """A change to the `GLOBAL` scope reaches every channel, so it is for admins (ADR-0017). This is the
    only write here that can reach `GLOBAL`: filters, toggles, rules and triggers are always written to
    the channel in the path, and a moderator can't reach a global entry through it."""
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail="only an admin can change a bot-wide ignore")


async def _login_of(request: Request, user_id: str | None, known: dict[str, str]) -> str | None:
    """A user id's login: from what the bot already stores, else from Twitch. None when neither knows."""
    if user_id is None:
        return None
    if user_id not in known:
        twitch = getattr(request.app.state, "twitch", None)
        lookup = getattr(twitch, "login_for", None)
        try:
            found = await lookup(user_id) if lookup is not None else None
        except Exception:  # Twitch being down must not take the list with it
            found = None
        if found is not None:
            known[user_id] = found
    return known.get(user_id)


async def _ignored_json(request: Request, scope: str) -> list[dict[str, Any]]:
    policy = _policy(request)
    known = {c.channel_id: c.login for c in policy.channels()}
    for s in (scope, GLOBAL):
        known.update({e.user_id: e.user_login for e in policy.ignore_entries(s) if e.user_login})
    return [
        {
            "user_id": e.user_id,
            "login": e.user_login,
            "reason": e.reason,
            "added_by": e.added_by,
            "added_by_login": await _login_of(request, e.added_by, known),
            "added_at": e.added_at,
        }
        for e in sorted(policy.ignore_entries(scope), key=lambda e: (e.user_login or "", e.user_id))
    ]


@router.get("/channels/{login}/ignored")
async def ignored_users(request: Request, login: str, caller: Caller = READ) -> dict[str, Any]:
    """Who the bot ignores here, and who it ignores in every channel: each as `{user_id, login, reason,
    added_by, added_by_login, added_at}`, `added_at` in epoch ms. `added_by` is the user id of whoever
    set it (their own, after `ignore me` in chat), or null when it came from the API or the admin UI.
    Changed from chat (`ignore`, `ignore me`, `unignore me`) or with the two endpoints below."""
    settings = _channel(request, login)
    return {
        "ignored": await _ignored_json(request, settings.channel_id),
        "ignored_everywhere": await _ignored_json(request, GLOBAL),
    }


@router.post("/channels/{login}/ignored", status_code=201)
async def add_ignored(request: Request, login: str, body: IgnoreBody, caller: Caller = WRITE) -> dict[str, Any]:
    """Ignore a user here, or everywhere with `everywhere: true` (admins only). Audited as `ignore.add`,
    like chat."""
    if body.everywhere:
        _check_bot_wide(caller)
    settings, policy = _channel(request, login), _policy(request)
    user = await deps.twitch(request).resolve_user(body.login)
    if user is None:
        raise HTTPException(status_code=404, detail=f"no Twitch user named {body.login}")
    scope = GLOBAL if body.everywhere else settings.channel_id
    await policy.mutate(
        lambda repo: repo.set_ignored(scope, user["id"], user["name"], True, caller.actor, reason=body.reason)
    )
    return {"user_id": user["id"], "login": user["name"], "everywhere": body.everywhere}


@router.delete("/channels/{login}/ignored/{user_id}")
async def remove_ignored(
    request: Request,
    login: str,
    user_id: str,
    everywhere: bool = Query(default=False, description="lift an ignore set for every channel instead"),
    caller: Caller = WRITE_OWN,
) -> dict[str, Any]:
    """Stop ignoring a user here (or everywhere). Audited as `ignore.remove`, like chat.

    `everywhere` is for admins. A signed-in user may also lift an ignore they set on themselves (`ignore
    me`), in any channel; that one is always per channel, so `everywhere` never applies to it."""
    if everywhere:
        _check_bot_wide(caller)
    own = caller.user_id == user_id and not everywhere
    if not own:
        check_area(caller, "channel", login)
    settings, policy = _channel(request, login), _policy(request)
    scope = GLOBAL if everywhere else settings.channel_id
    entry = policy.ignore_entry(scope, user_id)
    if own and not caller.manages(login) and (entry is None or entry.added_by != user_id):
        raise HTTPException(status_code=403, detail="only a moderator can lift an ignore someone else set")
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{user_id} isn't ignored {'everywhere' if everywhere else 'here'}")
    await policy.mutate(lambda repo: repo.set_ignored(scope, user_id, entry.user_login or "", False, caller.actor))
    return {"user_id": user_id, "removed": True, "everywhere": everywhere}


@router.put("/channels/{login}/modules/{module}")
async def set_module(
    request: Request, login: str, module: str, body: Enabled, caller: Caller = WRITE
) -> dict[str, Any]:
    settings = _channel(request, login)
    return await toggle_module(request, settings.channel_id, module, body.enabled, caller)


@router.delete("/channels/{login}/modules/{module}")
async def reset_module(request: Request, login: str, module: str, caller: Caller = WRITE) -> dict[str, Any]:
    """Back to what the bot-wide toggle says, as `!module reset` does."""
    settings = _channel(request, login)
    return await toggle_module(request, settings.channel_id, module, None, caller)


# ── commands ────────────────────────────────────────────────────────────────
LogLevelName = Literal["off", "errors", "output", "invocations", "all"]


class CooldownBody(BaseModel):
    tier_s: int = Field(ge=0, le=86_400)  # shared by everyone at that role
    user_s: int = Field(ge=0, le=86_400)  # per user


class CommandRule(BaseModel):
    """Only the fields sent change. `enabled: null` goes back to the module's setting; `required_role:
    null` clears the rule here (and its `allowed_roles`); a role's cooldown set to `null` is cleared."""

    enabled: bool | None = None
    required_role: str | None = None
    allowed_roles: list[str] | None = Field(default=None, max_length=20)
    cooldowns: dict[str, CooldownBody | None] | None = None
    log_level: LogLevelName | None = None


def command_spec(request: Request, name: str) -> CommandSpec:
    """A command as `!perm` and `!cmd` find it: in the registry, then among the system packs' members."""
    runtime = deps.runtime(request)
    wanted = name.lower()
    found = runtime.registry.get(wanted)
    if found is not None:
        return found.spec
    for spec in system_specs(runtime.resolver):
        if spec.name == wanted:
            return spec
    raise HTTPException(status_code=404, detail=f"no command named {name}")


async def _published(request: Request, channel_id: str) -> dict[str, CommandSpec]:
    services = request.app.state
    return await published_specs(channel_id, getattr(services, "customcmds", None), getattr(services, "packs", None))


async def channel_command_spec(request: Request, channel_id: str, name: str) -> CommandSpec:
    """A command as `!perm`, `!cooldown` and `!cmd` find it in a channel: a built-in or system pack member,
    else the custom command chat there reaches under that name (`_common.rule_spec`)."""
    try:
        return command_spec(request, name)
    except HTTPException:
        found = (await _published(request, channel_id)).get(name.lower())
        if found is None:
            raise
        return found


def rule_json(policy: PolicyService, scope: str, spec: CommandSpec) -> dict[str, Any]:
    required, allowed = policy.required_role(scope, spec)
    return {
        "name": spec.name,
        "module": spec.module,
        "summary": spec.summary,
        "enabled": policy.is_enabled(scope, spec),
        "toggleable": spec.toggleable,
        "fixed_policy": spec.fixed_policy,
        "required_role": required,
        # The rank its pack sets (ADR-0033): what `required_role` can't go under here.
        "min_role": spec.min_role,
        "allowed_roles": list(allowed) if allowed else None,
        "cooldowns": {
            role: {"tier_s": cd.tier_s, "user_s": cd.user_s} for role, cd in policy.cooldown_rules(scope, spec).items()
        },
        "log_level": policy.log_level(scope, spec).value,
        # A custom command (ADR-0009): its rules are keyed by its id, and it turns on and off with its
        # publication (`cc enable|disable`) or its pack's module, not here.
        "custom": is_custom(spec),
    }


@router.get("/channels/{login}/commands", summary="The commands a channel can run")
async def channel_commands(request: Request, login: str) -> dict[str, Any]:
    """What this channel's chat can actually run, with the rules that apply here. Public, like the page."""
    settings = _channel(request, login)
    policy, runtime = _policy(request), deps.runtime(request)
    listing = [
        {
            **rule_json(policy, settings.channel_id, spec),
            "requires": list(spec.requires),
            "missing": sorted(set(spec.requires) - set(settings.capabilities)),
        }
        for spec in [c.spec for c in runtime.registry.all()] + system_specs(runtime.resolver)
    ]
    # Then the custom commands chat here reaches, unless a built-in has the name (spec §5.1).
    taken = {c["name"] for c in listing}
    for spec in (await _published(request, settings.channel_id)).values():
        if spec.name not in taken:
            listing.append({**rule_json(policy, settings.channel_id, spec), "requires": [], "missing": []})
    return {"channel": settings.login, "prefix": settings.prefix, "commands": listing}


async def apply_command_rule(
    request: Request, scope: str, spec: CommandSpec, body: CommandRule, caller: Caller, rank: int
) -> None:
    """`!cmd`, `!perm` and `!cooldown` in one change, each checked the way chat checks it (ADR-0026)."""
    policy = _policy(request)
    fields = body.model_dump(exclude_unset=True)
    rules = {"required_role", "allowed_roles", "cooldowns"} & set(fields)
    if rules and spec.fixed_policy:
        raise HTTPException(status_code=400, detail=f"{spec.name} is always allowed for everyone")
    if rules and spec.module == "core_admin" and rank < BOT_ADMIN_RANK:
        raise HTTPException(status_code=403, detail="only a bot admin can change an admin command's rules")
    if "enabled" in fields and is_custom(spec):
        where = f"cc disable|enable {spec.name}" if spec.module == CUSTOM_MODULE else f"the {spec.module} module"
        raise HTTPException(
            status_code=400, detail=f"{spec.name} is a custom command: it turns off and on with {where}"
        )
    if "enabled" in fields and not spec.toggleable:
        raise HTTPException(status_code=400, detail=f"{spec.name} can't be turned off")
    if fields.get("allowed_roles") == []:
        raise HTTPException(status_code=400, detail="allowed_roles needs a role, or null to clear it")
    named = [fields.get("required_role"), *(body.allowed_roles or []), *(body.cooldowns or {})]
    unknown = sorted({r for r in named if r is not None and policy.rank_of(scope, r) is None})
    if unknown:
        raise HTTPException(status_code=400, detail=f"no role named {', '.join(unknown)}")
    lower = [fields.get("required_role"), *(body.allowed_roles or [])]
    if any(r is not None and policy.below_floor(scope, spec, r) for r in lower):
        raise HTTPException(
            status_code=400,
            detail=f"{spec.name} needs {spec.min_role} at least: its {spec.module} pack sets that,"
            " and a channel can only raise it",
        )

    async def write(repo: Any) -> None:
        if "enabled" in fields or "log_level" in fields:
            await repo.set_command_toggle(
                scope,
                spec.key,
                caller.actor,
                enabled=fields.get("enabled"),
                log_level=fields.get("log_level"),
                clear_enabled="enabled" in fields and fields["enabled"] is None,
            )
        if "required_role" in fields or "allowed_roles" in fields:
            current, allowed = policy.required_role(scope, spec)
            required = fields.get("required_role", current)
            roles = fields.get("allowed_roles", list(allowed) if allowed else None)
            if required is None:
                roles = None
            await repo.set_command_rule(scope, spec.key, required, roles, caller.actor)
        for role, cooldown in (body.cooldowns or {}).items():
            tier, user = (None, None) if cooldown is None else (cooldown.tier_s, cooldown.user_s)
            await repo.set_cooldown(scope, spec.key, role, tier, user, caller.actor)

    await policy.mutate(write)


@router.patch("/channels/{login}/commands/{name}")
async def patch_command(
    request: Request, login: str, name: str, body: CommandRule, caller: Caller = WRITE
) -> dict[str, Any]:
    """Turn a command on or off here, set who may use it, its cooldowns per role and how much of its runs
    is logged: `!cmd`, `!perm` and `!cooldown` in one call."""
    settings, policy = _channel(request, login), _policy(request)
    spec = await channel_command_spec(request, settings.channel_id, name)
    rank = caller.rank_in(policy, settings.login)
    await apply_command_rule(request, settings.channel_id, spec, body, caller, rank)
    return rule_json(policy, settings.channel_id, spec)


def reset_rule(policy: PolicyService, scope: str, spec: CommandSpec) -> CommandRule:
    """What `DELETE` sends: `!cmd reset`, `!perm clear` and every cooldown cleared, where each applies."""
    fields: dict[str, Any] = {"enabled": None} if spec.toggleable and not is_custom(spec) else {}
    if not spec.fixed_policy:
        fields["required_role"] = None
        fields["cooldowns"] = dict.fromkeys(policy.cooldown_rules(scope, spec))
    return CommandRule(**fields)


@router.delete("/channels/{login}/commands/{name}")
async def reset_command(request: Request, login: str, name: str, caller: Caller = WRITE) -> dict[str, Any]:
    """Back to the command's own rules here. The log level stays."""
    settings, policy = _channel(request, login), _policy(request)
    spec = await channel_command_spec(request, settings.channel_id, name)
    rank = caller.rank_in(policy, settings.login)
    body = reset_rule(policy, settings.channel_id, spec)
    await apply_command_rule(request, settings.channel_id, spec, body, caller, rank)
    return rule_json(policy, settings.channel_id, spec)


# ── custom commands (ADR-0009 action item 5) ────────────────────────────────
def _custom_json(command: Any) -> dict[str, Any]:
    return {
        "id": command.id,
        "name": command.name,
        "owner": command.owner_login,
        "summary": command.summary,
        "body": command.body,
        "version": command.version,
        "shareable": command.shareable,
        "params": [
            {
                "position": p.position,
                "name": p.name,
                "type": p.type,
                "required": p.required,
                "description": p.description,
                "choices": list(p.choices),
            }
            for p in to_params(command.params)
        ],
        "data": returns(command.body),  # what {_N} holds afterwards, read from the body's `result` stages
    }


# ADR-0012
@router.get("/custom-commands", summary="Shared custom commands")
async def custom_commands(request: Request, owner: str | None = Query(default=None, max_length=40)) -> dict[str, Any]:
    """Public: what is published everywhere, or one owner's shared commands."""
    service: CustomCommandService = deps.customcmds(request)
    if owner is None:
        published = await service.publications_in(GLOBAL)
        return {"commands": [{**_custom_json(c), "published_as": p.name} for p, c in published]}
    twitch = deps.twitch(request)
    user = await twitch.resolve_user(owner)
    if user is None:
        raise HTTPException(status_code=404, detail=f"no Twitch user named {owner}")
    owned = await service.owned_by(user["id"])
    return {"commands": [_custom_json(c) for c in owned if c.shareable]}


@router.get("/channels/{login}/publications", summary="A channel's published commands")
async def publications(request: Request, login: str) -> dict[str, Any]:
    """Public: the custom commands this channel offers, and who wrote them."""
    settings = _channel(request, login)
    service: CustomCommandService = deps.customcmds(request)
    found = await service.publications_in(settings.channel_id)
    return {
        "channel": settings.login,
        "publications": [
            {
                **_custom_json(command),
                "published_as": publication.name,
                "status": publication.status,
                "required_role": publication.required_role,
                # The version this channel last ran; differs from "version" when the author edited it since.
                "last_run_version": publication.last_run_version,
            }
            for publication, command in found
        ],
    }


@router.patch("/channels/{login}/publications/{name}")
async def set_publication(
    request: Request, login: str, name: str, body: Enabled, caller: Caller = PERSONAL_WRITE
) -> dict[str, Any]:
    """`cc enable|disable`: for whoever reaches the channel's `publish_min_role`, as in chat."""
    settings = _channel(request, login)
    check_setting_role(request, caller, settings.login, "publish_min_role", "turn published commands on or off")
    service: CustomCommandService = deps.customcmds(request)
    changed = await service.set_publication_status(
        channel_id=settings.channel_id,
        name=name,
        status="active" if body.enabled else "disabled",
        actor_user_id=caller.actor.user_id,
        actor_via=caller.actor.via,
    )
    if not changed:
        raise HTTPException(status_code=404, detail=f"{name} isn't published here")
    return {"name": name, "enabled": body.enabled}
