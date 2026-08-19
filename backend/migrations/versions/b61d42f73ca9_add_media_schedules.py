"""add normalized media schedules

Revision ID: b61d42f73ca9
Revises: a2c91d84e630
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b61d42f73ca9"
down_revision: str | Sequence[str] | None = "a2c91d84e630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("instance_name", sa.String(length=160), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("parent_title", sa.String(length=300), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("release_type", sa.String(length=40), nullable=False),
        sa.Column("monitored", sa.Boolean(), nullable=False),
        sa.Column("has_file", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_id", "external_id", name="uq_media_schedule_source"),
    )
    for column in ("integration_id", "scheduled_at", "provider", "instance_name"):
        op.create_index(f"ix_media_schedules_{column}", "media_schedules", [column])
    op.execute(
        "UPDATE media_activities SET title = 'Unknown title' "
        "WHERE title LIKE '/%' OR instr(title, char(92)) > 0"
    )
    op.execute(
        "UPDATE integrations SET config = json_remove(config, '$.last_collected_at') "
        "WHERE provider IN ('sonarr', 'radarr')"
    )


def downgrade() -> None:
    op.drop_table("media_schedules")
