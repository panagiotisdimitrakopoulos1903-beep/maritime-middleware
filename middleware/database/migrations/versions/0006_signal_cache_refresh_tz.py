"""Make signal_cache_refreshes.refreshed_at timezone-aware

Revision ID: 0006_signal_cache_refresh_tz
Revises: 0005_outbound_messages
Create Date: 2026-07-28 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0006_signal_cache_refresh_tz"
down_revision: Union[str, None] = "0005_outbound_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE signal_cache_refreshes
        ALTER COLUMN refreshed_at TYPE TIMESTAMP WITH TIME ZONE
        USING refreshed_at AT TIME ZONE 'UTC'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE signal_cache_refreshes
        ALTER COLUMN refreshed_at TYPE TIMESTAMP WITHOUT TIME ZONE
        USING refreshed_at AT TIME ZONE 'UTC'
        """
    )
