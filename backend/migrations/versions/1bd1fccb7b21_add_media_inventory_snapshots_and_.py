"""add media inventory snapshots and change ledger

Revision ID: 1bd1fccb7b21
Revises: f2b83d91a640
Create Date: 2026-10-21 23:09:52.224200
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1bd1fccb7b21"
down_revision: str | Sequence[str] | None = "f2b83d91a640"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_inventory_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("previous_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_type", sa.String(length=24), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("net_bytes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("media_inventory_changes", schema=None) as batch_op:
        batch_op.create_index(
            "ix_media_inventory_changes_source_time",
            ["integration_id", "observed_at"],
            unique=False,
        )

    op.create_table(
        "media_inventory_snapshots",
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("integration_id"),
    )


def downgrade() -> None:
    op.drop_table("media_inventory_snapshots")
    with op.batch_alter_table("media_inventory_changes", schema=None) as batch_op:
        batch_op.drop_index("ix_media_inventory_changes_source_time")

    op.drop_table("media_inventory_changes")
