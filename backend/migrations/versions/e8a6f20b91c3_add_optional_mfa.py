"""add optional authenticator MFA

Revision ID: e8a6f20b91c3
Revises: c7e4b1a9d2f0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a6f20b91c3"
down_revision: str | Sequence[str] | None = "c7e4b1a9d2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable columns leave every existing account opted out of MFA.
    op.add_column("users", sa.Column("mfa_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_pending_secret", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("mfa_pending_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("mfa_last_step", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("mfa_recovery_hashes", sa.JSON(), nullable=True))


def downgrade() -> None:
    for name in (
        "mfa_recovery_hashes",
        "mfa_last_step",
        "mfa_pending_expires_at",
        "mfa_pending_secret",
        "mfa_secret",
    ):
        op.drop_column("users", name)
