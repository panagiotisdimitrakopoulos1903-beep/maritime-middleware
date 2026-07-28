"""
tests/test_email_threading.py — tests for RFC 2822 email threading
(ADR 0004, Decision #3): `_compute_thread_id` and its wiring into
`_process_inbound` (middleware/api/app.py).

Kept as its own file rather than folded into test_scheduler_jobs.py: that
file covers scheduler/jobs.py::_poll_imap_inbox (the poller, which mocks
`_process_inbound` away entirely to test dedup/ordering in isolation). These
tests exercise the opposite side — `_process_inbound`/`_compute_thread_id`
themselves, for real, against a real (sqlite) DB — a different unit under
test even though both ultimately sit on the same pipeline function.

Covers:
  - a message with no `in_reply_to` starts its own thread (thread_id :=
    message_id, or a generated UUID if message_id itself is absent)
  - a message whose `in_reply_to` matches an existing row's `message_id`
    joins that row's thread
  - a message whose `in_reply_to` matches nothing existing starts a new
    thread rather than erroring
  - message_id/in_reply_to/references are actually persisted on the
    InboundOrder row, not just used transiently for the thread_id lookup

Run with: pytest tests/ -v
"""
import asyncio
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.app as app
from database.models import Base, InboundOrder, OutboundMessage, get_session
from parser.llm_parser import ParsedOrder
from api.app import SendRequest


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def sqlite_engine():
    """Fresh in-memory sqlite DB per test. StaticPool keeps a single
    connection alive so every get_session() call sees the same in-memory
    database (plain 'sqlite://' would otherwise hand out a brand-new empty
    DB per connection) — same pattern as test_scheduler_jobs.py."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def wired_app_engine(monkeypatch, sqlite_engine):
    """Point api.app's module-level `engine` at our sqlite DB. Without this,
    _process_inbound's get_session(engine) call would hit the real Postgres
    DSN from config.py, which isn't running in the test environment —
    mirrors test_scheduler_jobs.py's `wired_engine` fixture for
    scheduler.jobs._engine."""
    monkeypatch.setattr(app, "engine", sqlite_engine)
    return sqlite_engine


@pytest.fixture(autouse=True)
def mock_parse_message():
    """_process_inbound's Step 1 calls parse_message(raw_body), which makes
    a real Anthropic API call. Stub it with a default (all-fields-empty)
    ParsedOrder — fine here since these tests only assert on
    thread_id/message_id/in_reply_to/references, never on parsed cargo
    fields."""
    with patch.object(app, "parse_message", return_value=ParsedOrder()) as m:
        yield m


def _get_order(session, order_id):
    return session.query(InboundOrder).filter_by(id=order_id).first()


# ── _compute_thread_id direct unit tests ────────────────────────────────────

class TestComputeThreadId:

    def test_no_in_reply_to_starts_new_thread_keyed_on_message_id(self, wired_app_engine):
        session = get_session(wired_app_engine)
        try:
            thread_id = app._compute_thread_id(session, "<m1@x.com>", None)
        finally:
            session.close()
        assert thread_id == "<m1@x.com>"

    def test_no_in_reply_to_and_no_message_id_generates_a_uuid(self, wired_app_engine):
        session = get_session(wired_app_engine)
        try:
            thread_id = app._compute_thread_id(session, None, None)
        finally:
            session.close()
        assert thread_id
        uuid.UUID(thread_id)  # raises ValueError if not a valid UUID string

    def test_in_reply_to_matching_existing_message_id_joins_that_thread(self, wired_app_engine):
        session = get_session(wired_app_engine)
        try:
            session.add(InboundOrder(
                id=uuid.uuid4(),
                sender="a@b.com", subject="s", raw_body="b",
                message_id="<parent@x.com>",
                thread_id="<parent@x.com>",
            ))
            session.commit()
            thread_id = app._compute_thread_id(session, "<child@x.com>", "<parent@x.com>")
        finally:
            session.close()
        assert thread_id == "<parent@x.com>"

    def test_in_reply_to_with_no_matching_row_starts_new_thread(self, wired_app_engine):
        session = get_session(wired_app_engine)
        try:
            thread_id = app._compute_thread_id(session, "<child@x.com>", "<nonexistent@x.com>")
        finally:
            session.close()
        # No existing row's message_id matches -> this message starts its
        # own thread, keyed on its own message_id (not the unmatched
        # in_reply_to).
        assert thread_id == "<child@x.com>"

    def test_in_reply_to_matching_outbound_message_joins_that_thread(self, wired_app_engine):
        """A counterparty replying to a message *we* sent (POST
        /internal/send) has an in_reply_to that matches an
        OutboundMessage.message_id, not any InboundOrder.message_id — the
        gap this fix addresses."""
        session = get_session(wired_app_engine)
        try:
            session.add(OutboundMessage(
                in_reply_to_order_id=None,
                thread_id="<orig@example.com>",
                from_addr="broker@example.com",
                to_addr="counterparty@example.com",
                subject="RE: cargo",
                body="offer",
                message_id="<broker-reply@example.com>",
                sent_at=app.datetime.now(app.timezone.utc),
                send_status="sent",
            ))
            session.commit()
            thread_id = app._compute_thread_id(
                session, "<second-reply@example.com>", "<broker-reply@example.com>"
            )
        finally:
            session.close()
        assert thread_id == "<orig@example.com>"


# ── End-to-end through _process_inbound ─────────────────────────────────────

class TestProcessInboundThreading:

    def test_first_message_in_a_thread_persists_own_message_id_as_thread_id(self, wired_app_engine):
        order_id = uuid.uuid4()
        app._process_inbound(
            "broker@example.com", "cargo enquiry", "55k grain ant/jpn",
            order_id,
            message_id="<orig@example.com>",
        )
        session = get_session(wired_app_engine)
        try:
            row = _get_order(session, order_id)
            assert row is not None
            assert row.message_id == "<orig@example.com>"
            assert row.in_reply_to is None
            assert row.references is None
            assert row.thread_id == "<orig@example.com>"
        finally:
            session.close()

    def test_reply_joins_parent_thread(self, wired_app_engine):
        parent_id = uuid.uuid4()
        app._process_inbound(
            "ops@petrochem-shipping.com", "ENQ - crude", "80,000mt crude",
            parent_id,
            message_id="<parent@example.com>",
        )
        reply_id = uuid.uuid4()
        app._process_inbound(
            "desk3@euro-tankers.com", "RE: ENQ - crude", "can offer aframax",
            reply_id,
            message_id="<reply@example.com>",
            in_reply_to="<parent@example.com>",
            references="<parent@example.com>",
        )
        session = get_session(wired_app_engine)
        try:
            parent_row = _get_order(session, parent_id)
            reply_row = _get_order(session, reply_id)
            assert parent_row.thread_id == "<parent@example.com>"
            assert reply_row.thread_id == parent_row.thread_id
            assert reply_row.in_reply_to == "<parent@example.com>"
            assert reply_row.references == "<parent@example.com>"
        finally:
            session.close()

    def test_reply_to_unknown_message_id_starts_its_own_thread(self, wired_app_engine):
        order_id = uuid.uuid4()
        app._process_inbound(
            "broker@example.com", "RE: something old", "body",
            order_id,
            message_id="<orphan-reply@example.com>",
            in_reply_to="<never-ingested@example.com>",
        )
        session = get_session(wired_app_engine)
        try:
            row = _get_order(session, order_id)
            assert row.thread_id == "<orphan-reply@example.com>"
        finally:
            session.close()

    def test_message_with_no_threading_headers_at_all_still_gets_a_thread_id(self, wired_app_engine):
        """Manual/legacy ingest callers (pre-ADR-0004 /internal/ingest calls
        that omit the new optional fields) must still produce a persisted,
        non-null thread_id rather than erroring or leaving it null."""
        order_id = uuid.uuid4()
        app._process_inbound(
            "legacy@example.com", "no headers", "body", order_id,
        )
        session = get_session(wired_app_engine)
        try:
            row = _get_order(session, order_id)
            assert row.message_id is None
            assert row.thread_id is not None
        finally:
            session.close()


# ── End-to-end: reply-to-our-sent-reply threading (the gap this fix closes) ──

class TestReplyToOutboundMessageThreading:
    """
    Covers the gap flagged in ADR 0004's follow-up: a broker sends a reply
    via POST /internal/send (persisted as an OutboundMessage row, not an
    InboundOrder row); if the counterparty then replies to *that* message,
    the new inbound message's in_reply_to matches OutboundMessage.message_id
    rather than any InboundOrder.message_id. Before the fix, that reply fell
    through to `_compute_thread_id`'s final line and incorrectly started a
    brand new thread instead of joining the existing one.
    """

    def test_reply_to_broker_sent_reply_joins_original_thread(self, wired_app_engine):
        # ── Step 1: initial inbound order — the thread root ─────────────────
        order_id = uuid.uuid4()
        app._process_inbound(
            "counterparty@example.com", "ENQ - cargo", "55k grain ant/jpn",
            order_id,
            message_id="<orig@example.com>",
        )
        session = get_session(wired_app_engine)
        try:
            root_row = _get_order(session, order_id)
            assert root_row.thread_id == "<orig@example.com>"
        finally:
            session.close()

        # ── Step 2: broker sends a reply via POST /internal/send ───────────
        req = SendRequest(
            sender="broker@example.com",
            to="counterparty@example.com",
            subject="RE: ENQ - cargo",
            body="we can offer an aframax",
            in_reply_to_order_id=str(order_id),
        )
        # A real HTTP caller always sends in_reply_to_order_id as a JSON
        # string (as constructed above), and send_reply's query against it
        # works fine on real Postgres (psycopg2 casts the string
        # implicitly). The in-memory sqlite fixture's UUID column type is
        # emulated rather than native, though, and its bind processor
        # requires an actual uuid.UUID instance — same class of sqlite-only
        # divergence wired_app_engine already documents above. Reassigning
        # post-construction (pydantic doesn't re-validate on plain
        # attribute assignment) works around the fixture without changing
        # what's actually sent to send_reply's business logic.
        req.in_reply_to_order_id = order_id
        with patch.object(app, "send_email", return_value="<broker-reply@example.com>") as mock_send:
            result = asyncio.run(app.send_reply(req))
        mock_send.assert_called_once()
        assert result["message_id"] == "<broker-reply@example.com>"

        session = get_session(wired_app_engine)
        try:
            outbound = (
                session.query(OutboundMessage)
                .filter_by(message_id="<broker-reply@example.com>")
                .first()
            )
            assert outbound is not None
            assert outbound.thread_id == "<orig@example.com>"
        finally:
            session.close()

        # ── Step 3: counterparty replies to the broker's sent reply ─────────
        reply_id = uuid.uuid4()
        app._process_inbound(
            "counterparty@example.com", "RE: RE: ENQ - cargo", "aframax works for us",
            reply_id,
            message_id="<counterparty-reply2@example.com>",
            in_reply_to="<broker-reply@example.com>",
        )

        # ── Step 4: it must land in the same thread as the original ────────
        session = get_session(wired_app_engine)
        try:
            reply_row = _get_order(session, reply_id)
            assert reply_row.thread_id == "<orig@example.com>"
        finally:
            session.close()
