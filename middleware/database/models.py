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

    # RFC 2822 threading headers (ADR 0004, Decision #3). message_id is the
    # message's own Message-ID header (not to be confused with
    # source_message_id above, which is the IMAP UID / mock id used for
    # dedup); in_reply_to/references are only present on replies. thread_id
    # is computed at ingest time (see api/app.py::_process_inbound): reused
    # from the matching parent row if in_reply_to matches an existing
    # message_id, otherwise a new thread starts here. Not unique — unlike
    # source_message_id, nothing here needs to enforce uniqueness at the DB
    # level.
    message_id = Column(String(255), nullable=True)
    in_reply_to = Column(String(255), nullable=True)
    references = Column(Text, nullable=True)
    thread_id = Column(String(255), nullable=True)

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

    # Field names actually below settings.parser_confidence_threshold at
    # parse time (parsed.low_confidence_field_names from llm_parser.py) —
    # NOT the same as confidence_scores.keys(), which is all 6 core fields
    # unconditionally. FR-21.
    low_confidence_field_names = Column(JSON, nullable=True)

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
        Index("ix_inbound_orders_message_id", "message_id"),
        Index("ix_inbound_orders_thread_id", "thread_id"),
    )


# ── Outbound messages (Sent) ──────────────────────────────────────────────────

class OutboundMessage(Base):
    """
    Every reply sent via POST /internal/send (ADR 0004, Decisions #5/#6).

    Populated by that endpoint's own send code path only — on BOTH success
    and failure, per Decision #6 ("populated only by the backend's own
    successful send calls" means "only this code path writes rows", not
    "only successful sends get a row"; send_status/error_message only make
    sense as columns if failed attempts are recorded too, so the broker's
    Sent view can show a failed send rather than silently losing it).

    Deliberately a separate table from inbound_orders (not a unified
    `messages` table with a direction column) — see CLAUDE.md's
    "Conventions worth knowing" and ADR 0004 Decision #6's Reasoning:
    inbound messages carry parse/match columns that are structurally
    meaningless for outbound rows.
    """
    __tablename__ = "outbound_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK context — which inbound order (if any) this is a reply to.
    in_reply_to_order_id = Column(
        UUID(as_uuid=True), ForeignKey("inbound_orders.id"), nullable=True
    )

    # Denormalised copy of the parent order's thread_id (same reasoning as
    # MatchResult's denormalised vessel snapshot below — don't collapse this
    # into a foreign-key-only relationship; carrying thread_id forward here
    # gives a coherent Sent view later without a join back to
    # inbound_orders). Null when in_reply_to_order_id is null.
    thread_id = Column(String(255), nullable=True)

    # Envelope
    from_addr = Column(String(255), nullable=False)
    to_addr = Column(String(255), nullable=False)
    cc_addr = Column(String(255), nullable=True)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)

    # RFC 2822 threading headers on the outbound message itself.
    message_id = Column(String(255), nullable=True)  # our own generated Message-ID
    in_reply_to = Column(String(255), nullable=True)
    references = Column(Text, nullable=True)

    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    send_status = Column(String(20), nullable=False)  # "sent" / "failed"
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_outbound_messages_in_reply_to_order_id", "in_reply_to_order_id"),
        Index("ix_outbound_messages_sent_at", "sent_at"),
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
