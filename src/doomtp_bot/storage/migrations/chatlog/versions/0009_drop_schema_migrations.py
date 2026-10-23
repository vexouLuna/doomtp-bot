"""Drop `schema_migrations`, the table the SQL runner kept before Alembic (ADR-0014, ADR-0022).

Revision 0001 kept it so an image from before ADR-0022 could still read it after a rollback. Every
deployment is past those images now, so it goes. The downgrade puts it back as 0001 left it, for a
rollback to an image from before this one.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

from doomtp_bot.storage.schema import record_legacy

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE schema_migrations")


def downgrade() -> None:
    record_legacy(1, "0001_init.sql")
