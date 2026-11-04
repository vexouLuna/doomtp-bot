"""A deleted command's or pack's name can be used again.

Deleting is a soft delete that keeps the row, and `UNIQUE (owner_user_id, name)` counted it, so
`cc add foo` after `cc rm foo` failed for good. The names are now unique among the rows not deleted.
A deleted command's alias links go too (the service deletes them from now on), so the alias is free.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TABLES = (("custom_commands", "ux_custom_commands_owner_name"), ("custom_command_packs", "ux_cc_packs_owner_name"))


def upgrade() -> None:
    op.execute(
        "DELETE FROM custom_command_links l USING custom_commands c WHERE l.command_id = c.id AND c.status = 'deleted'"
    )
    for table, index in TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_owner_user_id_name_key")
        op.execute(f"CREATE UNIQUE INDEX {index} ON {table}(owner_user_id, name) WHERE status <> 'deleted'")


def downgrade() -> None:
    for table, index in TABLES:
        op.execute(f"DROP INDEX {index}")
        # A name reused since has a deleted row beside it; rename the deleted ones so the old rule holds.
        op.execute(
            f"UPDATE {table} t SET name = t.name || '~' || t.id WHERE t.status = 'deleted' AND EXISTS"
            f" (SELECT 1 FROM {table} o WHERE o.owner_user_id = t.owner_user_id AND o.name = t.name AND o.id <> t.id)"
        )
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_owner_user_id_name_key UNIQUE (owner_user_id, name)")
