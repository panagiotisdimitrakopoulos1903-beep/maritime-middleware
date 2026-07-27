"""Add low_confidence_field_names to inbound_orders

Revision ID: 0003_low_confidence_field_names
Revises: 0002_source_message_id
Create Date: 2026-07-27 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_low_confidence_field_names"
down_revision: Union[str, None] = "0002_source_message_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inbound_orders",
        sa.Column("low_confidence_field_names", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inbound_orders", "low_confidence_field_names")
