"""vex-platform 0.10's jobs schema 6: the `job_schedules` table and `schedule_id` on job runs (ADR-0027).

Additive. The bot declares no schedulable kinds yet, so the table stays empty.

Revision ID: 0022
Revises: 0021
"""

from alembic import op
from vex_platform import migrations

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrations.apply(op, migrations.jobs_sql(6, schema="jobs"))


def downgrade() -> None:
    op.execute("DROP INDEX jobs.job_runs_schedule")
    op.execute("ALTER TABLE jobs.job_runs DROP COLUMN schedule_id")
    op.execute("DROP TABLE jobs.job_schedules")
