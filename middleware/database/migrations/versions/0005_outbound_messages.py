"""Add outbound_messages table (Sent folder, ADR 0004 Decisions #5/#6)

Revision ID: 0005_outbound_messages
Revises: 0004_email_threading
Create Date: 2026-07-28 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_outbound_messages"
down_revision: Union[str, None] = "0004_email_threading"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "in_reply_to_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inbound_orders.id"),
            nullable=True,
        ),
        sa.Column("thread_id", sa.String(255), nullable=True),
        sa.Column("from_addr", sa.String(255), nullable=False),
        sa.Column("to_addr", sa.String(255), nullable=False),
        sa.Column("cc_addr", sa.String(255), nullable=True),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("in_reply_to", sa.String(255), nullable=True),
        sa.Column("references", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("send_status", sa.String(20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_outbound_messages_in_reply_to_order_id",
        "outbound_messages",
        ["in_reply_to_order_id"],
    )
    op.create_index(
        "ix_outbound_messages_sent_at", "outbound_messages", ["sent_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbound_messages_sent_at", table_name="outbound_messages"
    )
    op.drop_index(
        "ix_outbound_messages_in_reply_to_order_id", table_name="outbound_messages"
    )
    op.drop_table("outbound_messages")
