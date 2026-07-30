"""
tests/test_parse_status.py — tests for ADR 0007 (explicit parse-failure
status and match gating).

Covers api/app.py::_process_inbound's Step 2/3/4 behavior:
  - A failed parse (parser.llm_parser.ParsedOrder.parse_status == "failed")
    persists InboundOrder.parse_status == "failed" and produces ZERO
    MatchResult rows — matching is skipped entirely, not run-and-discarded
    (ADR 0007, Decision 2). This is asserted even with a real candidate
    vessel present in the cache, so the test would fail loudly if matching
    ran anyway.
  - A structurally successful parse — including the all-null-fields,
    zero-confidence case (a real, if very weak, judgment call by the
    model about an uninformative message, per ADR 0007 Decision 1) — still
    persists parse_status == "success" and runs matching normally,
    producing MatchResult rows against an available cached vessel.

Follows the in-memory sqlite + monkeypatched app.engine pattern established
in test_send_reply_errors.py / test_mark_order_read.py. `_process_inbound`
is a plain (non-async) function, called directly — no BackgroundTasks
plumbing needed for these tests.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.app as app
from database.models import Base, InboundOrder, MatchResult, CachedVessel, get_session
from parser.llm_parser import ParsedOrder, FieldWithConfidence


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


def _seed_one_available_vessel(engine):
    """A candidate vessel that WOULD be matched if the matching engine ran.

    Present so a passing "zero MatchResult rows" assertion on the failed-
    parse test means matching was genuinely skipped, not that there was
    simply nothing to match against.
    """
    session = get_session(engine)
    try:
        session.add(CachedVessel(
            id=uuid.uuid4(),
            cached_at=datetime.now(timezone.utc).replace(tzinfo=None),
            vessel_id="9999999",
            vessel_name="MV Test Candidate",
            vessel_class="Panamax",
            dwt=75000,
            built_year=2018,
            open_port="Antwerp",
            open_port_area="ARA",
            open_date=(datetime.now(timezone.utc) + timedelta(days=3)).replace(tzinfo=None),
            commercial_status="available",
            market_deployment="spot",
            cargo_types=["grain"],
            raw_signal_data={},
        ))
        session.commit()
    finally:
        session.close()


def _get_order(engine, order_id):
    session = get_session(engine)
    try:
        return session.query(InboundOrder).filter_by(id=order_id).first()
    finally:
        session.close()


def _count_match_results(engine, order_id):
    session = get_session(engine)
    try:
        return session.query(MatchResult).filter_by(order_id=order_id).count()
    finally:
        session.close()


# ── Failed parse: skip matching entirely, zero MatchResult rows ────────────

class TestFailedParseSkipsMatching:

    def test_failed_parse_persists_status_and_produces_no_match_results(
        self, wired_app_engine
    ):
        _seed_one_available_vessel(wired_app_engine)

        failed_order = ParsedOrder()
        failed_order.error = "RetryError[<Future ... raised AuthenticationError>]"
        failed_order.parse_confidence = 0.0
        failed_order.parse_status = "failed"

        order_id = uuid.uuid4()
        with patch("api.app.parse_message", return_value=failed_order):
            app._process_inbound(
                sender="broker@example.com",
                subject="garbled telex",
                raw_body="sdkfj 25 wtv possibly grain???",
                order_id=order_id,
            )

        order = _get_order(wired_app_engine, order_id)
        assert order is not None
        assert order.parse_status == "failed"
        assert order.parse_error == failed_order.error

        assert _count_match_results(wired_app_engine, order_id) == 0

    def test_json_decode_failure_from_real_parser_also_skips_matching(
        self, wired_app_engine
    ):
        """Exercise parse_message()'s own JSONDecodeError except-branch
        directly (not just a hand-built ParsedOrder), then confirm
        _process_inbound respects the parse_status it produces."""
        _seed_one_available_vessel(wired_app_engine)

        with patch("parser.llm_parser._call_llm", return_value="not valid json{{{"):
            from parser.llm_parser import parse_message
            parsed = parse_message("50k mt grain ant/spore")

        assert parsed.parse_status == "failed"
        assert parsed.error is not None

        order_id = uuid.uuid4()
        with patch("api.app.parse_message", return_value=parsed):
            app._process_inbound(
                sender="broker@example.com",
                subject="cargo enquiry",
                raw_body="50k mt grain ant/spore",
                order_id=order_id,
            )

        order = _get_order(wired_app_engine, order_id)
        assert order.parse_status == "failed"
        assert _count_match_results(wired_app_engine, order_id) == 0


# ── Successful parse (including all-null-fields): matching runs normally ───

class TestSuccessfulParseRunsMatching:

    def test_all_null_fields_zero_confidence_parse_is_still_success_and_matches(
        self, wired_app_engine
    ):
        """A structurally successful LLM response where every field came
        back null/0.0 confidence (a genuinely uninformative message) is
        `parse_status == "success"` by definition (ADR 0007, Decision 1) —
        not "failed" — and matching still runs against it, hitting the
        matching engine's own neutral-0.5-default behavior (unchanged by
        this ADR)."""
        _seed_one_available_vessel(wired_app_engine)

        blank_order = ParsedOrder()  # every field at its class default
        assert blank_order.parse_status == "success"
        assert blank_order.parse_confidence == 0.0
        assert blank_order.error is None

        order_id = uuid.uuid4()
        with patch("api.app.parse_message", return_value=blank_order):
            app._process_inbound(
                sender="broker@example.com",
                subject="vague message",
                raw_body="hi, any ideas?",
                order_id=order_id,
            )

        order = _get_order(wired_app_engine, order_id)
        assert order.parse_status == "success"
        assert order.parse_error is None

        # Matching ran (not skipped) — one available candidate vessel in
        # the cache produces exactly one MatchResult row.
        assert _count_match_results(wired_app_engine, order_id) == 1

    def test_normal_high_confidence_parse_is_success_and_matches(
        self, wired_app_engine
    ):
        _seed_one_available_vessel(wired_app_engine)

        good_order = ParsedOrder(
            cargo_type=FieldWithConfidence(value="grain", confidence=0.98),
            quantity_mt=FieldWithConfidence(value=57500, confidence=0.95),
            load_port=FieldWithConfidence(value="Antwerp", confidence=0.97),
            discharge_port=FieldWithConfidence(value="Japan", confidence=0.9),
            laycan_start=FieldWithConfidence(value="2024-08-10", confidence=0.95),
            laycan_end=FieldWithConfidence(value="2024-08-20", confidence=0.95),
        )
        good_order.load_port_canonical = "Antwerp"
        good_order.discharge_port_canonical = "Japan (any)"
        good_order.parse_confidence = 0.95

        order_id = uuid.uuid4()
        with patch("api.app.parse_message", return_value=good_order):
            app._process_inbound(
                sender="broker@example.com",
                subject="grain cargo",
                raw_body="55/60,000 MT GRAIN ant/japan aug 10-20",
                order_id=order_id,
            )

        order = _get_order(wired_app_engine, order_id)
        assert order.parse_status == "success"
        assert _count_match_results(wired_app_engine, order_id) == 1
