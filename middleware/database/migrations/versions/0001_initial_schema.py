"""Initial schema — all tables

Revision ID: 0001_initial_schema
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # ── inbound_orders ────────────────────────────────────────────────────────
    op.create_table(
        "inbound_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("sender", sa.String(255)),
        sa.Column("subject", sa.String(500)),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("cargo_type", sa.String(100)),
        sa.Column("quantity_mt", sa.Float()),
        sa.Column("quantity_min_mt", sa.Float()),
        sa.Column("quantity_max_mt", sa.Float()),
        sa.Column("load_port", sa.String(100)),
        sa.Column("load_port_canonical", sa.String(100)),
        sa.Column("discharge_port", sa.String(100)),
        sa.Column("discharge_port_canonical", sa.String(100)),
        sa.Column("laycan_start", sa.DateTime()),
        sa.Column("laycan_end", sa.DateTime()),
        sa.Column("vessel_size_dwt", sa.Float()),
        sa.Column("vessel_type", sa.String(100)),
        sa.Column("freight_rate", sa.String(100)),
        sa.Column("charterer", sa.String(255)),
        sa.Column("confidence_scores", postgresql.JSON()),
        sa.Column("parse_confidence", sa.Float()),
        sa.Column("has_low_confidence_fields", sa.Boolean(), default=False),
        sa.Column("parse_error", sa.Text()),
    )
    op.create_index("ix_inbound_orders_received_at", "inbound_orders", ["received_at"])
    op.create_index("ix_inbound_orders_cargo_type", "inbound_orders", ["cargo_type"])
    op.create_index("ix_inbound_orders_load_port_canonical", "inbound_orders", ["load_port_canonical"])

    # ── cached_vessels ────────────────────────────────────────────────────────
    op.create_table(
        "cached_vessels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cached_at", sa.DateTime(), nullable=False),
        sa.Column("vessel_id", sa.String(100), nullable=False, unique=True),
        sa.Column("vessel_name", sa.String(255)),
        sa.Column("vessel_class", sa.String(100)),
        sa.Column("dwt", sa.Float()),
        sa.Column("built_year", sa.Integer()),
        sa.Column("open_port", sa.String(100)),
        sa.Column("open_port_area", sa.String(100)),
        sa.Column("open_date", sa.DateTime()),
        sa.Column("commercial_status", sa.String(50)),
        sa.Column("market_deployment", sa.String(50)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("last_ais_update", sa.DateTime()),
        sa.Column("operational_status", sa.String(50)),
        sa.Column("cargo_types", postgresql.JSON()),
        sa.Column("raw_signal_data", postgresql.JSON()),
    )
    op.create_index("ix_cached_vessels_vessel_class", "cached_vessels", ["vessel_class"])
    op.create_index("ix_cached_vessels_open_port_area", "cached_vessels", ["open_port_area"])
    op.create_index("ix_cached_vessels_open_date", "cached_vessels", ["open_date"])
    op.create_index("ix_cached_vessels_commercial_status", "cached_vessels", ["commercial_status"])

    # ── signal_cache_refreshes ────────────────────────────────────────────────
    op.create_table(
        "signal_cache_refreshes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("refreshed_at", sa.DateTime(), nullable=False),
        sa.Column("vessel_count", sa.Integer()),
        sa.Column("success", sa.Boolean(), default=True),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
    )

    # ── match_results ─────────────────────────────────────────────────────────
    op.create_table(
        "match_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inbound_orders.id"),
            nullable=False,
        ),
        sa.Column("matched_at", sa.DateTime(), nullable=False),
        sa.Column("vessel_id", sa.String(100), nullable=False),
        sa.Column("vessel_name", sa.String(255)),
        sa.Column("vessel_class", sa.String(100)),
        sa.Column("dwt", sa.Float()),
        sa.Column("open_port", sa.String(100)),
        sa.Column("open_date", sa.DateTime()),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("score_vessel_size", sa.Float()),
        sa.Column("score_geography", sa.Float()),
        sa.Column("score_date_overlap", sa.Float()),
        sa.Column("score_cargo_type", sa.Float()),
    )
    op.create_index("ix_match_results_order_id", "match_results", ["order_id"])
    op.create_index("ix_match_results_matched_at", "match_results", ["matched_at"])


def downgrade() -> None:
    op.drop_table("match_results")
    op.drop_table("signal_cache_refreshes")
    op.drop_table("cached_vessels")
    op.drop_table("inbound_orders")
