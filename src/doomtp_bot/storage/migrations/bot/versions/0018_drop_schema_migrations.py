"""Drop `schema_migrations`, the table the SQL runner kept before Alembic (ADR-0014, ADR-0022).

Revisions 0001–0005 kept it in step so an image from before ADR-0022 could still read it after a rollback.
Every deployment is past those images now, so it goes. The downgrade puts it back as those revisions left
it, for a rollback to an image from before this one.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

from doomtp_bot.storage.schema import record_legacy

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

LEGACY = [
    (1, "0001_init.sql"),
    (2, "0002_quotes.sql"),
    (3, "0003_variable_limits.sql"),
    (4, "0004_more_variable_limits.sql"),
    (5, "0005_internal_and_system_packs.sql"),
]


def upgrade() -> None:
    op.execute("DROP TABLE schema_migrations")


def downgrade() -> None:
    for version, name in LEGACY:
        record_legacy(version, name)
