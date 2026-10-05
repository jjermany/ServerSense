"""Index bounded container grace-period evidence queries.

Revision ID: f2b83d91a640
Revises: 81160105e312
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2b83d91a640"
down_revision: str | Sequence[str] | None = "81160105e312"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_docker_samples_container_timestamp", "docker_samples", ["container_id", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_docker_samples_container_timestamp", table_name="docker_samples")
