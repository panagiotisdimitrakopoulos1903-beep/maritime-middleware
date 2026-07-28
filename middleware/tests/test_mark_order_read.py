"""
tests/test_mark_order_read.py — tests for PATCH /orders/{order_id}/read
(api/app.py::mark_order_read, ADR 0004 Decision #7).

Covers:
  - 404 when order_id doesn't match any InboundOrder row.
  - Marking an unread order read succeeds and persists is_read=True.
  - Calling it again on an already-read order is idempotent: no error,
    same response shape, is_read stays True.

Follows the in-memory sqlite + monkeypatched app.engine pattern established
in test_send_reply_errors.py / test_email_threading.py. Note: app.mark_order_read
is called directly with a raw uuid.UUID (not str(...)) — the in-memory
sqlite fixture's emulated UUID column type requires an actual uuid.UUID
instance for its bind processor, same divergence documented in those files;
a real HTTP caller always sends the id as a path-string, which psycopg2
casts implicitly against real Postgres.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.app as app
from database.models import Base, InboundOrder, get_session


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def wired_app_engine(monkeypatch, sqlite_engine):
    monkeypatch.setattr(app, "engine", sqlite_engine)
    return sqlite_engine


def _make_order(engine, **overrides):
    session = get_session(engine)
    try:
        defaults = dict(
            id=uuid.uuid4(),
            received_at=datetime.now(timezone.utc),
            sender="broker@example.com",
            subject="cargo enquiry",
            raw_body="30k mt grain ex ARA",
            is_read=False,
        )
        defaults.update(overrides)
        order = InboundOrder(**defaults)
        session.add(order)
        session.commit()
        return order.id
    finally:
        session.close()


def _get_order(engine, order_id):
    session = get_session(engine)
    try:
        return session.query(InboundOrder).filter_by(id=order_id).first()
    finally:
        session.close()


# ── 404: unknown order ──────────────────────────────────────────────────────

class TestMarkOrderReadUnknownOrder:

    def test_unknown_order_id_raises_404(self, wired_app_engine):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(app.mark_order_read(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Order not found"


# ── Happy path + idempotency ─────────────────────────────────────────────────

class TestMarkOrderReadIdempotent:

    def test_marks_unread_order_as_read(self, wired_app_engine):
        order_id = _make_order(wired_app_engine, is_read=False)
        result = asyncio.run(app.mark_order_read(order_id))
        assert result == {"order_id": str(order_id), "is_read": True}
        assert _get_order(wired_app_engine, order_id).is_read is True

    def test_calling_twice_is_idempotent(self, wired_app_engine):
        order_id = _make_order(wired_app_engine, is_read=False)

        first = asyncio.run(app.mark_order_read(order_id))
        second = asyncio.run(app.mark_order_read(order_id))

        assert first == second == {"order_id": str(order_id), "is_read": True}
        assert _get_order(wired_app_engine, order_id).is_read is True

    def test_already_read_order_does_not_error(self, wired_app_engine):
        order_id = _make_order(wired_app_engine, is_read=True)
        result = asyncio.run(app.mark_order_read(order_id))
        assert result == {"order_id": str(order_id), "is_read": True}
