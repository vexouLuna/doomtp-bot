"""`stream_announced`: the stream each channel last went live with, so a restart doesn't announce it again.

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Helix's `started_at` tells one stream from the next; the poller's live map is memory-only.
    op.execute(
        """
        CREATE TABLE stream_announced (
            channel_id   text PRIMARY KEY,
            started_at   text NOT NULL,
            announced_at bigint NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE stream_announced")
