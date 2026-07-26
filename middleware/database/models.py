"""
database/models.py — SQLAlchemy models for all persisted data
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    create_engine, Column, String, Float, Integer,
    DateTime, Text, Boolean, JSON, ForeignKey, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session
from sqlalchemy.dialects.postgresql import UUID
import uuid


class Base(DeclarativeBase):
    pass


# ── Inbound orders ────────────────────────────────────────────────────────────

class InboundOrder(Base):
    """
    Every email that passes through the Milter hook.
    Raw message stored alongside the structured parse result.
    """
    __tablename__ = "inbound_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Raw email fields
    sender = Column(String(255))
    subject = Column(String(500))
    raw_body = Column(Text, nullable=False)

    # Stable id of the source message (IMAP UID / mock id), used to dedupe
    # repeated polls of the same mailbox. Null for Milter-era / manual rows.
    source_message_id = Column(String(255), unique=True, nullable=True)

    # Parsed structured fields
    cargo_type = Column(String(100))
    quantity_mt = Column(Float)
    quantity_min_mt = Column(Float)
    quantity_max_mt = Column(Float)
    load_port = Column(String(100))
    load_port_canonical = Column(String(100))   # normalised name
    discharge_port = Column(String(100))
    discharge_port_canonical = Column(String(100))
    laycan_start = Column(DateTime)
    laycan_end = Column(DateTime)
    vessel_size_dwt = Column(Float)
    vessel_type = Column(String(100))
    freight_rate = Column(String(100))
    charterer = Column(String(255))

    # Confidence scores per field (stored as JSON dict)
    confidence_scores = Column(JSON)

    # Overall parse quality
    parse_confidence = Column(Float)          # mean of all field confidences
    has_low_confidence_fields = Column(Boolean, default=False)
    parse_error = Column(Text)                # if parsing failed entirely

    # Relationships
    matches = relationship("MatchResult", back_populates="order")

    __table_args__ = (
        Index("ix_inbound_orders_received_at", "received_at"),
        Index("ix_inbound_orders_cargo_type", "cargo_type"),
        Index("ix_inbound_orders_load_port_canonical", "load_port_canonical"),
        Index("ix_inbound_orders_source_message_id", "source_message_id"),
    )


# ── Signal Ocean cache ────────────────────────────────────────────────────────

class CachedVessel(Base):
    """
    Local cache of Signal Ocean tonnage list.
    Refreshed every N minutes by the scheduler.
    """
    __tablename__ = "cached_vessels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cached_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Signal Ocean vessel identifiers
    vessel_id = Column(String(100), nullable=False, unique=True)
    vessel_name = Column(String(255))
    vessel_class = Column(String(100))
    dwt = Column(Float)
    built_year = Column(Integer)

    # Open position data
    open_port = Column(String(100))
    open_port_area = Column(String(100))
    open_date = Column(DateTime)
    commercial_status = Column(String(50))   # available, on_subs, fixed etc
    market_deployment = Column(String(50))   # spot, relet etc

    # AIS / operational
    latitude = Column(Float)
    longitude = Column(Float)
    last_ais_update = Column(DateTime)
    operational_status = Column(String(50))

    # Supported cargo types (JSON list)
    cargo_types = Column(JSON)

    # Raw Signal data snapshot
    raw_signal_data = Column(JSON)

    __table_args__ = (
        Index("ix_cached_vessels_vessel_class", "vessel_class"),
        Index("ix_cached_vessels_open_port_area", "open_port_area"),
        Index("ix_cached_vessels_open_date", "open_date"),
        Index("ix_cached_vessels_commercial_status", "commercial_status"),
    )


class SignalCacheRefresh(Base):
    """
    Log of every Signal Ocean cache refresh — for staleness tracking.
    """
    __tablename__ = "signal_cache_refreshes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    refreshed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    vessel_count = Column(Integer)
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    duration_ms = Column(Integer)


# ── Match results ─────────────────────────────────────────────────────────────

class MatchResult(Base):
    """
    The ranked vessels returned for a given inbound order.
    Stored for analytics and the data monetisation layer.
    """
    __tablename__ = "match_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("inbound_orders.id"), nullable=False)
    matched_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Vessel snapshot at time of match (denormalised for history integrity)
    vessel_id = Column(String(100), nullable=False)
    vessel_name = Column(String(255))
    vessel_class = Column(String(100))
    dwt = Column(Float)
    open_port = Column(String(100))
    open_date = Column(DateTime)

    # Scoring breakdown
    rank = Column(Integer, nullable=False)
    total_score = Column(Float, nullable=False)
    score_vessel_size = Column(Float)
    score_geography = Column(Float)
    score_date_overlap = Column(Float)
    score_cargo_type = Column(Float)

    order = relationship("InboundOrder", back_populates="matches")

    __table_args__ = (
        Index("ix_match_results_order_id", "order_id"),
        Index("ix_match_results_matched_at", "matched_at"),
    )


# ── Database engine + session ─────────────────────────────────────────────────

def get_engine(database_url: str):
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def get_session(engine) -> Session:
    return Session(engine)


def create_tables(engine):
    Base.metadata.create_all(engine)
