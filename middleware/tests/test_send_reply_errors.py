"""
tests/test_send_reply_errors.py — error-path tests for POST /internal/send
(api/app.py::send_reply, ADR 0004 Decisions #5/#6).

test_email_threading.py::TestReplyToOutboundMessageThreading already covers
the happy path (send succeeds, OutboundMessage persisted, thread_id
inherited correctly). This file fills the gap flagged in the reply-compose
UI handoff (ComposeReply.jsx, ADR 0004 Decision #4): the three failure
branches the frontend's status-state machine and post() helper depend on
were reasoned through by code inspection only, never exercised. Covers:

  - 404 when in_reply_to_order_id doesn't match any InboundOrder row —
    and confirms NO OutboundMessage row is written in this case (the
    handler raises before reaching the send/persist block).
  - 503 when SMTP isn't configured (SMTPNotConfiguredError) — confirms an
    OutboundMessage row IS still written, with send_status="failed" and
    error_message populated, matching Decision #6's "record failed
    attempts too" intent.
  - 502 when send_email raises an smtplib/socket-level error — same
    persist-on-failure assertion, plus confirms the detail message the
    frontend's post() helper will surface is prefixed "SMTP send failed:".
  - a reply with no in_reply_to_order_id at all (fresh, non-reply send)
    still succeeds and persists thread_id=None / in_reply_to_order_id=None.

Run with: pytest tests/test_send_reply_errors.py -v
"""
import asyncio
import smtplib
import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.app as app
from database.models import Base, InboundOrder, OutboundMessage, get_session
from mail.smtp_client import SMTPNotConfiguredError
from api.app import SendRequest


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


def _all_outbound(engine):
    session = get_session(engine)
    try:
        return session.query(OutboundMessage).all()
    finally:
        session.close()


def _make_request(**overrides):
    defaults = dict(
        sender="broker@example.com",
        to="counterparty@example.com",
        subject="RE: cargo enquiry",
        body="we can offer an aframax",
        in_reply_to_order_id=None,
    )
    defaults.update(overrides)
    return SendRequest(**defaults)


# ── 404: unknown order ──────────────────────────────────────────────────────

class TestSendReplyUnknownOrder:
    """
    A real HTTP caller always sends in_reply_to_order_id as a JSON string;
    on real Postgres psycopg2 casts it implicitly. The in-memory sqlite
    fixture's UUID column type is emulated rather than native, though, and
    its bind processor requires an actual uuid.UUID instance — same
    sqlite-only divergence documented in test_email_threading.py's
    wired_app_engine fixture and TestReplyToOutboundMessageThreading. We
    reassign post-construction (pydantic doesn't re-validate on plain
    attribute assignment) to work around the fixture without changing
    what's actually sent to send_reply's business logic.
    """

    def test_unknown_order_id_raises_404(self, wired_app_engine):
        req = _make_request(in_reply_to_order_id=str(uuid.uuid4()))
        req.in_reply_to_order_id = uuid.uuid4()
        with patch.object(app, "send_email") as mock_send:
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(app.send_reply(req))
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Order not found"
        mock_send.assert_not_called()

    def test_unknown_order_id_writes_no_outbound_row(self, wired_app_engine):
        req = _make_request(in_reply_to_order_id=str(uuid.uuid4()))
        req.in_reply_to_order_id = uuid.uuid4()
        with patch.object(app, "send_email"):
            with pytest.raises(HTTPException):
                asyncio.run(app.send_reply(req))
        assert _all_outbound(wired_app_engine) == []


# ── 503: SMTP not configured ─────────────────────────────────────────────────

class TestSendReplySmtpNotConfigured:

    def test_raises_503_with_configuration_detail(self, wired_app_engine):
        req = _make_request()
        with patch.object(
            app, "send_email",
            side_effect=SMTPNotConfiguredError(
                "SMTP is not configured — set SMTP_HOST, SMTP_USER, SMTP_PASS in .env"
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(app.send_reply(req))
        assert exc_info.value.status_code == 503
        assert "SMTP_HOST" in exc_info.value.detail

    def test_writes_failed_outbound_row_even_though_send_failed(self, wired_app_engine):
        req = _make_request()
        with patch.object(
            app, "send_email", side_effect=SMTPNotConfiguredError("not configured")
        ):
            with pytest.raises(HTTPException):
                asyncio.run(app.send_reply(req))
        rows = _all_outbound(wired_app_engine)
        assert len(rows) == 1
        assert rows[0].send_status == "failed"
        assert rows[0].error_message == "not configured"
        assert rows[0].message_id is None
        # Broker's typed content must be preserved on the row for the Sent
        # view even though the send itself failed.
        assert rows[0].to_addr == "counterparty@example.com"
        assert rows[0].subject == "RE: cargo enquiry"


# ── 502: SMTP send failure (smtplib/socket-level) ────────────────────────────

class TestSendReplySmtpSendFailure:

    def test_smtp_exception_raises_502_with_prefixed_detail(self, wired_app_engine):
        req = _make_request()
        with patch.object(
            app, "send_email",
            side_effect=smtplib.SMTPAuthenticationError(535, b"bad creds"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(app.send_reply(req))
        assert exc_info.value.status_code == 502
        assert exc_info.value.detail.startswith("SMTP send failed: ")

    def test_connection_refused_also_maps_to_502(self, wired_app_engine):
        req = _make_request()
        with patch.object(
            app, "send_email", side_effect=ConnectionRefusedError("refused")
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(app.send_reply(req))
        assert exc_info.value.status_code == 502

    def test_writes_failed_outbound_row_on_send_exception(self, wired_app_engine):
        req = _make_request()
        with patch.object(
            app, "send_email", side_effect=smtplib.SMTPServerDisconnected("gone")
        ):
            with pytest.raises(HTTPException):
                asyncio.run(app.send_reply(req))
        rows = _all_outbound(wired_app_engine)
        assert len(rows) == 1
        assert rows[0].send_status == "failed"
        assert "gone" in rows[0].error_message


# ── Fresh (non-reply) send still works ───────────────────────────────────────

class TestSendReplyWithoutParentOrder:

    def test_send_with_no_in_reply_to_order_id_succeeds(self, wired_app_engine):
        req = _make_request(in_reply_to_order_id=None)
        with patch.object(app, "send_email", return_value="<new@example.com>"):
            result = asyncio.run(app.send_reply(req))
        assert result == {"status": "sent", "message_id": "<new@example.com>"}
        rows = _all_outbound(wired_app_engine)
        assert len(rows) == 1
        assert rows[0].in_reply_to_order_id is None
        assert rows[0].thread_id is None
        assert rows[0].send_status == "sent"
