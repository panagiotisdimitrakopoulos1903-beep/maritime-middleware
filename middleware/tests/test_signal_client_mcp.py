"""
tests/test_signal_client_mcp.py — tests for middleware/mcp/signal_client.py,
the SIGNAL_MODE=live backend for the Signal Ocean MCP server
(middleware/mcp/signal_server.py).

Distinguished from middleware/tests/test_core.py, which covers
middleware/signal_client/client.py (the *other*, unrelated module used by
the FastAPI matching pipeline — see signal_client.py's own module docstring
for why these two same-named-but-different modules coexist). This file has
no prior coverage; SIGNAL_MODE=live has never had automated tests before.

No real Signal Ocean API key is available in this environment, so nothing
here is end-to-end integration testing. Tests mock the SDK's `Connection` /
`TonnageListAPI` / `DistancesAPI` classes and construct real SDK dataclass
instances (`signal_ocean.tonnage_list.models.Vessel`/`Area`/`Port`,
`signal_ocean.distances.Port`, etc.) to drive the mapping/logic code
honestly — a real SDK field rename would break these tests, unlike a
hand-rolled fake object with guessed attribute names.

═══════════════════════════════════════════════════════════════════════════
OPEN QUESTIONS REQUIRING REAL API KEY VERIFICATION BEFORE PRODUCTION TRUST
(blocking full confidence in SIGNAL_MODE=live; see coder pass report and
 signal_client.py's own inline comments for the reasoning behind each)
═══════════════════════════════════════════════════════════════════════════
1. `refresh()`'s `loading_port` anchor: resolves an arbitrary anchor port
   (the first port `get_ports()` happens to return) to satisfy the real
   SDK's non-optional `Port` argument on `get_tonnage_list()`, because this
   wrapper's own function signature has no caller-supplied port to use
   instead. Whether an arbitrary anchor port yields correct/complete
   tonnage list results from the real API is UNVERIFIED.
2. `get_distances()`'s `loading_condition_id=LoadingCondition.BALLAST`
   default: a design guess (vessel sails to load empty from its open
   position), not verified against real API output or a maritime domain
   expert.
3. `get_distances()`'s hardcoded `"Aframax"` vessel_class stand-in: the
   wrapper's own signature has no vessel-class parameter, so a fixed value
   was picked on the reasoning that distance is dominated by geography, not
   vessel class — also UNVERIFIED.

The three characterization tests in `TestUnverifiedLiveModeAssumptions`
below pin down CURRENT behavior for each of these, not correctness. Do not
read a passing test there as "confirmed right" — read it as "this is what
the code does today."
═══════════════════════════════════════════════════════════════════════════

Run with: pytest tests/ -v
"""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from config import settings
from database.models import Base, CachedVessel, get_session

from signal_ocean.tonnage_list.models import (
    Area as SOArea,
    Port as SOPort,
    TonnageList,
    Vessel as SOVessel,
    VesselClass as SOVesselClass,
)
from signal_ocean.distances import (
    LoadingCondition,
    Port as DistPort,
    VesselClass as DistVesselClass,
)


# ── Load middleware/mcp/signal_client.py under a private module name ───────
#
# Bare `import signal_client` is genuinely ambiguous inside this test
# process (unlike production, where mcp/signal_server.py always runs as its
# own process with middleware/mcp/ as sys.path[0] — see signal_client.py's
# own top-of-file comment): middleware/signal_client/ is a *different*,
# unrelated namespace package (middleware/signal_client/client.py, used by
# test_core.py's `from signal_client.client import VesselSnapshot`). If any
# test in this session imports that first, "signal_client" gets cached in
# sys.modules as that namespace package, and a later bare
# `importlib.import_module("signal_client")` would silently hand back the
# wrong module instead of raising. Loading straight from this file's path
# under a distinct sys.modules key sidesteps the ambiguity entirely,
# regardless of test collection/execution order.
_SIGNAL_CLIENT_MCP_PATH = (
    Path(__file__).resolve().parent.parent / "mcp" / "signal_client.py"
)
_spec = importlib.util.spec_from_file_location(
    "mcp_signal_client_live_under_test", _SIGNAL_CLIENT_MCP_PATH
)
sc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sc
_spec.loader.exec_module(sc)

# Same bare-name ambiguity applies to `mcp.mock_vessels` (the installed
# `mcp` SDK package shadows middleware/mcp/ for any `import mcp....`) — load
# it the same way, purely to read MOCK_VESSELS' key set for the shape-parity
# assertion in TestRowToDict.
_MOCK_VESSELS_PATH = (
    Path(__file__).resolve().parent.parent / "mcp" / "mock_vessels.py"
)
_mv_spec = importlib.util.spec_from_file_location(
    "mcp_mock_vessels_under_test", _MOCK_VESSELS_PATH
)
_mock_vessels_module = importlib.util.module_from_spec(_mv_spec)
sys.modules[_mv_spec.name] = _mock_vessels_module
_mv_spec.loader.exec_module(_mock_vessels_module)
MOCK_VESSELS = _mock_vessels_module.MOCK_VESSELS


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sqlite_engine():
    """Fresh in-memory sqlite DB per test (StaticPool: one live connection,
    so every get_session() call sees the same DB — mirrors
    test_scheduler_jobs.py's fixture)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def wired_engine(monkeypatch, sqlite_engine):
    """Point sc's module-level _engine at our sqlite DB, same convention as
    test_scheduler_jobs.py's `wired_engine` fixture for scheduler.jobs."""
    monkeypatch.setattr(sc, "_engine", sqlite_engine)
    return sqlite_engine


def _make_sdk_vessel(
    imo=9123456,
    name="MV Test Aframax",
    vessel_class="Aframax",
    deadweight=105000,
    open_port="Ras Tanura",
    open_date=None,
    commercial_status="Available",
    market_deployment="Spot",
    open_areas=(),
):
    """Construct a real signal_ocean Vessel dataclass instance (all 28
    required positional fields) rather than a fake/mock object with guessed
    attributes, so a future SDK field rename breaks this test honestly."""
    return SOVessel(
        imo=imo,
        name=name,
        vessel_class=vessel_class,
        ice_class=None,
        year_built=2015,
        deadweight=deadweight,
        length_overall=250.0,
        breadth_extreme=44,
        market_deployment=market_deployment,
        push_type="Pushed",
        open_port_id=1,
        open_port=open_port,
        open_date=open_date,
        operational_status="Trading",
        commercial_operator_id=1,
        commercial_operator="Test Operator",
        commercial_status=commercial_status,
        eta=None,
        latest_ais=None,
        subclass="Dirty",
        willing_to_switch_subclass=False,
        open_prediction_accuracy="Confirmed",
        open_areas=open_areas,
        availability_port_type="Port",
        availability_date_type="Confirmed",
        fixture_type="Time Charter",
        current_vessel_sub_type_id=1,
        current_vessel_sub_type="Dirty",
        willing_to_switch_current_vessel_sub_type=False,
    )


def _wire_tonnage_list_api(monkeypatch, anchor_ports, vessels_by_class, api_mock=None):
    """Patch sc.TonnageListAPI so refresh() drives against a controlled
    fake API surface instead of the network. `vessels_by_class` maps a
    vessel-class name to (SOVesselClass, tuple_of_vessels); any class name
    from sc.get_vessel_classes() not present in the map resolves to no
    vessel classes found (mirrors a real "class not found" response),
    causing refresh()'s per-class loop to skip it via `continue`."""
    api = api_mock or MagicMock()
    api.get_ports.return_value = anchor_ports

    def _get_vessel_classes(filt):
        entry = vessels_by_class.get(filt.name_like)
        return (entry[0],) if entry else ()

    def _get_tonnage_list(loading_port, vessel_class, laycan_end_in_days=None, vessel_filter=None):
        for class_name, (so_class, vessels) in vessels_by_class.items():
            if so_class is vessel_class:
                return TonnageList(vessels=vessels, date=datetime.now(timezone.utc))
        return TonnageList(vessels=(), date=datetime.now(timezone.utc))

    api.get_vessel_classes.side_effect = _get_vessel_classes
    api.get_tonnage_list.side_effect = _get_tonnage_list

    tonnage_list_api_cls = MagicMock(return_value=api)
    monkeypatch.setattr(sc, "TonnageListAPI", tonnage_list_api_cls)
    return api, tonnage_list_api_cls


# ── 1a. _row_to_dict(): CachedVessel row -> MCP dict ────────────────────────

class TestRowToDict:

    def _make_row(self, **overrides):
        defaults = dict(
            vessel_id="9123456",
            vessel_name="MV Test Aframax",
            vessel_class="Aframax",
            dwt=105000.0,
            open_port="Ras Tanura",
            open_port_area="Middle East Gulf",
            open_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            commercial_status="Available",
            market_deployment="Spot",
            latitude=None,
            longitude=None,
            cargo_types=None,
        )
        defaults.update(overrides)
        return CachedVessel(**defaults)

    def test_maps_scalar_fields_correctly(self):
        row = self._make_row()
        result = sc._row_to_dict(row)

        assert result["vessel_id"] == "9123456"
        assert result["vessel_name"] == "MV Test Aframax"
        assert result["vessel_class"] == "Aframax"
        assert result["dwt"] == 105000.0
        assert result["open_port"] == "Ras Tanura"
        assert result["open_port_area"] == "Middle East Gulf"
        assert result["open_date"] == "2026-08-01T00:00:00+00:00"
        assert result["commercial_status"] == "Available"
        assert result["market_deployment"] == "Spot"

    def test_open_date_none_maps_to_none_not_empty_string(self):
        row = self._make_row(open_date=None)
        result = sc._row_to_dict(row)
        assert result["open_date"] is None

    def test_latitude_longitude_cargo_types_are_explicit_none(self):
        """These three fields are deliberately always None on the live path
        (not available on the real SDK's Vessel model) — must come through
        as an explicit None key, not be omitted or replaced with some other
        placeholder like [] or 0.0."""
        row = self._make_row()
        result = sc._row_to_dict(row)

        assert "latitude" in result and result["latitude"] is None
        assert "longitude" in result and result["longitude"] is None
        assert "cargo_types" in result and result["cargo_types"] is None

    def test_dict_keys_match_mock_vessels_shape_exactly(self):
        """signal_server.py passes this dict straight through to MCP tool
        callers with zero transformation, so its key set must exactly match
        mock_vessels.MOCK_VESSELS' shape for SIGNAL_MODE to be a
        config-only swap, per signal_client.py's own module docstring."""
        row = self._make_row()
        result = sc._row_to_dict(row)
        assert set(result.keys()) == set(MOCK_VESSELS[0].keys())


# ── 1b. refresh(): SDK Vessel -> CachedVessel row mapping ───────────────────

class TestRefreshFieldMapping:

    def test_vessel_fields_mapped_correctly_into_cached_vessel(self, monkeypatch, wired_engine):
        area = SOArea(id=10, name="Middle East Gulf", location_taxonomy="Region", taxonomy_id=1)
        vessel = _make_sdk_vessel(
            imo=9111111,
            name="MV Gulf Runner",
            vessel_class="Aframax",
            deadweight=104500,
            open_port="Fujairah",
            open_date=datetime(2026, 8, 5, tzinfo=timezone.utc),
            commercial_status="Available",
            market_deployment="Spot",
            open_areas=(area,),
        )
        so_class = SOVesselClass(id=1, name="Aframax")
        anchor_port = SOPort(id=1, name="Anchor Port")

        _wire_tonnage_list_api(
            monkeypatch,
            anchor_ports=(anchor_port,),
            vessels_by_class={"Aframax": (so_class, (vessel,))},
        )
        # get_vessel_classes(name_like=...) is called once per name in
        # sc.get_vessel_classes(); only "Aframax" resolves, others no-op.
        monkeypatch.setattr(sc, "get_vessel_classes", lambda: ["Aframax"])

        result = sc.refresh()

        assert result["success"] is True
        assert result["vessel_count"] == 1

        session = get_session(wired_engine)
        try:
            rows = session.query(CachedVessel).all()
            assert len(rows) == 1
            row = rows[0]
            assert row.vessel_id == "9111111"
            assert row.vessel_name == "MV Gulf Runner"
            assert row.vessel_class == "Aframax"
            assert row.dwt == 104500
            assert row.open_port == "Fujairah"
            assert row.open_port_area == "Middle East Gulf"
            # sqlite's DateTime column type round-trips as a naive datetime
            # (no tz-awareness in the sqlite driver) — compare the naive
            # wall-clock value, not tzinfo, which is a storage-layer detail
            # unrelated to the mapping logic under test.
            assert row.open_date == datetime(2026, 8, 5)
            assert row.commercial_status == "Available"
            assert row.market_deployment == "Spot"
            assert row.latitude is None
            assert row.longitude is None
            assert row.cargo_types is None
        finally:
            session.close()

    def test_empty_open_areas_does_not_crash_and_maps_to_none(self, monkeypatch, wired_engine):
        """Guards against `open_areas[0]` on an empty tuple — a vessel with
        no open area data must map to open_port_area=None, not raise
        IndexError."""
        vessel = _make_sdk_vessel(imo=9222222, name="MV No Area", open_areas=())
        so_class = SOVesselClass(id=2, name="Aframax")
        anchor_port = SOPort(id=1, name="Anchor Port")

        _wire_tonnage_list_api(
            monkeypatch,
            anchor_ports=(anchor_port,),
            vessels_by_class={"Aframax": (so_class, (vessel,))},
        )
        monkeypatch.setattr(sc, "get_vessel_classes", lambda: ["Aframax"])

        result = sc.refresh()  # must not raise IndexError
        assert result["success"] is True

        session = get_session(wired_engine)
        try:
            row = session.query(CachedVessel).filter(CachedVessel.vessel_id == "9222222").first()
            assert row is not None
            assert row.open_port_area is None
        finally:
            session.close()

    def test_no_anchor_ports_returns_unsuccessful_result_without_crashing(self, monkeypatch, wired_engine):
        api = MagicMock()
        api.get_ports.return_value = ()
        monkeypatch.setattr(sc, "TonnageListAPI", MagicMock(return_value=api))

        result = sc.refresh()

        assert result["success"] is False
        assert result["vessel_count"] == 0
        assert "no ports" in result["error"].lower()


# ── 2. Session handling: lazy engine caching ────────────────────────────────

class TestEngineSessionCaching:

    def test_get_engine_builds_lazily_and_caches_across_calls(self, monkeypatch, sqlite_engine):
        monkeypatch.setattr(sc, "_engine", None)
        mock_get_engine = MagicMock(return_value=sqlite_engine)
        monkeypatch.setattr(sc, "get_engine", mock_get_engine)

        first = sc._get_engine()
        second = sc._get_engine()

        assert first is sqlite_engine
        assert second is sqlite_engine
        # Not rebuilt on the second call — this is the whole point of the
        # module-level _engine cache.
        mock_get_engine.assert_called_once_with(settings.database_url)

    def test_session_is_bound_to_the_cached_engine(self, monkeypatch, wired_engine):
        session = sc._session()
        try:
            assert session.get_bind() is wired_engine
        finally:
            session.close()


# ── 3a. get_ports(): rebuild from core.ports.PORT_TO_AREA ───────────────────

class TestGetPorts:

    def test_no_query_returns_full_shape_from_port_to_area(self):
        from core.ports import PORT_TO_AREA

        result = sc.get_ports()

        assert len(result) == len(PORT_TO_AREA)
        for entry in result:
            assert set(entry.keys()) == {"name", "area"}
        result_map = {p["name"]: p["area"] for p in result}
        assert result_map == PORT_TO_AREA

    def test_exact_name_match(self):
        result = sc.get_ports("Rotterdam")
        names = {p["name"] for p in result}
        assert "Rotterdam" in names

    def test_partial_substring_name_match(self):
        result = sc.get_ports("otterda")
        names = {p["name"] for p in result}
        assert "Rotterdam" in names

    def test_case_insensitive_query(self):
        """get_ports() lowercases both the query and the candidate name/area
        before comparing (`q in p["name"].lower()`), so an uppercase query
        must still match."""
        result = sc.get_ports("ROTTERDAM")
        names = {p["name"] for p in result}
        assert "Rotterdam" in names

    def test_query_matches_on_area_too(self):
        result = sc.get_ports("Middle East Gulf")
        areas = {p["area"] for p in result}
        assert areas == {"Middle East Gulf"}
        names = {p["name"] for p in result}
        assert "Fujairah" in names

    def test_no_match_returns_empty_list(self):
        result = sc.get_ports("ZzzNoSuchPlaceAnywhere")
        assert result == []


# ── 3b. _resolve_distances_port(): exact-match-then-substring-fallback ──────

class TestResolveDistancesPort:

    def _ports(self):
        return [
            DistPort(id=1, name="Rotterdam"),
            DistPort(id=2, name="Rotterdam Europoort"),
            DistPort(id=3, name="Singapore"),
        ]

    def test_exact_match_found(self):
        result = sc._resolve_distances_port(self._ports(), "Rotterdam")
        assert result.id == 1
        assert result.name == "Rotterdam"

    def test_exact_match_is_case_insensitive(self):
        result = sc._resolve_distances_port(self._ports(), "rotterdam")
        assert result.id == 1

    def test_substring_fallback_when_no_exact_match(self):
        """'Europoort' isn't an exact name for any port, but it is a
        substring of 'Rotterdam Europoort' — falls back to that."""
        result = sc._resolve_distances_port(self._ports(), "Europoort")
        assert result.id == 2
        assert result.name == "Rotterdam Europoort"

    def test_no_match_at_all_returns_none(self):
        """Documented current failure behavior: returns None (caller,
        get_distances(), turns this into a `distance_nm: None` + note
        response rather than raising)."""
        result = sc._resolve_distances_port(self._ports(), "Nowhereville")
        assert result is None


# ── Characterization tests: unverified live-mode design assumptions ────────
#
# These pin down CURRENT behavior only — see the module-level "OPEN
# QUESTIONS" block at the top of this file. None of these assertions should
# be read as "this is correct"; they exist so a future change to this
# behavior is a deliberate, visible diff rather than a silent regression.

class TestUnverifiedLiveModeAssumptions:

    def test_refresh_passes_arbitrary_anchor_port_as_loading_port(self, monkeypatch, wired_engine):
        """UNVERIFIED ASSUMPTION (see file header, item 1): refresh() has no
        caller-supplied loading port, so it resolves whichever port
        get_ports() happens to return first and passes that to
        get_tonnage_list(loading_port=...). This test documents that current
        behavior — it does NOT assert this produces correct/complete
        results from the real Signal Ocean API."""
        anchor_port = SOPort(id=42, name="Arbitrary Anchor Port")
        so_class = SOVesselClass(id=1, name="Aframax")
        vessel = _make_sdk_vessel(imo=9333333)

        api, _ = _wire_tonnage_list_api(
            monkeypatch,
            anchor_ports=(anchor_port, SOPort(id=43, name="Some Other Port")),
            vessels_by_class={"Aframax": (so_class, (vessel,))},
        )
        monkeypatch.setattr(sc, "get_vessel_classes", lambda: ["Aframax"])

        sc.refresh()

        call = api.get_tonnage_list.call_args
        assert call.kwargs["loading_port"] is anchor_port, (
            "current (unverified) behavior: refresh() anchors on "
            "get_ports()[0] regardless of geographic relevance to the "
            "vessel classes being queried"
        )

    def test_get_distances_uses_ballast_loading_condition(self, monkeypatch):
        """UNVERIFIED ASSUMPTION (see file header, item 2): get_distances()
        always passes LoadingCondition.BALLAST, on the reasoning that a
        vessel sailing from its open position to a prospective load port is
        empty. This test documents that current behavior — it does NOT
        assert BALLAST is the maritime-domain-correct choice."""
        api = MagicMock()
        api.get_vessel_classes.return_value = (DistVesselClass(id=1, name="Aframax"),)
        ports = [DistPort(id=1, name="Rotterdam"), DistPort(id=2, name="Singapore")]
        api.get_ports.return_value = tuple(ports)
        api.get_port_to_port_distance.return_value = None
        monkeypatch.setattr(sc, "DistancesAPI", MagicMock(return_value=api))

        sc.get_distances("Rotterdam", "Singapore")

        call = api.get_port_to_port_distance.call_args
        assert call.kwargs["loading_condition_id"] == LoadingCondition.BALLAST, (
            "current (unverified) behavior: get_distances() hardcodes "
            "BALLAST regardless of the actual cargo/laden state"
        )

    def test_get_distances_uses_hardcoded_aframax_vessel_class(self, monkeypatch):
        """UNVERIFIED ASSUMPTION (see file header, item 3): get_distances()
        has no vessel-class parameter in its own signature, so it always
        filters Signal's vessel classes on the literal string "Aframax" as
        a stand-in. This test documents that current behavior — it does NOT
        assert Aframax is a correctness-neutral choice for all callers."""
        api = MagicMock()
        api.get_vessel_classes.return_value = (DistVesselClass(id=1, name="Aframax"),)
        ports = [DistPort(id=1, name="Rotterdam"), DistPort(id=2, name="Singapore")]
        api.get_ports.return_value = tuple(ports)
        api.get_port_to_port_distance.return_value = None
        monkeypatch.setattr(sc, "DistancesAPI", MagicMock(return_value=api))

        sc.get_distances("Rotterdam", "Singapore")

        call = api.get_vessel_classes.call_args
        passed_filter = call.args[0]
        assert passed_filter.name_like == "Aframax", (
            "current (unverified) behavior: get_distances() hardcodes the "
            "'Aframax' vessel class filter regardless of which vessel the "
            "distance is actually being computed for"
        )
