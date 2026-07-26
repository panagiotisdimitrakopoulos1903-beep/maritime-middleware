"""Add source_message_id to inbound_orders

Revision ID: 0002_source_message_id
Revises: 0001_initial_schema
Create Date: 2026-07-26 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002_source_message_id"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inbound_orders",
        sa.Column("source_message_id", sa.String(255), nullable=True),
    )
    # NOTE: op.add_column() does not honor the `unique=True` flag on a
    # Column object (a known Alembic limitation) — the uniqueness has to be
    # created explicitly. A unique index both enforces it and satisfies the
    # `ix_inbound_orders_source_message_id` index requested alongside the
    # model change, so a single unique index covers both needs.
    op.create_index(
        "ix_inbound_orders_source_message_id",
        "inbound_orders",
        ["source_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_orders_source_message_id", table_name="inbound_orders")
    op.drop_column("inbound_orders", "source_message_id")
