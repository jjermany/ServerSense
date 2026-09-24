"""add media correlation identifiers

Revision ID: 81160105e312
Revises: e8a6f20b91c3
Create Date: 2026-09-24 10:41:48.383623
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "81160105e312"
down_revision: str | Sequence[str] | None = "e8a6f20b91c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_activities", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provider_media_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("download_id_hash", sa.String(length=64), nullable=True))

    # Force one bounded history refresh so existing normalized rows can gain
    # the provider identifiers without persisting raw history payloads.
    op.execute(
        "UPDATE integrations SET config = json_remove(config, '$.last_collected_at') "
        "WHERE provider IN ('sonarr', 'radarr')"
    )


def downgrade() -> None:
    with op.batch_alter_table("media_activities", schema=None) as batch_op:
        batch_op.drop_column("download_id_hash")
        batch_op.drop_column("provider_media_id")
