"""`channels.bot_enabled` and `channels.slur_filter`.

`bot_enabled` off keeps the bot in the channel, logging, but it never says anything there: the outbox
drops every message and nothing runs. `slur_filter` is how much of the built-in slur list applies to what
the bot says; `base` is always applied, the other levels add to it (filters/slurs.py).

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE channels ADD COLUMN bot_enabled boolean NOT NULL DEFAULT true")
    op.execute(
        "ALTER TABLE channels ADD COLUMN slur_filter text NOT NULL DEFAULT 'base'"
        " CHECK (slur_filter IN ('base', 'strict', 'strictest'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE channels DROP COLUMN slur_filter")
    op.execute("ALTER TABLE channels DROP COLUMN bot_enabled")
