"""scheduled executions (cron double-fire guard)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25 21:05:00

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_executions",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_workflow_executions_schedule",
        "workflow_executions",
        ["definition_id", "scheduled_for"],
        unique=True,
        postgresql_where=sa.text("scheduled_for IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_workflow_executions_schedule", table_name="workflow_executions")
    op.drop_column("workflow_executions", "scheduled_for")
