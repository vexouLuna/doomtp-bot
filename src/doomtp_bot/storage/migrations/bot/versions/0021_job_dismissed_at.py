"""vex-platform 0.10's jobs schema 5: `dismissed_at` on job runs, so a dismissed failure stops counting (ADR-0027).

Additive: the counts skip dismissed runs by default, and a partial index serves them.

Revision ID: 0021
Revises: 0020
"""

from alembic import op
from vex_platform import migrations

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrations.apply(op, migrations.jobs_sql(5, schema="jobs"))


def downgrade() -> None:
    op.execute("DROP INDEX jobs.job_runs_undismissed")
    op.execute("ALTER TABLE jobs.job_runs DROP COLUMN dismissed_at")
