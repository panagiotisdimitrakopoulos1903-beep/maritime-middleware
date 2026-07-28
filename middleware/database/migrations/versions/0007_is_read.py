"""Add is_read to inbound_orders

Revision ID: 0007_is_read
Revises: 0006_signal_cache_refresh_tz
Create Date: 2026-07-28 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007_is_read"
down_revision: Union[str, None] = "0006_signal_cache_refresh_tz"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inbound_orders",
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("inbound_orders", "is_read")
