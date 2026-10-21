"""The channel settings, in one place (ADR-0026): which columns of `channels` a person may change, and
the lowest rank that may change each one, in chat and on the API alike.

The bot also writes a few columns itself (joining, leaving, the capability probe); nobody sets those.
A test holds `ChannelSettings`, the API's patch body and this table to the same names.
"""

from __future__ import annotations

from doomtp_bot.policy.roles import BROADCASTER_RANK, MODERATOR_RANK

#: Setting name → the lowest rank that may change it. The chat settings are for moderators; logging,
#: backfill and the "who may" roles for the broadcaster.
CHANNEL_SETTINGS: dict[str, int] = {
    "prefix": MODERATOR_RANK,
    "quiet_errors": MODERATOR_RANK,
    "cc_edit_notice": MODERATOR_RANK,
    "log_enabled": BROADCASTER_RANK,
    "history_backfill": BROADCASTER_RANK,
    "public_log": MODERATOR_RANK,
    "reply_hold_ms": MODERATOR_RANK,
    "timezone": MODERATOR_RANK,
    "automod_action": MODERATOR_RANK,
    "automod_timeout_s": MODERATOR_RANK,
    "channel_var_write_role": BROADCASTER_RANK,
    "grant_min_role": BROADCASTER_RANK,
    "publish_min_role": BROADCASTER_RANK,
    "create_min_role": BROADCASTER_RANK,
    "var_admin_role": BROADCASTER_RANK,
    "bot_enabled": MODERATOR_RANK,  # silencing the bot is a safety switch any moderator may pull
    "slur_filter": MODERATOR_RANK,
}

#: Columns only the bot writes: whether it is in the channel, and what the channel lets it do.
BOT_COLUMNS = frozenset({"active", "status", "tier", "capabilities"})

#: Every column `PolicyRepository.set_channel_field` accepts.
CHANNEL_COLUMNS = BOT_COLUMNS | CHANNEL_SETTINGS.keys()
