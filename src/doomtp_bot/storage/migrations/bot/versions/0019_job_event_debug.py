"""vex-platform 0.9.0's jobs schema 4: the `debug` event level (ADR-0027).

0.9.0 lets a step log `ctx.log.debug` lines, which the runtime drops until the check allows them, and
keeps only the newest progress report per run and step, by a partial index.

Revision ID: 0019
Revises: 0018
"""

from alembic import op
from vex_platform import migrations

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrations.apply(op, migrations.jobs_sql(4, schema="jobs"))


def downgrade() -> None:
    op.execute("DROP INDEX jobs.job_run_events_progress")
    op.execute("DELETE FROM jobs.job_run_events WHERE level = 'debug'")
    op.execute("ALTER TABLE jobs.job_run_events DROP CONSTRAINT job_run_events_level_check")
    op.execute(
        "ALTER TABLE jobs.job_run_events ADD CONSTRAINT job_run_events_level_check"
        " CHECK (level IN ('info', 'warning', 'error'))"
    )
