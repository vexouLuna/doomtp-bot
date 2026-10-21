# ADR-0036: Remove the View as preview

**Status:** Accepted, 2026-10-21 (supersedes [ADR-0030](0030-view-as-preview-header.md))
**Date:** 2026-10-21
**Deciders:** Project owner

## Context

ADR-0030 let an admin session send `X-View-As` and get the API's answers as someone else would. That
put a preview branch into every method of `Caller` in `api/access.py` (`channel_role`, `rank_in`,
`is_broadcaster_of`), into `authenticate`, `require_session` and `GET /session`. Every one of those is an
authorization decision, so a mistake in the preview code is a mistake in who may read what. An
architecture review (2026-10-21) counted it as the largest piece of authorization surface added for a
convenience, and doomtp-web had not yet adopted it (ADR-0030 item 3 was still open).

## Options

1. **Keep it** and test it as an authorization path in its own right.
2. **Remove it.** An admin who wants to see what a moderator sees signs in as a test account.
3. **Move the preview back into the site** (ADR-0030's option 1), with the drift that ADR named.

## Decision

Option 2, the project owner's call. `X-View-As`, `ViewAs`, `previewing`, `refuse_writes` and the view
branches in `Caller` are gone; `GET /api/v1/session` no longer has `view_as`.

For one transition, a request that still carries `X-View-As` gets 400 from `access.current_session`
rather than being answered as the admin: a site that thinks it is previewing must not show an admin's
data as someone else's or send writes it believes are read-only.

## Consequences

- `api/access.py` loses about 140 lines and every `Caller` method has one path.
- doomtp-web's preview stops working. If it ever sends the header, the bot says so with a 400.
- ADR-0030 item 3 (doomtp-web adopting the header) is withdrawn.

## Action Items

1. [x] Remove `X-View-As` from `api/access.py` and `api/routes/session.py`, and `tests/test_api_view_as.py`;
   refuse the header with 400 (`tests/test_api_web.py`).
2. [x] Documentation: architecture §11, the roadmap, ADR-0030's status.
3. [ ] doomtp-web removes its View as control.
4. [ ] Remove the 400 guard once doomtp-web no longer sends the header.
