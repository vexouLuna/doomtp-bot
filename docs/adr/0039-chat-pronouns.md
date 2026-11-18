# ADR-0039: Chatters' pronouns, looked up by the bot

**Status:** Accepted, 2026-11-18
**Date:** 2026-11-18
**Deciders:** Project owner

## Context

The chat overlay (doomtp-web, ADR-0038's consumer) can show a chatter's pronouns. Nothing in the
workspace stores them; the ones chatters set live in alejo.io's pronoun service, the one the Twitch Chat
Pronouns extension and Chatterino's and FFZ's add-ons read. The first plan (CH-4 a) had the overlay call it
from the browser. doomtp-web's rule is that its data comes only from the bot's JSON API, and the service
publishes no API reference or terms, so the owner reversed that (CO-4 c, 2026-11-18): **the bot serves the
pronouns**, looking them up and caching them, and the overlay asks the bot.

What the service answers (read from live responses, 2026-11-18):

    GET https://api.pronouns.alejo.io/v1/pronouns        -> {<id>: {name, subject, object, singular}}
    GET https://api.pronouns.alejo.io/v1/users/{login}   -> {channel_id, channel_login, pronoun_id, alt_pronoun_id}, 404 for nobody

## Decision

`GET /api/v2/channels/{login}/pronouns?user=<login>&user=<login>…` (`api/v2_pronouns.py`), backed by
`PronounService` (`pronouns.py`).

- **Access:** the live feed's terms, the log's reader: anyone while the channel's public log is on
  (`public_log` and `log_enabled`), the channel's moderators always; 401 otherwise. A public request counts as
  one read of the log's per-address read limit (30 a minute), so the overlay batches chatters.
- **Request:** `user` repeated, 1 to 50, each a Twitch login (`[A-Za-z0-9_]{1,25}`, a leading `@` dropped,
  any case). Anything else is a 422 `invalid`. An unknown channel is a 404 `unknown_channel` for a caller who
  may see it.
- **Response:** `{items, next_cursor}`, one page, `next_cursor` always null. One item per login asked, in
  order, duplicates once: `{login, status, display}`. `status` is `set` (`display` holds them: `"She/Her"`,
  two joined as `"He/Him · They/Them"`, a singular one such as `"Any"` alone), `none` (they set none, or an id
  the table doesn't know; `display` null) or `unknown` (the service didn't answer in time; `display` null).
- **The service never breaks chat.** Each upstream request has a 3 s timeout; at most 8 run at once across
  every caller; a network error, timeout or non-200/404 answer makes that chatter `unknown`, and the route
  still answers 200. `unknown` is not cached, so the next ask tries again.
- **Caching:** the pronoun table a day (a stale one is kept if a refresh fails), each chatter's answer an
  hour (`set` and `none` alike), at most 10,000 chatters, least recently used out first. A busy chat costs
  the service one request per chatter an hour.
- **What goes upstream:** only the chatter's login, with `User-Agent: doomtp-bot`.
- No migration, no new dependency (aiohttp is already in use). `scripts/dev_api.py` answers from a stand-in.

## Consequences

- The overlay needs no third-party call, no CORS, and inherits the bot's caching.
- The bot depends on an undocumented service. If its shape changes, chatters come back `none` or
  `unknown` and chat is unaffected; the fix is in `pronouns.py` alone.
- One cache in the bot process, lost at a restart.
