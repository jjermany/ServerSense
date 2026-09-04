"""store Docker state change time

Revision ID: c7e4b1a9d2f0
Revises: d4a91c28f6b2
Create Date: 2026-09-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e4b1a9d2f0"
down_revision: str | Sequence[str] | None = "d4a91c28f6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("docker_samples", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True)
        )

    # Only the newest snapshot is read by the UI. Seed it without scanning or
    # rewriting the full retained Docker history; the collector carries the
    # value forward accurately from the next sample onward.
    op.execute(
        """
        UPDATE docker_samples
        SET state_changed_at = CASE
            WHEN status = 'running' AND started_at IS NOT NULL THEN started_at
            ELSE timestamp
        END
        WHERE timestamp = (SELECT MAX(timestamp) FROM docker_samples)
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("docker_samples", schema=None) as batch_op:
        batch_op.drop_column("state_changed_at")
