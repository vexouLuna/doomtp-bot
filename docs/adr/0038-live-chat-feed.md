# ADR-0038: A live chat feed as server-sent events

**Status:** Accepted, 2026-11-17
**Date:** 2026-11-17
**Deciders:** Project owner

## Context

An OBS chat overlay (doomtp-web, `/overlay/:login`) needs a channel's chat as it happens: messages with
their emotes, badges and colour, and the removals that take messages off screen. The bot had no push of any
kind. The chat log is no low-latency source: its writer batches. Every chat event passes through one place,
`Dispatcher.handle`. An OBS browser source carries no sign-in.

The owner decided three things (2026-11-17):

- **Transport: server-sent events** (CH-2 a). One-way is all an overlay needs, it adds no dependency, and it
  is a plain GET under `/api/*`, the proxy rule the bot already has.
- **Access: the log's rule** (CH-3 a). Public for channels whose public log is on (`public_log` and
  `log_enabled`), the channel's moderators always; a cap on open streams per client address.
- **Removals are pushed** so a deleted, timed-out or cleared message disappears from the overlay. Third-party
  emotes are the overlay's business, looked up in the browser. Pronouns were too, until ADR-0039 moved
  them to the bot (`/api/v2/channels/{login}/pronouns`).

## Decision

`GET /api/v2/channels/{login}/chat/live`, `text/event-stream` (`api/v2_live.py`), fed by an in-process hub
(`chatlog/live.py`, `LiveFeed`) that the dispatcher calls beside the log writer.

### Events

| Event | Data |
|---|---|
| `ready` | `{"channel": login}`, once, first |
| `message` | the log's v2 `MessageEntry` (`kind: "message"`): `id`, `at`, `user {id, login, display_name}`, `text`, `fragments` (emotes with `emote_id`), `badges` (`set_id`, `id`), `color`, `bits`, `message_type`, `reply_parent_id`, `reply_parent_user`, `reward_id`, `source_channel_id`, `is_self`, `is_command`, `source`, `received_at`, `deleted_at`/`cleared_at` (null), `run` (null), `raw` (null) |
| `notification` | the log's v2 `NotificationEntry` (`kind: "notification"`): a sub, resub, gift, raid, announcement… |
| `delete` | `{message_id, user_id, at}`: one message deleted |
| `user_clear` | `{user_id, duration_s, at}`: a user's messages cleared (timeout or ban; `duration_s` null when Twitch doesn't say) |
| `chat_clear` | `{at}`: the whole chat cleared |
| `end` | `{reason}`: the bot closes the stream right after. `log_private` (the public log went off; public streams only), `too_slow` (the client fell behind), `shutdown` (the bot is stopping) |

Times are ISO 8601 UTC, as everywhere in v2. Each `data:` is one line of JSON. Messages and notifications are
the log's own entries, built by the same code (`chatlog/timeline.py`) and serialized by the same models, so
`@vex/platform-web`'s `toChatLine` renders a live message as it renders a logged one. A message is sent as
typed, with `is_command` as the log has it (a line forwarded from an alternate prefix counts as a command,
spec §2.1.1), so an overlay can hide commands. The bot's own messages come through with `is_self`.

### Keep-alive, reconnects, limits

- The stream opens with `retry: 3000`, and a comment line (`: keep-alive`) goes out after 15 s without an
  event, so proxies don't time it out.
- **No replay.** Events carry no `id:`, and `Last-Event-ID` is ignored: a reconnect starts from now. The hub
  stores nothing, and an overlay that wants what came before reads the recent log
  (`/api/v2/channels/{login}/log`) first, as a page of the same entries.
- **Limits.** 4 open streams per client address, 500 in all; one more is a 429 problem with
  `Retry-After: 30`. A public connect also counts as one read of the log's per-address read limit.
  The address is uvicorn's client address, right behind the proxy only when `WEB_FORWARDED_ALLOW_IPS`
  trusts it (the same caveat as the login limiter).
- **Slow clients are dropped, not buffered.** Each stream has a queue of 256 events. One that fills is
  emptied and gets `end` `too_slow`.
- **The log going private mid-stream** ends every public stream on the channel with `end` `log_private`
  within a second, whether or not chat is moving. Moderators' streams go on.
- **A client that goes away** cancels its stream, which frees its slot (`LiveFeed.close` in the generator's
  `finally`).
- **Shutdown.** uvicorn waits for open responses before it stops, so each stream checks once a second
  whether the server is exiting and ends itself with `end` `shutdown`.

### Proxying

The response sets `Cache-Control: no-cache` and `X-Accel-Buffering: no`, which tells an nginx-style proxy
not to buffer it. The bot's own middleware is pure ASGI apart from `LookupOrigins`, a Starlette
`BaseHTTPMiddleware`, which streams; a run of `scripts/dev_api.py` showed events arriving as they happened
through the whole stack. Whether the production proxy streams unbuffered is not visible in this repo and
must be checked there.

## Consequences

- The overlay needs one GET per channel, no token, no CORS (same origin as the bot).
- No history: a dropped connection loses what was said while it was down unless the overlay re-reads the log.
- A channel can't offer a public overlay while keeping its log private. A per-channel overlay token was the
  alternative (CH-3 b) and can come later.
- `scripts/dev_api.py` feeds made-up chat into the hub, so the overlay can be built without Twitch.
