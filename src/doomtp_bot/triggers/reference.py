"""What the public docs say about triggers: each event's cause and `{event.*}` variables, and the `$` fields.

One source for the site's language page, served by `GET /api/v1/language` (`events`, `bot_field_docs`). A test
checks that every trigger type and every `$` field has an entry, so a new one can't ship undocumented.
`trigger_payload` builds the flat `{event.*}` of the notification types out of Twitch's own detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Twitch's name for a chat notice → the trigger type that listens for it.
NOTICE_TRIGGER_TYPE = {"sub_gift": "gift_sub"}


def _person(raw: Any) -> dict[str, str] | None:
    """`{id, login, display}` as the notification payload keeps a user → `{id, name, display}`."""
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    name = str(raw.get("login") or raw.get("name") or "")
    return {"id": str(raw["id"]), "name": name, "display": str(raw.get("display") or name)}


def trigger_payload(notice: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The notification's payload plus the flat fields a trigger reads: `user`, and per type `viewers`,
    `tier`, `months`, `recipient`. Twitch's own detail stays under `detail`."""
    if notice not in ("raid", "sub", "resub", "sub_gift", "community_sub_gift"):
        return payload  # follows, redemptions and cheers are built flat already (twitch/mapping.py)
    raw = payload.get("detail")
    detail: dict[str, Any] = raw if isinstance(raw, dict) else {}
    out = dict(payload)
    if notice == "raid":
        out["user"] = _person(detail.get("user")) or _person(payload.get("chatter"))
        out["viewers"] = int(detail.get("viewer_count") or 0)
        return out
    out["user"] = _person(payload.get("chatter"))
    out["tier"] = detail.get("tier")
    if notice == "sub":
        out["months"] = int(detail.get("months") or 1)
    elif notice == "resub":
        out["months"] = int(detail.get("cumulative_months") or detail.get("months") or 0)
        out["streak_months"] = detail.get("streak_months")
        out["prime"] = bool(detail.get("prime"))
        out["gift"] = bool(detail.get("gift"))
    elif notice == "sub_gift":
        out["months"] = int(detail.get("months") or 1)
        out["recipient"] = _person(detail.get("recipient"))
    elif notice == "community_sub_gift":
        out["total"] = int(detail.get("total") or 0)
    return out


@dataclass(frozen=True, slots=True)
class FieldDoc:
    """One readable field: its path below the root, its type, what it holds, and a value it can have."""

    name: str
    type: str
    description: str
    example: str = ""


@dataclass(frozen=True, slots=True)
class EventDoc:
    fires: str  # what sets it off
    variables: tuple[FieldDoc, ...] = ()  # the {event.*} fields, beyond `type`
    chatter: str = ""  # who `$chatter` is in the run; empty: nobody
    needs: str = ""  # what the channel must have granted
    match: str = ""  # the trigger's own conditions


@dataclass(frozen=True, slots=True)
class RootDoc:
    about: str
    available: str  # where the root can be read
    fields: tuple[FieldDoc, ...]


def _user(prefix: str, who: str, *, display: bool = True, missing: str = "") -> tuple[FieldDoc, ...]:
    """`{prefix}.id`, `.name` and `.display` of a person in an event."""
    note = f"; {missing}" if missing else ""
    rows = [
        FieldDoc(f"{prefix}.id", "str", f"Twitch user id of {who}{note}", "80730642"),
        FieldDoc(f"{prefix}.name", "str", f"login of {who}, lowercase", "testfromuser"),
    ]
    if display:
        rows.append(FieldDoc(f"{prefix}.display", "str", f"display name of {who}", "TestFromUser"))
    return tuple(rows)


_TIER = FieldDoc("tier", "str", "sub tier: 1000, 2000 or 3000 (Prime counts as 1000)", "1000")

EVENTS: dict[str, EventDoc] = {
    "redemption": EventDoc(
        "a viewer redeems a channel point reward",
        (
            *_user("user", "the viewer"),
            FieldDoc("input", "str", "the text the viewer entered; empty when the reward asks for none", "hello"),
            FieldDoc("text", "str", "the same as `input`", "hello"),
            FieldDoc("reward.id", "str", "the reward's Twitch id", "92af127c-7326-4483-a52b-b0da0be61c01"),
            FieldDoc("reward.title", "str", "the reward's title", "Hydrate"),
            FieldDoc("reward.cost", "int", "its price in channel points", "500"),
            FieldDoc("status", "str", "unfulfilled, fulfilled or canceled", "unfulfilled"),
        ),
        "the viewer",
        needs="the broadcaster connects the bot (full tier)",
        match="reward_id",
    ),
    "raid": EventDoc(
        "another channel raids this one",
        (*_user("user", "the raiding channel"), FieldDoc("viewers", "int", "how many came with the raid", "42")),
        "the raider",
        match="min_viewers",
    ),
    "sub": EventDoc(
        "a viewer subscribes for the first time",
        (*_user("user", "the subscriber"), _TIER, FieldDoc("months", "int", "always 1", "1")),
        "the subscriber",
    ),
    "resub": EventDoc(
        "a viewer renews, and shares it",
        (
            *_user("user", "the subscriber"),
            _TIER,
            FieldDoc("months", "int", "total months subscribed", "14"),
            FieldDoc("streak_months", "int", "months in a row; missing when they don't share it", "3"),
            FieldDoc("prime", "bool", "paid with Prime", "false"),
            FieldDoc("gift", "bool", "the sub was a gift", "false"),
            FieldDoc("text", "str", "the message they added", "14 months!"),
        ),
        "the subscriber",
        match="min_months",
    ),
    "gift_sub": EventDoc(
        "a viewer gifts a sub to one person (a community gift of five fires this five times)",
        (
            *_user("user", "the gifter", missing="the whole `user` is missing when anonymous"),
            *_user("recipient", "who got it"),
            _TIER,
            FieldDoc("months", "int", "months gifted", "1"),
        ),
        "the gifter",
    ),
    "cheer": EventDoc(
        "a viewer cheers bits",
        (
            *_user("user", "the cheerer", missing="the whole `user` is missing when anonymous"),
            FieldDoc("bits", "int", "how many", "250"),
            FieldDoc("text", "str", "the cheer's message", "This is a test event."),
            FieldDoc("anonymous", "bool", "cheered anonymously", "false"),
        ),
        "the cheerer",
        needs="the broadcaster connects the bot (full tier)",
        match="min_bits",
    ),
    "follow": EventDoc(
        "a viewer follows the channel",
        (
            *_user("user", "the new follower"),
            FieldDoc("followed_at", "int", "when, in epoch milliseconds", "1767268800000"),
        ),
        "the new follower",
        needs="the bot is a moderator in the channel",
    ),
    "stream_online": EventDoc(
        "the bot's once-a-minute check sees the stream go live",
        (
            FieldDoc("live", "bool", "always true", "true"),
            FieldDoc("title", "str", "stream title, as Twitch lists it", "late night doom"),
            FieldDoc("game", "str", "category", "DOOM"),
            FieldDoc("viewers", "int", "viewer count", "12"),
            FieldDoc("started_at", "str", "ISO time the stream started", "2026-10-17T20:00:00Z"),
            FieldDoc("channel.login", "str", "the channel", "doomtp"),
        ),
    ),
    "stream_offline": EventDoc(
        "the same check sees the stream is no longer live",
        (FieldDoc("live", "bool", "always false", "false"), FieldDoc("channel.login", "str", "the channel", "doomtp")),
    ),
    "pyramid": EventDoc(
        "a chatter builds an emote pyramid, row by row, or breaks one",
        (
            FieldDoc("phase", "str", "step, complete or broken", "step"),
            FieldDoc("direction", "str", "up or down on a step; empty otherwise", "up"),
            FieldDoc("token", "str", "the emote", "Kappa"),
            FieldDoc("width", "int", "this row's width", "3"),
            FieldDoc("peak", "int", "the widest row so far", "3"),
            *_user("user", "the builder"),
            *_user("breaker", "who broke it", missing="only on broken"),
            FieldDoc("by_bot", "bool", "the bot finished or broke it", "false"),
            FieldDoc("self_broken", "bool", "the builder broke their own pyramid", "false"),
            FieldDoc(
                "pyramid_id",
                "str",
                "the message id of its first row; ties its rows together",
                "b7e0f1c2-4d3a-4e5f-9a8b-1c2d3e4f5a6b",
            ),
        ),
        "the builder, or the breaker on broken",
    ),
    "timer": EventDoc("its interval passes (every 60s at the least), counted in chat lines and live time as set"),
    "cron": EventDoc("the clock matches its cron schedule"),
    "listener": EventDoc(
        "a chat line matches its regex",
        (
            *_user("user", "who wrote it", display=False),
            FieldDoc("message", "str", "the line", "anyone playing doom?"),
        ),
        "the author",
        match="the regex; its captures are `{match.*}`",
    ),
    "bot_online": EventDoc(
        "the bot is in chat: at start, after an update, or after its Twitch connection was started again. "
        "A channel hears it at most every 15 minutes",
        (
            FieldDoc("reason", "str", "start, update or reconnect", "start"),
            FieldDoc("cause", "str", "on reconnect, why the connection was restarted", "not hearing chat"),
            FieldDoc("down_s", "int", "seconds it was gone; missing when not known", "40"),
            FieldDoc("version", "str", "the running version", "1.4.0"),
        ),
    ),
    "bot_updated": EventDoc(
        "the version changed since the last run; fires just before bot_online",
        (
            FieldDoc("version", "str", "the new version", "1.4.0"),
            FieldDoc("previous", "str", "the version before", "1.3.2"),
        ),
    ),
    "bot_joined": EventDoc("the bot is asked into the channel (!join or the website), not at every start"),
    "bot_modded": EventDoc("the bot's hourly check sees it became a moderator here"),
    "bot_unmodded": EventDoc("the same check sees it stopped being one"),
}

#: Every root a placeholder reads that the bot fills in, keyed as typed. The `$` roots' field names equal
#: `lang.parser.BOT_FIELDS`; `{event.*}` is per trigger type (EVENTS), `{match.*}` the listener's captures.
ROOTS: dict[str, RootDoc] = {
    "$chatter": RootDoc(
        "who ran the command, or the event's user in a trigger; missing when nobody did",
        "anywhere",
        (
            FieldDoc("id", "str", "Twitch user id", "80730642"),
            FieldDoc("name", "str", "login, lowercase", "alice"),
            FieldDoc("display", "str", "display name", "Alice"),
            FieldDoc("rank", "int", "permission rank in this channel (higher can do more)", "20"),
            FieldDoc("roles", "list", "the roles they hold here, lowest first", "[everyone, subscriber]"),
            FieldDoc("is_sub", "bool", "subscriber", "true"),
            FieldDoc("is_vip", "bool", "VIP", "false"),
            FieldDoc("is_mod", "bool", "moderator", "false"),
        ),
    ),
    "$channel": RootDoc(
        "the channel the line is in",
        "anywhere",
        (
            FieldDoc("id", "str", "Twitch channel id", "40174384"),
            FieldDoc("name", "str", "login, lowercase", "doomtp"),
            FieldDoc("display", "str", "display name", "DoomTP"),
            FieldDoc("prefix", "str", "the command sign here", "!"),
            FieldDoc("live", "bool", "whether the stream is live", "true"),
            FieldDoc("title", "str", "stream title; missing when offline", "late night doom"),
            FieldDoc("game", "str", "category; missing when offline", "DOOM"),
            FieldDoc("viewers", "int", "viewer count; 0 when offline", "12"),
            FieldDoc("uptime", "int", "seconds since the stream started; missing when offline", "5400"),
            FieldDoc("next_stream", "map", "the next stream on the Twitch schedule; missing when none", ""),
            FieldDoc("next_stream.title", "str", "its title; missing when it has none", "doom marathon"),
            FieldDoc("next_stream.category", "str", "its category; missing when it has none", "DOOM"),
            FieldDoc("next_stream.start", "str", "ISO time it starts", "2026-10-18T20:00:00Z"),
            FieldDoc("next_stream.in", "int", "seconds from now until it starts", "86400"),
        ),
    ),
    "$publisher": RootDoc(
        "who published the custom command being run",
        "custom commands",
        (
            FieldDoc("id", "str", "Twitch user id", "40174384"),
            FieldDoc("name", "str", "login, lowercase", "doomtp"),
            FieldDoc("display", "str", "display name", "DoomTP"),
        ),
    ),
    "$bot": RootDoc(
        "the bot itself",
        "anywhere",
        (
            FieldDoc("name", "str", "the bot's name", "doomtp-bot"),
            FieldDoc("id", "str", "its Twitch id (empty for now)", ""),
            FieldDoc("version", "str", "running version", "1.4.0"),
            FieldDoc("release", "str", "the tag or branch the image was built from; missing from a checkout", "v1.4.0"),
            FieldDoc("commit", "str", "the short commit it was built from; missing from a checkout", "e8f7187"),
            FieldDoc("uptime", "int", "seconds since this process started", "3600"),
        ),
    ),
    "$now": RootDoc(
        "the time of the run, in the channel's timezone",
        "anywhere",
        (
            FieldDoc("iso", "str", "ISO 8601 time, to the second", "2026-10-17T21:30:00+02:00"),
            FieldDoc("unix", "int", "epoch seconds", "1792265400"),
            FieldDoc("date", "str", "the date, YYYY-MM-DD", "2026-10-17"),
            FieldDoc("time", "str", "the time, HH:MM", "21:30"),
            FieldDoc("weekday", "str", "day of the week, in English", "Saturday"),
        ),
    ),
    "cmd": RootDoc(
        "the custom command being run",
        "custom commands",
        (
            FieldDoc("name", "str", "its name", "hug"),
            FieldDoc("alias", "str", "the name it was called by", "hug"),
            FieldDoc("id", "str", "its id", "cc_3k9x"),
            FieldDoc("version", "int", "its published version", "2"),
            FieldDoc("owner", "str", "the publisher's login", "doomtp"),
        ),
    ),
    "run": RootDoc(
        "this run",
        "anywhere",
        (
            FieldDoc("id", "str", "the run's id, as the log and explain show it", "r_01JA2B3C"),
            FieldDoc("trigger", "str", "what started it: chat, or a trigger type", "chat"),
        ),
    ),
    "cooldown": RootDoc(
        "the cooldown that refused the command",
        "`on_cooldown` callbacks",
        (
            FieldDoc("command", "str", "the command, by its canonical name", "random"),
            FieldDoc("tier", "str", "the role whose cooldown rule applied", "everyone"),
            FieldDoc("tier_remaining", "int", "seconds left on the shared cooldown", "12"),
            FieldDoc("user_remaining", "int", "seconds left on this chatter's own cooldown", "0"),
        ),
    ),
    "denied": RootDoc(
        "why the command was refused",
        "`on_denied` callbacks",
        (
            FieldDoc("command", "str", "the command, by its canonical name", "timeout"),
            FieldDoc("required_role", "str", "the role it needs", "moderator"),
            FieldDoc("rank", "int", "the chatter's rank", "20"),
        ),
    ),
}

#: The older shapes `GET /api/v1/language` keeps serving: `$` field → description, and `$` root → about.
BOT_FIELD_DOCS: dict[str, dict[str, str]] = {
    root[1:]: {f.name: f.description for f in doc.fields if "." not in f.name}
    for root, doc in ROOTS.items()
    if root.startswith("$")
}
BOT_ROOT_DOCS: dict[str, str] = {root[1:]: doc.about for root, doc in ROOTS.items() if root.startswith("$")}


def _field(f: FieldDoc) -> dict[str, str]:
    return {"name": f.name, "type": f.type, "description": f.description, "example": f.example}


def reference() -> dict[str, Any]:
    """The JSON `GET /api/v1/language` serves."""
    return {
        "events": [
            {
                "type": type_,
                "fires": doc.fires,
                "variables": [{**_field(f), "meaning": f.description} for f in doc.variables],
                "chatter": doc.chatter,
                "needs": doc.needs,
                "match": doc.match,
            }
            for type_, doc in EVENTS.items()
        ],
        "root_docs": {
            root: {"about": doc.about, "available": doc.available, "fields": [_field(f) for f in doc.fields]}
            for root, doc in ROOTS.items()
        },
        "bot_field_docs": BOT_FIELD_DOCS,
        "bot_root_docs": BOT_ROOT_DOCS,
    }


def _table(fields: tuple[FieldDoc, ...], root: str) -> list[str]:
    rows = ["| Field | Type | Description | Example |", "|---|---|---|---|"]
    for f in fields:
        example = f"`{f.example}`" if f.example else ""
        rows.append(f"| `{{{root}.{f.name}}}` | {f.type} | {f.description} | {example} |")
    return rows


def markdown() -> str:
    """The generated part of docs/namespaces.md: a summary of the roots, then a table per root and per
    trigger type. `scripts/reference_docs.py` writes it; a test fails when the file is behind."""
    out = ["| Root | What | Read in |", "|------|------|---------|"]
    out += [f"| `{{{root}.*}}` | {doc.about} | {doc.available} |" for root, doc in ROOTS.items()]
    out += [
        "| `{event.*}` | the trigger's payload, per type (§4.2) | custom commands and triggers |",
        "| `{match.*}` | a listener regex's captures: `0` (whole match), `1..N`, named groups | listeners |",
        "",
        "### 4.1 Root fields",
        "",
    ]

    for root, doc in ROOTS.items():
        out += [f"#### `{root}`", "", f"{doc.about[0].upper()}{doc.about[1:]}. Read in: {doc.available}.", ""]
        out += [*_table(doc.fields, root), ""]
    out += [
        "### 4.2 Event fields",
        "",
        "Every trigger's `{event.type}` (str) is its trigger type. The rest depends on the type.",
        "",
    ]
    for type_, event in EVENTS.items():
        out += [f"#### `{type_}`", "", f"Fires when {event.fires}."]
        if event.chatter:
            out.append(f"`$chatter` is {event.chatter}.")
        out.append("")
        out += [*_table(event.variables, "event"), ""] if event.variables else ["No fields beyond `type`.", ""]
    return "\n".join(out).rstrip() + "\n"
