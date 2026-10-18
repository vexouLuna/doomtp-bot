# Placeholder Namespaces — Registry

**Status:** Canonical list (update together with any language or runtime change) · **Date:** 2026-09-28 · **Syntax:** 2.0 (ADR-0018)

Every reference in a `{…}` placeholder starts with one of the roots below. **No other root names are valid.** Adding a root means adding a row here, updating `lang/parser.py` (`BOT_FIELDS`, `REGISTERED_ROOTS`) and `runtime/namespaces.py`, and checking that `GET /api/v1/language` reports it.

General form: `{ expression [ ?? fallback ] }`, where the simplest expression is one reference such as `{channel.deaths}`. The full grammar is in [command-language-spec.md](command-language-spec.md) §2.7–§2.8.

**`.` and `[ ]`.** A `.` walks names the bot defines: a namespace, a variable's name, a `$` field, a result's `code`/`message`/`data`. `[ ]` walks into a *value*: `{channel.stats[kills]}`, `{channel.log[-1]}`, `{channel.quotes[arg.1]}`, `{x["key with spaces"]}`. A bare word in brackets is a literal key; anything else (a number, a string, a reference, an operator) is an expression.

**`$` means the bot's.** Everything the bot supplies starts with `$` (`{$chatter.display}`); no `$` means a variable (`{channel.deaths}`). A new field can never collide with a variable.

---

## 1. Results

| Reference | Meaning | Available in |
|-----------|---------|--------------|
| `{_}` | Result flowing into this command (pipe stdin, or the previous result after `&&`/`\|\|`) | any expression |
| `{_N}` (`{_1}`, `{_2}`, …) | Result of the Nth command in source order (1-based) | any expression; must be able to have run before use (checked at preflight) |

A bare number is a number: `{1}` renders `1`. (For one release a typed line refuses `{1}` alone with the hint `a result is {_1} now`.)

| Form | Value |
|------|-------|
| `{_1}` | data if scalar, else message |
| `{_1.code}` | exit code |
| `{_1.message}` | formatted message |
| `{_1.data}` | full data |
| `{_1[key]}`, `{_1[items][0]}` | inside the data (e.g. `{_1[user]}` after `timeout`; each command's docs list its data) |


At most one of `.code`, `.message`, `.data` follows a result; anything else goes in brackets, so a data key named `code` is `{_1[code]}`.

## 2. Arguments

Only inside **custom command bodies**, trigger expressions (where the arguments are the event text) and callbacks. Built-in commands receive typed args directly, but **their documentation uses the same `arg.N` names**.

| Placeholder | Meaning |
|-------------|---------|
| `{arg.1}`, `{arg.2}` … | Nth argument (quotes removed; a quoted string is one argument) |
| `{arg.3+}` | Arguments 3..end, joined with single spaces, quotes removed. This is the "rest of the message" capture for unquoted text. *(Stripping quotes is the current choice; verify it in testing. See language proposal §5.)* |
| `{arg.3+raw}` | Arguments 3..end exactly as typed (original spacing and quotes; a placeholder there counts as the text it expanded to) |
| `{arg.count}` | Number of arguments |
| `{args}` | Same as `{arg.1+}` |
| `{arg.<name>}` | Alias for a positional argument, if the command declares a name for it (e.g. `arg.1` = `sides` → `{arg.sides}`) |

### Types (`:type`)

A type is either **declared once** in the command's parameter definition, or given **inline** as `{arg.1:int}`. It validates the value and converts it. If a declared parameter fails validation, the command returns **code 2**, with a usage message generated from the parameter docs. A failed inline cast counts as missing: the `??` fallback is used, or the invocation fails with 230 (`E_MISSING_VALUE`).

| Type | Accepts | Converted value / paths |
|------|---------|-------------------------|
| `str` | anything (default) | string |
| `int` | `-3`, `42` | integer; params may add `min` and `max` |
| `float` | `3.5`, `-0.2` | float |
| `bool` | `true/false/yes/no/on/off/1/0` | boolean |
| `range` | `1-100` | map: `{arg.1[lo]}`, `{arg.1[hi]}` |
| `duration` | `30s`, `10m`, `1h30m`, or plain seconds | integer seconds |
| `user` | `@name`, `name` | resolved Twitch user, a map: `{arg.1[id]}`, `{arg.1[name]}`, `{arg.1[display]}` |
| `choice(a,b,c)` | one of the listed values | string |
| `url` | http(s) URLs | string (passes a URL safety check) |
| `list` | a placeholder holding a list (passed through), or JSON `[…]` | list: `[i]`, `[-i]`, `:len` |
| `map` | a placeholder holding a map (passed through), or JSON `{…}` | map: `[key]`, `:len`, `:keys`, `:values` |

`[ ]` goes **into a value** and `:` **converts or measures** it. This keeps `{arg.1[name]}` (a key of a user) distinct from `{arg.1:int}` (type validation). The accessors `:len`, `:keys` and `:values` work on any list or map.

## 3. Variables (persistent)

| Namespace | Keyed by | Read | Write |
|-----------|----------|------|-------|
| `{chatter.x}` | user (global, all channels) | any pipeline (invoker's row) | invoker's own pipelines (typed by them, or their own custom commands) and built-ins declaring the write |
| `{channel.x}` | channel | anyone in the channel | invoker rank ≥ `channel_var_write_role` (default: moderator), built-ins declaring the write, or a publication with a **write grant** (ADR-0010) |
| `{channel.chatter.x}` | channel + user | anyone in the channel (e.g. leaderboards) | invoker's own pipelines, built-ins declaring the write, publications with a write grant |
| `{publisher.x}` | owner of the running custom command | only inside that owner's custom commands | same as read |
| `{publisher.chatter.x}` | owner of the running custom command + invoker | only inside that owner's custom commands | same as read |
| `{publisher.channel.x}` | owner + current channel | only inside that owner's custom commands (channel games, per-channel state) | same as read |
| `{publisher.channel.chatter.x}` | owner + current channel + invoker | only inside that owner's custom commands (per-player state in a channel game) | same as read |

- **`chatter` in these combined namespaces always means the invoker.** For triggers, it is the event user.
- **`channel` always means the channel the run is in.**
- **All variables are public for now.** Access control only restricts writes. Private variables are a future consideration.
- Other users' or channels' rows can't be addressed directly.
- Read inside a stored value with brackets: `{channel.stats[kills]}`. Writes take the same path: `!var set channel.stats[kills] 3`, `-> channel.stats[kills]`, `--> channel.log` (append).
- The full read/write rules per actor, plus grants, are in **[variable-access-matrix.md](variable-access-matrix.md)**. That table is authoritative. The Read and Write columns above are a summary.

## 4. Context (read-only)

A `$` root takes exactly one field (`$channel.next_stream` one more). `cmd`, `event`, `match`, `cooldown`, `denied` and `run` are structures the bot defines, so their dots go as deep as the structure does.

The tables below are generated from `triggers/reference.py` (`scripts/reference_docs.py`); the site's language page serves the same data.

<!-- reference:begin -->
| Root | What | Read in |
|------|------|---------|
| `{$chatter.*}` | who ran the command, or the event's user in a trigger; missing when nobody did | anywhere |
| `{$channel.*}` | the channel the line is in | anywhere |
| `{$publisher.*}` | who published the custom command being run | custom commands |
| `{$bot.*}` | the bot itself | anywhere |
| `{$now.*}` | the time of the run, in the channel's timezone | anywhere |
| `{cmd.*}` | the custom command being run | custom commands |
| `{run.*}` | this run | anywhere |
| `{cooldown.*}` | the cooldown that refused the command | `on_cooldown` callbacks |
| `{denied.*}` | why the command was refused | `on_denied` callbacks |
| `{event.*}` | the trigger's payload, per type (§4.2) | custom commands and triggers |
| `{match.*}` | a listener regex's captures: `0` (whole match), `1..N`, named groups | listeners |

### 4.1 Root fields

#### `$chatter`

Who ran the command, or the event's user in a trigger; missing when nobody did. Read in: anywhere.

| Field | Type | Description | Example |
|---|---|---|---|
| `{$chatter.id}` | str | Twitch user id | `80730642` |
| `{$chatter.name}` | str | login, lowercase | `alice` |
| `{$chatter.display}` | str | display name | `Alice` |
| `{$chatter.rank}` | int | permission rank in this channel (higher can do more) | `20` |
| `{$chatter.roles}` | list | the roles they hold here, lowest first | `[everyone, subscriber]` |
| `{$chatter.is_sub}` | bool | subscriber | `true` |
| `{$chatter.is_vip}` | bool | VIP | `false` |
| `{$chatter.is_mod}` | bool | moderator | `false` |

#### `$channel`

The channel the line is in. Read in: anywhere.

| Field | Type | Description | Example |
|---|---|---|---|
| `{$channel.id}` | str | Twitch channel id | `40174384` |
| `{$channel.name}` | str | login, lowercase | `doomtp` |
| `{$channel.display}` | str | display name | `DoomTP` |
| `{$channel.prefix}` | str | the command sign here | `!` |
| `{$channel.live}` | bool | whether the stream is live | `true` |
| `{$channel.title}` | str | stream title; missing when offline | `late night doom` |
| `{$channel.game}` | str | category; missing when offline | `DOOM` |
| `{$channel.viewers}` | int | viewer count; 0 when offline | `12` |
| `{$channel.uptime}` | int | seconds since the stream started; missing when offline | `5400` |
| `{$channel.next_stream}` | map | the next stream on the Twitch schedule; missing when none |  |
| `{$channel.next_stream.title}` | str | its title; missing when it has none | `doom marathon` |
| `{$channel.next_stream.category}` | str | its category; missing when it has none | `DOOM` |
| `{$channel.next_stream.start}` | str | ISO time it starts | `2026-10-18T20:00:00Z` |
| `{$channel.next_stream.in}` | int | seconds from now until it starts | `86400` |

#### `$publisher`

Who published the custom command being run. Read in: custom commands.

| Field | Type | Description | Example |
|---|---|---|---|
| `{$publisher.id}` | str | Twitch user id | `40174384` |
| `{$publisher.name}` | str | login, lowercase | `doomtp` |
| `{$publisher.display}` | str | display name | `DoomTP` |

#### `$bot`

The bot itself. Read in: anywhere.

| Field | Type | Description | Example |
|---|---|---|---|
| `{$bot.name}` | str | the bot's name | `doomtp-bot` |
| `{$bot.id}` | str | its Twitch id (empty for now) |  |
| `{$bot.version}` | str | running version | `1.4.0` |
| `{$bot.release}` | str | the tag or branch the image was built from; missing from a checkout | `v1.4.0` |
| `{$bot.commit}` | str | the short commit it was built from; missing from a checkout | `e8f7187` |
| `{$bot.uptime}` | int | seconds since this process started | `3600` |

#### `$now`

The time of the run, in the channel's timezone. Read in: anywhere.

| Field | Type | Description | Example |
|---|---|---|---|
| `{$now.iso}` | str | ISO 8601 time, to the second | `2026-10-17T21:30:00+02:00` |
| `{$now.unix}` | int | epoch seconds | `1792265400` |
| `{$now.date}` | str | the date, YYYY-MM-DD | `2026-10-17` |
| `{$now.time}` | str | the time, HH:MM | `21:30` |
| `{$now.weekday}` | str | day of the week, in English | `Saturday` |

#### `cmd`

The custom command being run. Read in: custom commands.

| Field | Type | Description | Example |
|---|---|---|---|
| `{cmd.name}` | str | its name | `hug` |
| `{cmd.alias}` | str | the name it was called by | `hug` |
| `{cmd.id}` | str | its id | `cc_3k9x` |
| `{cmd.version}` | int | its published version | `2` |
| `{cmd.owner}` | str | the publisher's login | `doomtp` |

#### `run`

This run. Read in: anywhere.

| Field | Type | Description | Example |
|---|---|---|---|
| `{run.id}` | str | the run's id, as the log and explain show it | `r_01JA2B3C` |
| `{run.trigger}` | str | what started it: chat, or a trigger type | `chat` |

#### `cooldown`

The cooldown that refused the command. Read in: `on_cooldown` callbacks.

| Field | Type | Description | Example |
|---|---|---|---|
| `{cooldown.command}` | str | the command, by its canonical name | `random` |
| `{cooldown.tier}` | str | the role whose cooldown rule applied | `everyone` |
| `{cooldown.tier_remaining}` | int | seconds left on the shared cooldown | `12` |
| `{cooldown.user_remaining}` | int | seconds left on this chatter's own cooldown | `0` |

#### `denied`

Why the command was refused. Read in: `on_denied` callbacks.

| Field | Type | Description | Example |
|---|---|---|---|
| `{denied.command}` | str | the command, by its canonical name | `timeout` |
| `{denied.required_role}` | str | the role it needs | `moderator` |
| `{denied.rank}` | int | the chatter's rank | `20` |

### 4.2 Event fields

Every trigger's `{event.type}` (str) is its trigger type. The rest depends on the type.

#### `redemption`

Fires when a viewer redeems a channel point reward.
`$chatter` is the viewer.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of the viewer | `80730642` |
| `{event.user.name}` | str | login of the viewer, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the viewer | `TestFromUser` |
| `{event.input}` | str | the text the viewer entered; empty when the reward asks for none | `hello` |
| `{event.text}` | str | the same as `input` | `hello` |
| `{event.reward.id}` | str | the reward's Twitch id | `92af127c-7326-4483-a52b-b0da0be61c01` |
| `{event.reward.title}` | str | the reward's title | `Hydrate` |
| `{event.reward.cost}` | int | its price in channel points | `500` |
| `{event.status}` | str | unfulfilled, fulfilled or canceled | `unfulfilled` |

#### `raid`

Fires when another channel raids this one.
`$chatter` is the raider.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of the raiding channel | `80730642` |
| `{event.user.name}` | str | login of the raiding channel, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the raiding channel | `TestFromUser` |
| `{event.viewers}` | int | how many came with the raid | `42` |

#### `sub`

Fires when a viewer subscribes for the first time.
`$chatter` is the subscriber.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of the subscriber | `80730642` |
| `{event.user.name}` | str | login of the subscriber, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the subscriber | `TestFromUser` |
| `{event.tier}` | str | sub tier: 1000, 2000 or 3000 (Prime counts as 1000) | `1000` |
| `{event.months}` | int | always 1 | `1` |

#### `resub`

Fires when a viewer renews, and shares it.
`$chatter` is the subscriber.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of the subscriber | `80730642` |
| `{event.user.name}` | str | login of the subscriber, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the subscriber | `TestFromUser` |
| `{event.tier}` | str | sub tier: 1000, 2000 or 3000 (Prime counts as 1000) | `1000` |
| `{event.months}` | int | total months subscribed | `14` |
| `{event.streak_months}` | int | months in a row; missing when they don't share it | `3` |
| `{event.prime}` | bool | paid with Prime | `false` |
| `{event.gift}` | bool | the sub was a gift | `false` |
| `{event.text}` | str | the message they added | `14 months!` |

#### `gift_sub`

Fires when a viewer gifts a sub to one person (a community gift of five fires this five times).
`$chatter` is the gifter.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of the gifter; the whole `user` is missing when anonymous | `80730642` |
| `{event.user.name}` | str | login of the gifter, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the gifter | `TestFromUser` |
| `{event.recipient.id}` | str | Twitch user id of who got it | `80730642` |
| `{event.recipient.name}` | str | login of who got it, lowercase | `testfromuser` |
| `{event.recipient.display}` | str | display name of who got it | `TestFromUser` |
| `{event.tier}` | str | sub tier: 1000, 2000 or 3000 (Prime counts as 1000) | `1000` |
| `{event.months}` | int | months gifted | `1` |

#### `cheer`

Fires when a viewer cheers bits.
`$chatter` is the cheerer.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of the cheerer; the whole `user` is missing when anonymous | `80730642` |
| `{event.user.name}` | str | login of the cheerer, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the cheerer | `TestFromUser` |
| `{event.bits}` | int | how many | `250` |
| `{event.text}` | str | the cheer's message | `This is a test event.` |
| `{event.anonymous}` | bool | cheered anonymously | `false` |

#### `follow`

Fires when a viewer follows the channel.
`$chatter` is the new follower.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of the new follower | `80730642` |
| `{event.user.name}` | str | login of the new follower, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the new follower | `TestFromUser` |
| `{event.followed_at}` | int | when, in epoch milliseconds | `1767268800000` |

#### `stream_online`

Fires when the bot's once-a-minute check sees the stream go live.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.live}` | bool | always true | `true` |
| `{event.title}` | str | stream title, as Twitch lists it | `late night doom` |
| `{event.game}` | str | category | `DOOM` |
| `{event.viewers}` | int | viewer count | `12` |
| `{event.started_at}` | str | ISO time the stream started | `2026-10-17T20:00:00Z` |
| `{event.channel.login}` | str | the channel | `doomtp` |

#### `stream_offline`

Fires when the same check sees the stream is no longer live.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.live}` | bool | always false | `false` |
| `{event.channel.login}` | str | the channel | `doomtp` |

#### `pyramid`

Fires when a chatter builds an emote pyramid, row by row, or breaks one.
`$chatter` is the builder, or the breaker on broken.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.phase}` | str | step, complete or broken | `step` |
| `{event.direction}` | str | up or down on a step; empty otherwise | `up` |
| `{event.token}` | str | the emote | `Kappa` |
| `{event.width}` | int | this row's width | `3` |
| `{event.peak}` | int | the widest row so far | `3` |
| `{event.user.id}` | str | Twitch user id of the builder | `80730642` |
| `{event.user.name}` | str | login of the builder, lowercase | `testfromuser` |
| `{event.user.display}` | str | display name of the builder | `TestFromUser` |
| `{event.breaker.id}` | str | Twitch user id of who broke it; only on broken | `80730642` |
| `{event.breaker.name}` | str | login of who broke it, lowercase | `testfromuser` |
| `{event.breaker.display}` | str | display name of who broke it | `TestFromUser` |
| `{event.by_bot}` | bool | the bot finished or broke it | `false` |
| `{event.self_broken}` | bool | the builder broke their own pyramid | `false` |
| `{event.pyramid_id}` | str | the message id of its first row; ties its rows together | `b7e0f1c2-4d3a-4e5f-9a8b-1c2d3e4f5a6b` |

#### `timer`

Fires when its interval passes (every 60s at the least), counted in chat lines and live time as set.

No fields beyond `type`.

#### `cron`

Fires when the clock matches its cron schedule.

No fields beyond `type`.

#### `listener`

Fires when a chat line matches its regex.
`$chatter` is the author.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.user.id}` | str | Twitch user id of who wrote it | `80730642` |
| `{event.user.name}` | str | login of who wrote it, lowercase | `testfromuser` |
| `{event.message}` | str | the line | `anyone playing doom?` |

#### `bot_online`

Fires when the bot is in chat: at start, after an update, or after its Twitch connection was started again. A channel hears it at most every 15 minutes.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.reason}` | str | start, update or reconnect | `start` |
| `{event.cause}` | str | on reconnect, why the connection was restarted | `not hearing chat` |
| `{event.down_s}` | int | seconds it was gone; missing when not known | `40` |
| `{event.version}` | str | the running version | `1.4.0` |

#### `bot_updated`

Fires when the version changed since the last run; fires just before bot_online.

| Field | Type | Description | Example |
|---|---|---|---|
| `{event.version}` | str | the new version | `1.4.0` |
| `{event.previous}` | str | the version before | `1.3.2` |

#### `bot_joined`

Fires when the bot is asked into the channel (!join or the website), not at every start.

No fields beyond `type`.

#### `bot_modded`

Fires when the bot's hourly check sees it became a moderator here.

No fields beyond `type`.

#### `bot_unmodded`

Fires when the same check sees it stopped being one.

No fields beyond `type`.
<!-- reference:end -->

## 5. Reserved names

- **Roots:** `_`, `_N`, `arg`, `args`, `$chatter`, `$channel`, `$publisher`, `$bot`, `$now`, `cmd`, `event`, `match`, `cooldown`, `denied`, `run`, and the variable namespaces of §3. The expression keywords `and`, `or`, `not`, `in`, `true` and `false` are never a reference.
- **Variable names can't be** (a closed list, `runtime/namespaces.py`):
  - `data`, `code`, `message`, `public`, `root`, in every namespace;
  - `chatter` under `channel.`, `publisher.` and `publisher.channel.`, and `channel` under `publisher.`, because those spell a longer namespace.

  Since the bot's fields moved behind `$`, names like `channel.title` are ordinary variables, separate from `$channel.title`.
- **Variable name format:** `[a-z][a-z0-9_]{0,31}`.

## 6. Fallbacks

`{x ?? default}`: if `x` is missing, empty or fails type validation, the default is used instead. The default can be a literal or another placeholder: `{chatter.location ?? {channel.location ?? Lisbon}}`. In an expression, `??` is an operator too: `{(arg.1 ?? "") == "add"}`.
