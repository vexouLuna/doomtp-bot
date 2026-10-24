"""One row per moderation event: drop the copies already stored and refuse new ones.

`mod_events` had no unique key and its insert no `ON CONFLICT`, so every backfill over a restart gap
(it asks from 5 s before the gap, ADR-0008) and every EventSub redelivery stored the same deletion,
timeout or clear again; #moonmoon showed actions two and three times. A deletion is one per message;
anything else is one per channel, type, target and time. The copies are deleted keeping the earliest
row, and the downgrade only drops the indexes: the deleted copies were never real events.

A timeout seen live through EventSub (`timeout`/`ban`, with the moderator) and again by backfill from
the IRC log (`user_clear`) carries two clocks, so their `at` can differ by a moment. Rows for the same
channel and target within `ACTION_WINDOW_MS` whose types can be the same action are one action: the
earliest is kept and takes the other's type and moderator. From here on the writer matches them the
same way, by the new `duration_s` too where both sides know it.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

ACTION_WINDOW_MS = 2000  # the writer's window too (chatlog/writer.py)
_ACTIONS = "('user_clear', 'timeout', 'ban')"


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
        "CREATE TEMP TABLE action_copies ON COMMIT DROP AS"
        " SELECT DISTINCT ON (b.id) b.id AS copy, a.id AS kept, b.type, b.moderator_user_id"
        " FROM mod_events a JOIN mod_events b ON a.channel_id = b.channel_id"
        "  AND a.target_user_id = b.target_user_id AND (a.at, a.id) < (b.at, b.id)"
        f"  AND b.at - a.at <= {ACTION_WINDOW_MS}"
        f"  AND a.type IN {_ACTIONS} AND b.type IN {_ACTIONS}"
        "  AND (a.type = b.type OR 'user_clear' IN (a.type, b.type))"
        " ORDER BY b.id, a.at, a.id"
    )
    op.execute("DELETE FROM mod_events WHERE id IN (SELECT copy FROM action_copies)")
    op.execute(
        "UPDATE mod_events m SET"
        "  type = CASE WHEN m.type = 'user_clear' THEN COALESCE(c.type, m.type) ELSE m.type END,"
        "  moderator_user_id = COALESCE(m.moderator_user_id, c.moderator)"
        " FROM (SELECT kept, max(type) FILTER (WHERE type <> 'user_clear') AS type,"
        "  max(moderator_user_id) AS moderator FROM action_copies GROUP BY kept) c"
        " WHERE m.id = c.kept"
    )
    op.execute("ALTER TABLE mod_events ADD COLUMN duration_s INTEGER")
    op.execute(
        "CREATE UNIQUE INDEX ux_mod_events_message ON mod_events(channel_id, message_id)"
        " WHERE type = 'delete' AND message_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_mod_events_event ON mod_events(channel_id, type, target_user_id, at)"
        " NULLS NOT DISTINCT WHERE type <> 'delete' OR message_id IS NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mod_events DROP COLUMN duration_s")
    op.execute("DROP INDEX ux_mod_events_event")
    op.execute("DROP INDEX ux_mod_events_message")
