"""
tests/test_signal_cache_refresh.py — tests for
signal_client/client.py::SignalCacheClient.refresh() / _prune_stale().

Covers the bug found by the briefing agent and confirmed by direct code
tracing in the real local Postgres DB: the per-vessel-class fetch loop in
refresh() used to swallow every exception, log the cycle as success=True
regardless of how many classes actually failed, and unconditionally run
_prune_stale() against whatever partial/empty result the broken fetch
produced — deleting cached vessels that simply failed to fetch, not ones
that genuinely disappeared from the market. 7 consecutive refresh cycles in
that DB all logged success=true, vessel_count=0, and the first of those
cycles deleted all 14 previously-cached seed vessels.

The fix (see signal_client/client.py::refresh()):
  - success is now True only when zero vessel classes failed to fetch.
  - _prune_stale() only runs when success is True — a partial or total
    per-class failure skips pruning entirely for that cycle, trading one
    extra cycle of staleness for never wiping real cached data based on
    incomplete fetch results.
  - error_message on a partial failure names exactly which classes failed
    and why, so it's visibly distinct from both a clean success
    (error_message=None) and a total pre-loop failure (e.g. _get_api()
    connection errors, already handled by the outer except before this fix
    and unchanged here).

Distinguished from middleware/tests/test_signal_client_mcp.py, which covers
the different, unrelated middleware/mcp/signal_client.py module (the
SIGNAL_MODE=live MCP backend) — that file's docstring's claim that
test_core.py covers *this* module (signal_client/client.py) is stale/
inaccurate; grepping confirms neither test_core.py nor any other existing
test file exercises SignalCacheClient.refresh()/_prune_stale() before this
file was added.

Mocking approach: unlike test_signal_client_mcp.py (which constructs real
signal_ocean SDK dataclass instances to keep its field-mapping assertions
honest against SDK renames), this file drives SignalCacheClient._get_api()
with a plain unittest.mock.MagicMock/SimpleNamespace fake. That's a
deliberate, narrower choice: this fix is entirely about the *control flow*
around per-class failures (does a raised exception get counted, does
success reflect it, does pruning get gated on it) — not about whether any
particular SDK field name is mapped correctly. A hand-rolled fake with a
few guessed attribute names (imo, name, deadweight, ...) is enough to drive
_upsert_vessel's getattr(..., None) calls deterministically and is far
simpler to read here than building full SDK dataclasses for a control-flow
test.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, CachedVessel, SignalCacheRefresh, get_session
from signal_client.client import SignalCacheClient

# The exact seven classes refresh() iterates today (signal_client/client.py).
TARGET_CLASSES = [
    "Aframax", "Suezmax", "VLCC", "Panamax", "Capesize", "Handymax", "Handysize"
]


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
def session(sqlite_engine):
    s = get_session(sqlite_engine)
    yield s
    s.close()


def _make_fake_api(fail_classes: set[str]):
    """
    A MagicMock-free fake api object driving get_vessel_classes/
    get_tonnage_list deterministically per vessel class, without needing
    real signal_ocean SDK dataclasses (see module docstring for why this
    is a proportionate choice for a control-flow-focused fix).

    refresh() calls get_vessel_classes(class_filter) then
    get_tonnage_list(...) once per class_name, strictly in sequence, so a
    single-slot "current class" closure is sufficient to correlate the two
    calls without needing to inspect real VesselClassFilter internals.
    """
    state = {"class_name": None}

    class FakeApi:
        def get_vessel_classes(self, class_filter):
            state["class_name"] = class_filter.name_like
            return [SimpleNamespace(name=class_filter.name_like)]

        def get_tonnage_list(self, loading_port, vessel_class, laycan_end_in_days, vessel_filter):
            name = state["class_name"]
            if name in fail_classes:
                raise RuntimeError(f"simulated fetch failure for {name}")
            fake_vessel = SimpleNamespace(
                imo=f"{name}-IMO",
                name=f"{name} Vessel",
                deadweight=100_000.0,
                year_built=2015,
                open_port_name="Rotterdam",
                open_narrow_area="ARA",
                open_date=datetime.now(timezone.utc),
                commercial_status="CommercialStatus.AVAILABLE",
                market_deployment="MarketDeployment.SPOT",
                latitude=51.9,
                longitude=4.5,
                latest_ais=datetime.now(timezone.utc),
            )
            return SimpleNamespace(vessels=[fake_vessel])

    return FakeApi()


def _client_with_fake_api(session, fail_classes: set[str]) -> SignalCacheClient:
    client = SignalCacheClient(session)
    fake_api = _make_fake_api(fail_classes)
    # Instance attribute shadows the bound method — refresh() calls
    # self._get_api(), which finds this first, so no real Connection/
    # TonnageListAPI network call ever happens.
    client._get_api = lambda: fake_api
    return client


def _seed_stale_vessel(session, vessel_id="STALE-VESSEL"):
    v = CachedVessel(
        id=uuid.uuid4(),
        vessel_id=vessel_id,
        vessel_name="Stale Ghost",
        vessel_class="Aframax",
        dwt=105_000.0,
        commercial_status="CommercialStatus.AVAILABLE",
        cached_at=datetime.now(timezone.utc),
    )
    session.add(v)
    session.commit()
    return vessel_id


def _latest_refresh_row(session) -> SignalCacheRefresh:
    return (
        session.query(SignalCacheRefresh)
        .order_by(SignalCacheRefresh.refreshed_at.desc())
        .first()
    )


# ── 1. All classes succeed ──────────────────────────────────────────────────

def test_all_classes_succeed_logs_success_and_prunes(session):
    stale_id = _seed_stale_vessel(session)
    client = _client_with_fake_api(session, fail_classes=set())

    count = client.refresh()

    assert count == len(TARGET_CLASSES)

    row = _latest_refresh_row(session)
    assert row.success is True
    assert row.error_message is None
    assert row.vessel_count == len(TARGET_CLASSES)

    # Pruning ran: the stale seed vessel (not in this cycle's fetch) is gone.
    assert session.query(CachedVessel).filter_by(vessel_id=stale_id).first() is None

    # Every fetched class made it into the cache.
    for name in TARGET_CLASSES:
        assert session.query(CachedVessel).filter_by(vessel_id=f"{name}-IMO").first() is not None


# ── 2. Some classes fail, some succeed ──────────────────────────────────────

def test_partial_failure_logs_failure_and_skips_prune(session):
    stale_id = _seed_stale_vessel(session)
    failing = {"Suezmax", "VLCC"}
    client = _client_with_fake_api(session, fail_classes=failing)

    count = client.refresh()

    expected_success_count = len(TARGET_CLASSES) - len(failing)
    assert count == expected_success_count

    row = _latest_refresh_row(session)
    assert row.success is False
    assert row.vessel_count == expected_success_count
    assert row.error_message is not None
    for name in failing:
        assert name in row.error_message

    # Core regression check: pruning was skipped, so the pre-existing
    # cached vessel survives even though it wasn't in this cycle's
    # (partial) fetch results.
    assert session.query(CachedVessel).filter_by(vessel_id=stale_id).first() is not None

    # Vessels from classes that DID succeed are still upserted.
    for name in TARGET_CLASSES:
        row_v = session.query(CachedVessel).filter_by(vessel_id=f"{name}-IMO").first()
        if name in failing:
            assert row_v is None
        else:
            assert row_v is not None


# ── 3. All classes fail ─────────────────────────────────────────────────────

def test_all_classes_fail_logs_failure_and_skips_prune(session):
    stale_id = _seed_stale_vessel(session)
    client = _client_with_fake_api(session, fail_classes=set(TARGET_CLASSES))

    count = client.refresh()

    assert count == 0

    row = _latest_refresh_row(session)
    assert row.success is False
    assert row.vessel_count == 0
    assert row.error_message is not None
    assert f"{len(TARGET_CLASSES)}/{len(TARGET_CLASSES)}" in row.error_message

    # The exact real-DB scenario this fix targets: a total per-class-fetch
    # failure must not wipe previously-cached, still-real data.
    assert session.query(CachedVessel).filter_by(vessel_id=stale_id).first() is not None
    assert session.query(CachedVessel).count() == 1


# ── 4. get_last_refresh() reflects genuine success only ─────────────────────

def test_get_last_refresh_ignores_failed_cycles(session):
    # No refresh rows at all yet → None.
    client = _client_with_fake_api(session, fail_classes=set(TARGET_CLASSES))
    assert client.get_last_refresh() is None

    # A genuinely successful refresh happened earlier...
    earlier = datetime.now(timezone.utc) - timedelta(hours=2)
    session.add(SignalCacheRefresh(
        refreshed_at=earlier,
        vessel_count=14,
        success=True,
        error_message=None,
        duration_ms=500,
    ))
    session.commit()

    # ...then every class fails on this cycle.
    client.refresh()

    # get_last_refresh() must still report the earlier genuine success,
    # not the just-logged failed cycle (and never a mislabeled-successful
    # empty row, which is exactly what the pre-fix code would have logged
    # here instead).
    last = client.get_last_refresh()
    assert last is not None
    # In-memory sqlite's DateTime(timezone=True) column returns a naive
    # datetime (unlike real Postgres), so compare naively — same divergence
    # documented in test_email_threading.py/test_mark_order_read.py.
    last_naive = last.replace(tzinfo=None) if last.tzinfo else last
    earlier_naive = earlier.replace(tzinfo=None)
    assert abs((last_naive - earlier_naive).total_seconds()) < 1
