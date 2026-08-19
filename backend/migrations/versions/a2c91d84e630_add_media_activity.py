"""add normalized media activity

Revision ID: a2c91d84e630
Revises: 00789e0e53f4
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2c91d84e630"
down_revision: str | Sequence[str] | None = "00789e0e53f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("instance_name", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("parent_title", sa.String(length=300), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("quality", sa.String(length=100), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("is_upgrade", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_id", "external_id", name="uq_media_activity_source"),
    )
    for column in ("integration_id", "occurred_at", "provider", "instance_name", "event_type"):
        op.create_index(f"ix_media_activities_{column}", "media_activities", [column])


def downgrade() -> None:
    op.drop_table("media_activities")
