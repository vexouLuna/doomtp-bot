"""`channels.alt_prefixes`: extra command signs, each forwarding a list of commands (or all) to the main sign.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON text like `capabilities`: [{"prefix": "?", "commands": ["ping"] | "all"}]. Additive with a
    # default, so the old bot keeps running while this migrates.
    op.execute("ALTER TABLE channels ADD COLUMN alt_prefixes text NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    op.execute("ALTER TABLE channels DROP COLUMN alt_prefixes")
