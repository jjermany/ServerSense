"""add durable SENSE jobs and message provenance

Revision ID: d4a91c28f6b2
Revises: 3d4e5f607182
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a91c28f6b2"
down_revision: str | Sequence[str] | None = "3d4e5f607182"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.add_column(sa.Column("summary", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("summary_updated_at", sa.DateTime(timezone=True)))
    with op.batch_alter_table("ai_messages") as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(40), nullable=False, server_default="user")
        )
        batch_op.add_column(sa.Column("provider", sa.String(80)))
        batch_op.add_column(sa.Column("model", sa.String(200)))
        batch_op.add_column(sa.Column("references", sa.JSON(), nullable=False, server_default="{}"))
    op.execute("UPDATE ai_messages SET source = 'sense_ai' WHERE role = 'assistant'")

    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            sa.Integer(),
            sa.ForeignKey("ai_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "response_message_id",
            sa.Integer(),
            sa.ForeignKey("ai_messages.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("intent", sa.String(40), nullable=False, server_default="analysis"),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("context_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("partial_response", sa.Text(), nullable=False, server_default=""),
        sa.Column("tools_used", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notify_on_completion", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "completion_notification_sent", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("notification_id", sa.Integer()),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("first_token_at", sa.DateTime(timezone=True)),
        sa.Column("backgrounded_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("timed_out_at", sa.DateTime(timezone=True)),
        sa.Column("interrupted_at", sa.DateTime(timezone=True)),
        sa.Column("generated_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_jobs_user_id", "ai_jobs", ["user_id"])
    op.create_index("ix_ai_jobs_conversation_id", "ai_jobs", ["conversation_id"])
    op.create_index("ix_ai_jobs_status", "ai_jobs", ["status"])
    op.create_index("ix_ai_jobs_user_message_id", "ai_jobs", ["user_message_id"])

    op.create_table(
        "in_app_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("ai_jobs.id", ondelete="CASCADE")),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        ),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("ai_messages.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(40), nullable=False, server_default="ai_job_complete"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("preview", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_in_app_notifications_user_id", "in_app_notifications", ["user_id"])
    op.create_index("ix_in_app_notifications_job_id", "in_app_notifications", ["job_id"])
    op.create_index("ix_in_app_notifications_created_at", "in_app_notifications", ["created_at"])
    op.create_index("ix_in_app_notifications_read_at", "in_app_notifications", ["read_at"])


def downgrade() -> None:
    op.drop_table("in_app_notifications")
    op.drop_table("ai_jobs")
    with op.batch_alter_table("ai_messages") as batch_op:
        batch_op.drop_column("references")
        batch_op.drop_column("model")
        batch_op.drop_column("provider")
        batch_op.drop_column("source")
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.drop_column("summary_updated_at")
        batch_op.drop_column("summary")
