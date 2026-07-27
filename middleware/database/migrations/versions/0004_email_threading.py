"""Add RFC 2822 email threading columns to inbound_orders

Revision ID: 0004_email_threading
Revises: 0003_low_confidence_field_names
Create Date: 2026-07-27 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004_email_threading"
down_revision: Union[str, None] = "0003_low_confidence_field_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inbound_orders",
        sa.Column("message_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "inbound_orders",
        sa.Column("in_reply_to", sa.String(255), nullable=True),
    )
    op.add_column(
        "inbound_orders",
        sa.Column("references", sa.Text(), nullable=True),
    )
    op.add_column(
        "inbound_orders",
        sa.Column("thread_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_inbound_orders_message_id", "inbound_orders", ["message_id"])
    op.create_index("ix_inbound_orders_thread_id", "inbound_orders", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_inbound_orders_thread_id", table_name="inbound_orders")
    op.drop_index("ix_inbound_orders_message_id", table_name="inbound_orders")
    op.drop_column("inbound_orders", "thread_id")
    op.drop_column("inbound_orders", "references")
    op.drop_column("inbound_orders", "in_reply_to")
    op.drop_column("inbound_orders", "message_id")
