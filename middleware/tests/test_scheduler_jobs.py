"""
tests/test_scheduler_jobs.py — tests for the IMAP inbox poller wired in per
ADR 0002 (scheduler/jobs.py::_poll_imap_inbox, _load_imap_source).

Covers:
  - dedup against InboundOrder.source_message_id (no double-parse/double-ingest
    on repeated polls)
  - oldest-first processing order of genuinely new messages
  - mock/live module selection in _load_imap_source, without colliding with
    the installed `mcp` SDK package
  - empty-inbox / fetch-error behavior (no crash, no spurious processing)

Run with: pytest tests/ -v
"""
import asyncio
import concurrent.futures
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import scheduler.jobs as jobs
from database.models import Base, InboundOrder, get_session

MIDDLEWARE_MCP_DIR = (Path(__file__).resolve().parent.parent / "mcp").resolve()


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sqlite_engine():
    """
    Fresh in-memory sqlite DB per test. StaticPool keeps a single connection
    alive so every get_session() call sees the same in-memory database
    (plain 'sqlite://' would otherwise hand out a brand-new empty DB per
    connection).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def wired_engine(monkeypatch, sqlite_engine):
    """
    Point scheduler.jobs's module-level _engine at our sqlite DB. Without
    this, `_poll_imap_inbox` would lazily call get_engine(settings.database_url)
    the first time it runs, which defaults to a real Postgres DSN
    (config.py) that isn't running in the test environment.
    """
    monkeypatch.setattr(jobs, "_engine", sqlite_engine)
    return sqlite_engine


def _seed_order(session, source_message_id):
    session.add(InboundOrder(
        id=uuid.uuid4(),
        sender="existing@example.com",
        subject="already ingested",
        raw_body="body already processed on a prior poll",
        source_message_id=source_message_id,
    ))
    session.commit()


def _mock_message(id_, hours_ago, sender="broker@example.com", subject="cargo enquiry"):
    received = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "id": id_,
        "sender": sender,
        "subject": subject,
        "received_at": received,
        "raw_body": f"raw body for {id_}",
    }


# ── Dedup against source_message_id ──────────────────────────────────────────

class TestPollImapInboxDedup:

    def test_only_new_messages_trigger_process_inbound(self, wired_engine):
        session = get_session(wired_engine)
        try:
            _seed_order(session, "mock-0001")
            _seed_order(session, "mock-0002")
        finally:
            session.close()

        messages = [
            _mock_message("mock-0001", hours_ago=1),   # already seen
            _mock_message("mock-0002", hours_ago=3),   # already seen
            _mock_message("mock-0003", hours_ago=5),   # new
            _mock_message("mock-0004", hours_ago=8),   # new
            _mock_message("mock-0005", hours_ago=20),  # new
        ]
        fake_source = MagicMock()
        fake_source.get_latest_orders.return_value = messages

        with patch.object(jobs, "_load_imap_source", return_value=fake_source), \
             patch("api.app._process_inbound") as mock_process:
            jobs._poll_imap_inbox()

        assert mock_process.call_count == 3
        processed_ids = {c.kwargs["source_message_id"] for c in mock_process.call_args_list}
        assert processed_ids == {"mock-0003", "mock-0004", "mock-0005"}

    def test_already_seen_ids_produce_no_new_rows_and_no_reprocessing(self, wired_engine):
        """Re-polling a mailbox where every message was already ingested must
        neither call _process_inbound again nor create duplicate InboundOrder
        rows (this is the whole point of the source_message_id column)."""
        session = get_session(wired_engine)
        try:
            _seed_order(session, "mock-0001")
            before_count = session.query(InboundOrder).count()
        finally:
            session.close()

        fake_source = MagicMock()
        fake_source.get_latest_orders.return_value = [_mock_message("mock-0001", hours_ago=1)]

        with patch.object(jobs, "_load_imap_source", return_value=fake_source), \
             patch("api.app._process_inbound") as mock_process:
            jobs._poll_imap_inbox()

        mock_process.assert_not_called()

        session = get_session(wired_engine)
        try:
            after_count = session.query(InboundOrder).count()
        finally:
            session.close()
        assert after_count == before_count == 1

    def test_new_messages_processed_oldest_first(self, wired_engine):
        messages = [
            _mock_message("m-newest", hours_ago=1),
            _mock_message("m-oldest", hours_ago=20),
            _mock_message("m-middle", hours_ago=8),
        ]
        fake_source = MagicMock()
        fake_source.get_latest_orders.return_value = messages

        with patch.object(jobs, "_load_imap_source", return_value=fake_source), \
             patch("api.app._process_inbound") as mock_process:
            jobs._poll_imap_inbox()

        processed_ids = [c.kwargs["source_message_id"] for c in mock_process.call_args_list]
        assert processed_ids == ["m-oldest", "m-middle", "m-newest"]

    def test_respects_imap_poll_batch_size_via_limit_kwarg(self, wired_engine):
        """_poll_imap_inbox must ask the source for settings.imap_poll_batch_size
        messages, not an unbounded fetch."""
        from config import settings

        fake_source = MagicMock()
        fake_source.get_latest_orders.return_value = []

        with patch.object(jobs, "_load_imap_source", return_value=fake_source), \
             patch("api.app._process_inbound"):
            jobs._poll_imap_inbox()

        fake_source.get_latest_orders.assert_called_once_with(
            limit=settings.imap_poll_batch_size
        )


# ── Mode selection: mock vs. live, and no collision with the `mcp` SDK ──────

class TestLoadImapSource:

    def test_defaults_to_mock_when_unset(self, monkeypatch):
        monkeypatch.delenv("IMAP_MODE", raising=False)
        source = jobs._load_imap_source()
        assert source.__name__ == "mock_inbox"
        assert hasattr(source, "get_latest_orders")
        assert hasattr(source, "MOCK_EMAILS")  # characteristic of mock_inbox
        assert Path(source.__file__).resolve().parent == MIDDLEWARE_MCP_DIR

    def test_explicit_mock_mode(self, monkeypatch):
        monkeypatch.setenv("IMAP_MODE", "mock")
        source = jobs._load_imap_source()
        assert source.__name__ == "mock_inbox"

    def test_live_mode_selects_imap_client(self, monkeypatch):
        monkeypatch.setenv("IMAP_MODE", "live")
        source = jobs._load_imap_source()
        assert source.__name__ == "imap_client"
        assert hasattr(source, "get_latest_orders")
        assert not hasattr(source, "MOCK_EMAILS")
        assert Path(source.__file__).resolve().parent == MIDDLEWARE_MCP_DIR

    def test_mode_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("IMAP_MODE", "LIVE")
        assert jobs._load_imap_source().__name__ == "imap_client"
        monkeypatch.setenv("IMAP_MODE", "Mock")
        assert jobs._load_imap_source().__name__ == "mock_inbox"

    def test_unrecognised_mode_falls_back_to_mock(self, monkeypatch):
        # _load_imap_source only special-cases "live"; anything else (typos
        # included) should behave like the documented default, not silently
        # pick live/real IMAP.
        monkeypatch.setenv("IMAP_MODE", "sandbox")
        assert jobs._load_imap_source().__name__ == "mock_inbox"

    def test_does_not_collide_with_installed_mcp_sdk_package(self, monkeypatch):
        """
        The whole reason _load_imap_source exists (per its docstring and ADR
        0002) is that `middleware/mcp/` the directory and `mcp` the installed
        PyPI SDK package (imap_server.py does
        `from mcp.server.fastmcp import FastMCP`) share a name. Confirm the
        loader resolves to our directory's modules, not the SDK package.
        """
        import importlib

        monkeypatch.delenv("IMAP_MODE", raising=False)
        source = jobs._load_imap_source()

        real_mcp_sdk = importlib.import_module("mcp")
        assert source is not real_mcp_sdk
        assert source.__name__ not in ("mcp", "mcp.server.fastmcp")
        # FastMCP is the SDK's signature export; our source modules never
        # define or re-export it.
        assert not hasattr(source, "FastMCP")


# ── Empty inbox / fetch failure ──────────────────────────────────────────────

class TestPollImapInboxEmptyOrFailingInbox:

    def test_empty_inbox_completes_without_error_or_processing(self, wired_engine):
        fake_source = MagicMock()
        fake_source.get_latest_orders.return_value = []

        with patch.object(jobs, "_load_imap_source", return_value=fake_source), \
             patch("api.app._process_inbound") as mock_process:
            jobs._poll_imap_inbox()  # must not raise

        mock_process.assert_not_called()

    def test_source_fetch_error_is_caught_not_raised(self, wired_engine):
        fake_source = MagicMock()
        fake_source.get_latest_orders.side_effect = RuntimeError("mailbox unreachable")

        with patch.object(jobs, "_load_imap_source", return_value=fake_source), \
             patch("api.app._process_inbound") as mock_process:
            jobs._poll_imap_inbox()  # must not raise, error is logged internally

        mock_process.assert_not_called()


# ── _push_to_websockets from worker threads / concurrently ──────────────────
#
# ADR 0002 open question #3 asked whether `_push_to_websockets`'s
# `asyncio.get_event_loop()` call behaved correctly when `_process_inbound`
# runs on an APScheduler worker thread (the new `_poll_imap_inbox` call
# site), as opposed to FastAPI's own BackgroundTasks threadpool (the
# pre-existing `/internal/ingest` call site). It did not: a debug pass
# empirically confirmed (Python 3.11.7, APScheduler 3.10.4) that
# `asyncio.get_event_loop()` raises `RuntimeError` on a thread with no loop
# set, that `_push_to_websockets` swallowed that error, silently dropping
# the `NEW_MATCHES` payload and wrongly evicting the healthy websocket from
# `active_websockets`. That finding is what ADR 0003
# (`.claude/decisions/0003-websocket-broadcast-thread-safety.md`) fixes:
# `middleware/api/app.py` now captures the main thread's running loop into a
# module-level `_main_event_loop` at `lifespan` startup and routes every
# broadcast through `asyncio.run_coroutine_threadsafe(_broadcast_new_matches(payload),
# _main_event_loop)`, with a guard that no-ops-and-warns if `_main_event_loop`
# is `None` or closed.
#
# `test_get_event_loop_raises_on_thread_with_no_loop` below documents a
# standard-library fact that remains true after the fix (and is exactly why
# the fix needed a captured loop reference in the first place) — left as-is,
# not inverted. The rest of this class exercises the real, fixed `app.py`
# code end to end: a no-loop-yet no-op, a positive delivery from a worker
# thread, a same-thread call proving `run_coroutine_threadsafe` is
# thread-agnostic, a concurrent multi-thread regression test for the
# confinement-based thread-safety fix, and a stale/closed-loop no-op.

class _FakeField:
    def __init__(self, value=None, confidence=0.9):
        self.value = value
        self.confidence = confidence


class _FakeParsed:
    def __init__(self):
        self.cargo_type = _FakeField("grain")
        self.quantity_mt = _FakeField(55000)
        self.load_port_canonical = "Antwerp"
        self.load_port = _FakeField("Antwerp")
        self.discharge_port_canonical = "Japan"
        self.discharge_port = _FakeField("Japan")
        self.laycan_start = _FakeField("2024-08-10")
        self.laycan_end = _FakeField("2024-08-20")
        self.parse_confidence = 0.9
        self.has_low_confidence_fields = False
        self.low_confidence_field_names = []
        self.parse_status = "success"
        self.error = None


class _FakeWebSocket:
    def __init__(self):
        self.delivered = []

    async def send_json(self, payload):
        self.delivered.append(payload)


def _wait_until(predicate, timeout=5.0, interval=0.01):
    """Poll `predicate` until it's truthy or `timeout` seconds elapse.

    `_push_to_websockets` is fire-and-forget by design (ADR 0003 decision
    #4) — it schedules `_broadcast_new_matches` on the main loop and returns
    without waiting for it, so tests that assert on delivery need to poll
    rather than assume delivery happened synchronously.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestPushToWebsocketsFromWorkerThread:

    @pytest.fixture
    def app_module(self):
        """Snapshot and restore api.app's module-level websocket broadcast
        state (`active_websockets`, `_main_event_loop`) so these tests can
        freely mutate it without bleeding into other tests."""
        import api.app as app

        original_websockets = list(app.active_websockets)
        original_loop = app._main_event_loop
        yield app
        app.active_websockets.clear()
        app.active_websockets.extend(original_websockets)
        app._main_event_loop = original_loop

    @pytest.fixture
    def running_loop_thread(self):
        """A real asyncio event loop running on a dedicated background
        thread, standing in for "the main thread's loop" the way
        `_main_event_loop` is captured from `lifespan` in production.
        `run_coroutine_threadsafe` only cares that the target loop is
        running, not which thread it lives on (ADR 0003, Reasoning)."""
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        yield loop
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()

    def test_get_event_loop_raises_on_thread_with_no_loop(self):
        """Baseline: confirms the actual Python/asyncio behavior in this
        environment (Python 3.11.7) rather than assuming it — the ADR
        explicitly flagged that this behavior has changed across Python
        versions. Remains true after the fix (`app.py` no longer calls
        `asyncio.get_event_loop()` anywhere) — this documents why the fix
        needed a captured loop reference in the first place, so it is left
        as-is, not inverted."""
        result = {}

        def worker():
            try:
                result["loop"] = asyncio.get_event_loop()
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert "error" in result, (
            "expected asyncio.get_event_loop() to raise on a fresh "
            "non-main thread with no loop set; if this now returns a loop "
            "instead, the Python/asyncio behavior has changed and the "
            "finding below needs re-verifying"
        )
        assert isinstance(result["error"], RuntimeError)
        assert "no current event loop" in str(result["error"]).lower()

    def test_broadcast_skipped_gracefully_when_no_main_loop_captured(self, app_module):
        """No-loop-yet: `_main_event_loop` is still `None` (broadcast
        attempted before `lifespan` ran). Must no-op safely: no exception,
        no eviction, no delivery, and a warning logged (ADR 0003 decision
        #4)."""
        app = app_module
        app._main_event_loop = None

        fake_ws = _FakeWebSocket()
        app.active_websockets.clear()
        app.active_websockets.append(fake_ws)

        def call_from_worker_thread():
            assert threading.current_thread() is not threading.main_thread()
            app._push_to_websockets(uuid.uuid4(), _FakeParsed(), [], None)

        with capture_logs() as logs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(call_from_worker_thread)
                raised = future.exception()

        assert raised is None
        assert app.active_websockets == [fake_ws]
        assert fake_ws.delivered == []
        assert any(
            e["log_level"] == "warning"
            and e["event"] == "websocket.broadcast_skipped"
            for e in logs
        ), f"expected a websocket.broadcast_skipped warning, got: {logs}"

    def test_broadcast_delivers_from_worker_thread_when_main_loop_is_running(
        self, app_module, running_loop_thread
    ):
        """Positive delivery: `_main_event_loop` points at a real, running
        loop (on its own dedicated thread, playing the role of the main
        thread's loop). Calling `_push_to_websockets` from a separate
        ThreadPoolExecutor worker (simulating `/internal/ingest` or
        `_poll_imap_inbox`) must actually deliver the NEW_MATCHES payload
        and must not evict the healthy socket."""
        app = app_module
        app._main_event_loop = running_loop_thread

        fake_ws = _FakeWebSocket()
        app.active_websockets.clear()
        app.active_websockets.append(fake_ws)

        def call_from_worker_thread():
            assert threading.current_thread() is not threading.main_thread()
            app._push_to_websockets(uuid.uuid4(), _FakeParsed(), [], None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(call_from_worker_thread)
            future.result(timeout=5)

        assert _wait_until(lambda: fake_ws.delivered != []), (
            "NEW_MATCHES payload was never delivered to the fake websocket"
        )
        assert len(fake_ws.delivered) == 1
        assert fake_ws.delivered[0]["type"] == "NEW_MATCHES"
        assert app.active_websockets == [fake_ws]

    def test_broadcast_delivers_when_called_from_main_loops_own_thread(
        self, app_module, running_loop_thread
    ):
        """Main-thread call: `_push_to_websockets` is called from inside a
        coroutine already running on the same loop set as
        `_main_event_loop` — no separate thread involved. Proves
        `run_coroutine_threadsafe` works correctly even when called from the
        target loop's own thread (ADR 0003, Reasoning)."""
        app = app_module
        app._main_event_loop = running_loop_thread

        fake_ws = _FakeWebSocket()
        app.active_websockets.clear()
        app.active_websockets.append(fake_ws)

        async def call_on_loop():
            assert asyncio.get_running_loop() is running_loop_thread
            app._push_to_websockets(uuid.uuid4(), _FakeParsed(), [], None)

        future = asyncio.run_coroutine_threadsafe(call_on_loop(), running_loop_thread)
        future.result(timeout=5)  # call_on_loop (the sync-looking call) has returned

        assert _wait_until(lambda: fake_ws.delivered != []), (
            "NEW_MATCHES payload was never delivered when scheduled from "
            "the main loop's own thread"
        )
        assert len(fake_ws.delivered) == 1
        assert app.active_websockets == [fake_ws]

    def test_concurrent_broadcasts_and_connect_disconnect_do_not_race(
        self, app_module, running_loop_thread
    ):
        """Concurrent multi-thread broadcast: two worker threads push at
        roughly the same time (simulating a `/internal/ingest` push and an
        `_poll_imap_inbox` push landing together) while a connect/disconnect
        on `active_websockets` happens interleaved, scheduled onto the same
        main loop the way `websocket_endpoint` really runs. This is the
        regression test for the confinement-based thread-safety fix
        (Decision #3): no ValueError/RuntimeError should escape from list
        mutation, and final membership should be correct."""
        app = app_module
        app._main_event_loop = running_loop_thread

        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        app.active_websockets.clear()
        app.active_websockets.append(ws_a)

        errors = []

        def push_from_worker():
            try:
                app._push_to_websockets(uuid.uuid4(), _FakeParsed(), [], None)
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        def connect_on_main_loop(ws):
            async def _connect():
                app.active_websockets.append(ws)

            try:
                asyncio.run_coroutine_threadsafe(
                    _connect(), running_loop_thread
                ).result(timeout=5)
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        def disconnect_on_main_loop(ws):
            async def _disconnect():
                if ws in app.active_websockets:
                    app.active_websockets.remove(ws)

            try:
                asyncio.run_coroutine_threadsafe(
                    _disconnect(), running_loop_thread
                ).result(timeout=5)
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = [
                ex.submit(push_from_worker),          # e.g. /internal/ingest
                ex.submit(push_from_worker),           # e.g. _poll_imap_inbox
                ex.submit(connect_on_main_loop, ws_b),
                ex.submit(disconnect_on_main_loop, ws_a),
            ]
            for f in futures:
                f.result(timeout=5)

        # Membership-mutating calls (connect/disconnect) are synchronous
        # above via .result(); delivery from the fire-and-forget pushes may
        # still be in flight, so poll before asserting on it.
        _wait_until(lambda: ws_a.delivered or ws_b.delivered, timeout=5)

        assert errors == [], f"list mutation raised: {errors}"
        assert ws_a not in app.active_websockets
        assert ws_b in app.active_websockets
        # At least one of the two concurrent pushes should have reached a
        # connected socket — proves broadcasts kept working under
        # concurrent connect/disconnect, without over-specifying exact
        # per-socket counts given the intentionally racy interleaving.
        assert len(ws_a.delivered) + len(ws_b.delivered) >= 1

    def test_broadcast_no_ops_gracefully_when_main_loop_is_closed(self, app_module):
        """Stale/closed loop: `_main_event_loop` points at a loop that has
        since been closed (app shutting down). Must no-op gracefully — no
        exception, no delivery, a warning logged."""
        app = app_module
        stale_loop = asyncio.new_event_loop()
        stale_loop.close()
        app._main_event_loop = stale_loop

        fake_ws = _FakeWebSocket()
        app.active_websockets.clear()
        app.active_websockets.append(fake_ws)

        def call_from_worker_thread():
            app._push_to_websockets(uuid.uuid4(), _FakeParsed(), [], None)

        with capture_logs() as logs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(call_from_worker_thread)
                raised = future.exception()

        assert raised is None
        assert app.active_websockets == [fake_ws]
        assert fake_ws.delivered == []
        assert any(
            e["log_level"] == "warning"
            and e["event"] == "websocket.broadcast_skipped"
            for e in logs
        ), f"expected a websocket.broadcast_skipped warning, got: {logs}"
