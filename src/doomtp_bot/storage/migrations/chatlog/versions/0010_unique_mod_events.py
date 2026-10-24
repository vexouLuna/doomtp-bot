"""One row per moderation event: drop the copies already stored and refuse new ones.

`mod_events` had no unique key and its insert no `ON CONFLICT`, so every backfill over a restart gap
(it asks from 5 s before the gap, ADR-0008) and every EventSub redelivery stored the same deletion,
timeout or clear again; #moonmoon showed actions two and three times. A deletion is one per message;
anything else is one per channel, type, target and time. The copies are deleted keeping the earliest
row, and the downgrade only drops the indexes: the deleted copies were never real events.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM mod_events m USING ("
        "  SELECT id, row_number() OVER ("
        "    PARTITION BY channel_id, message_id ORDER BY at, id"
        "  ) AS n FROM mod_events WHERE type = 'delete' AND message_id IS NOT NULL"
        ") d WHERE m.id = d.id AND d.n > 1"
    )
    op.execute(
        "DELETE FROM mod_events m USING ("
        "  SELECT id, row_number() OVER ("
        "    PARTITION BY channel_id, type, target_user_id, at ORDER BY id"
        "  ) AS n FROM mod_events WHERE type <> 'delete' OR message_id IS NULL"
        ") d WHERE m.id = d.id AND d.n > 1"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_mod_events_message ON mod_events(channel_id, message_id)"
        " WHERE type = 'delete' AND message_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_mod_events_event ON mod_events(channel_id, type, target_user_id, at)"
        " NULLS NOT DISTINCT WHERE type <> 'delete' OR message_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_mod_events_event")
    op.execute("DROP INDEX ux_mod_events_message")
