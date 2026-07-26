"""
Real Signal Ocean client. Used when SIGNAL_MODE=live in .env.

Kept separate from mock_vessels.py so switching from mock to the live
Signal Ocean API is a config change only (SIGNAL_MODE, SIGNAL_OCEAN_API_KEY
in .env) — no code in signal_server.py or downstream (matching engine,
database, UI) needs to change.

Wraps the signal-ocean SDK (PRD 6.2). Also owns the background cache
refresh (FR-15): refresh() pulls the tonnage list and upserts it into
cached_vessels; get_* functions below always read the local cache, never
call the live API directly, so matching stays fast (NFR-02, under 100ms).
"""

import os
import sys

# This module lives in middleware/mcp/. When signal_server.py is launched
# directly (`python middleware/mcp/signal_server.py`, cwd = repo root, per
# the claude_desktop_config.json snippet at the bottom of signal_server.py),
# Python puts the *script's own directory* (middleware/mcp/) at sys.path[0],
# not middleware/ — so `config`, `database`, and `core` (all top-level
# packages that live directly under middleware/) fail to import. Mirrors the
# same directory-layout fix scheduler/jobs.py applies in the other direction
# (it adds middleware/mcp/ to sys.path so it can import mock_inbox/imap_client
# as bare modules). Must run before the `config`/`database`/`core` imports
# below.
_MIDDLEWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MIDDLEWARE_DIR not in sys.path:
    sys.path.insert(0, _MIDDLEWARE_DIR)

from datetime import datetime, timezone

import structlog
from signal_ocean import Connection
from signal_ocean.tonnage_list import VesselClassFilter, TonnageListAPI
from signal_ocean.distances import DistancesAPI

from config import settings
from database.models import (  # SQLAlchemy models + engine/session factory, per PRD 8.1
    CachedVessel,
    SignalCacheRefresh,
    get_engine,
    get_session,
)

log = structlog.get_logger()

# ── DB session helper ──────────────────────────────────────────────────────────
# database.session doesn't exist — database/models.py's real pattern is a
# parameterised get_engine(database_url) / get_session(engine) pair (no
# parameterless session factory). This module is a standalone script (run
# by signal_server.py, not imported into the FastAPI app), so it lazily
# builds and caches its own module-level engine the same way
# scheduler/jobs.py does for its own standalone job functions.
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = get_engine(settings.database_url)
    return _engine


def _session():
    return get_session(_get_engine())


def _connection() -> Connection:
    return Connection(api_key=settings.signal_ocean_api_key)


def _row_to_dict(v: CachedVessel) -> dict:
    """
    CachedVessel (SQLAlchemy model) -> plain dict.

    CachedVessel has no as_dict() method (it never existed — grepped the
    whole repo, the two removed call sites below were the only callers
    anywhere). Shape must match mock_vessels.py's MOCK_VESSELS exactly,
    since signal_server.py passes this straight through to MCP tool
    callers with zero transformation.
    """
    return {
        "vessel_id": v.vessel_id,
        "vessel_name": v.vessel_name,
        "vessel_class": v.vessel_class,
        "dwt": v.dwt,
        "open_port": v.open_port,
        "open_port_area": v.open_port_area,
        "open_date": v.open_date.isoformat() if v.open_date else None,
        "commercial_status": v.commercial_status,
        "market_deployment": v.market_deployment,
        "latitude": v.latitude,
        "longitude": v.longitude,
        "cargo_types": v.cargo_types,
    }


def refresh() -> dict:
    """
    Pull the current tonnage list from Signal Ocean and upsert it into
    the local cached_vessels table.

    NOTE: this function is currently unreachable from any live code path —
    the background scheduler (scheduler/jobs.py) refreshes the cache via
    the *other* signal_client module (middleware/signal_client/client.py),
    not this one. Still fixed for correctness/consistency since it's public
    API surface in this file, but lower priority than the read-path
    functions below.
    """
    started = datetime.now(timezone.utc)
    api = TonnageListAPI(_connection())

    # get_tonnage_list(loading_port, vessel_class, ...) requires a real
    # `Port` — the real SDK does `loading_port.id` unconditionally inside
    # the call, so passing loading_port=None (as a first pass at this fix
    # might suggest) would raise AttributeError on every invocation, not
    # silently no-op. This wrapper's own signature (mirrors
    # mock_vessels.get_tonnage_list: vessel_class/area/cargo_type only) has
    # no caller-supplied port to resolve against, and loading_port only
    # affects ETA-relative fields this wrapper doesn't surface anyway — so
    # JUDGMENT CALL: resolve an arbitrary anchor port from Signal's own
    # port list purely to satisfy the required parameter. Unverified
    # against a live API key in this environment; revisit if Signal
    # Ocean's semantics turn out to filter results by loading_port too.
    try:
        anchor_ports = api.get_ports()
    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "mode": "live",
            "last_refreshed": started.isoformat(),
            "vessel_count": 0,
            "success": False,
            "error": f"could not resolve anchor port: {e}",
            "duration_ms": duration_ms,
        }

    if not anchor_ports:
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "mode": "live",
            "last_refreshed": started.isoformat(),
            "vessel_count": 0,
            "success": False,
            "error": "Signal Ocean returned no ports to anchor the tonnage list query against",
            "duration_ms": duration_ms,
        }

    anchor_port = anchor_ports[0]
    all_vessels = []

    for class_name in get_vessel_classes():
        try:
            classes = api.get_vessel_classes(VesselClassFilter(name_like=class_name))
            if not classes:
                continue
            vessel_class = classes[0]

            tonnage_list = api.get_tonnage_list(
                loading_port=anchor_port,
                vessel_class=vessel_class,
                laycan_end_in_days=settings.signal_laycan_window_days,
            )
            all_vessels.extend(tonnage_list.vessels)
        except Exception as e:
            log.warning("signal_client.refresh_class_failed", vessel_class=class_name, error=str(e))
            continue

    with _session() as session:
        session.query(CachedVessel).delete()
        for v in all_vessels:
            # Real SDK Vessel dataclass fields (signal-ocean 2.1.1): imo, name,
            # vessel_class, deadweight, open_port (str), open_date,
            # commercial_status, market_deployment, open_areas (tuple[Area]).
            # There is NO vessel_name/dwt/open_port_area/latitude/longitude/
            # cargo_types on Vessel — see report for the latitude/longitude/
            # cargo_types judgment call (explicit None, not fabricated).
            open_areas = v.open_areas or ()
            open_area_name = open_areas[0].name if open_areas else None

            session.add(CachedVessel(
                vessel_id=str(v.imo),
                vessel_name=v.name,
                vessel_class=v.vessel_class,
                dwt=v.deadweight,
                open_port=v.open_port,
                open_port_area=open_area_name,
                open_date=v.open_date,
                commercial_status=v.commercial_status,
                market_deployment=v.market_deployment,
                latitude=None,   # not available on signal-ocean's Vessel model
                longitude=None,  # not available on signal-ocean's Vessel model
                cargo_types=None,  # not available on signal-ocean's Vessel model
                cached_at=started,
            ))
        session.commit()

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return {
        "mode": "live",
        "last_refreshed": started.isoformat(),
        "vessel_count": len(all_vessels),
        "success": True,
        "duration_ms": duration_ms,
    }


def get_tonnage_list(vessel_class: str = None, area: str = None, cargo_type: str = None) -> list[dict]:
    """Reads from the local cache — never hits the live API directly."""
    with _session() as session:
        query = session.query(CachedVessel)
        if vessel_class:
            query = query.filter(CachedVessel.vessel_class.ilike(vessel_class))
        if area:
            query = query.filter(CachedVessel.open_port_area.ilike(f"%{area}%"))
        if cargo_type:
            query = query.filter(CachedVessel.cargo_types.contains([cargo_type]))
        return [_row_to_dict(v) for v in query.all()]


def get_vessel(vessel_id: str) -> dict:
    with _session() as session:
        v = session.query(CachedVessel).filter(CachedVessel.vessel_id == vessel_id).first()
        return _row_to_dict(v) if v else None


def get_ports(query: str = None) -> list[dict]:
    """Signal Ocean doesn't expose a simple port-lookup endpoint directly —
    this reads from core/ports.py's alias table, the same one the parser
    normalises against, so IMAP-parsed port names and Signal port names
    stay consistent."""
    from core.ports import PORT_TO_AREA

    # core/ports.py exports two flat dicts, not a list-of-dicts:
    #   PORT_ALIASES: alias (lowercase) -> canonical name
    #   PORT_TO_AREA: canonical name -> trading area
    # Build the {"name": ..., "area": ...}-shaped list mock_vessels.py's
    # PORTS returns by walking canonical names in PORT_TO_AREA (already the
    # canonical-name keyspace, so no PORT_ALIASES cross-reference is needed
    # to resolve them further). mock's PORTS entries also carry a "country"
    # key, but core/ports.py has no country data to source that from, so
    # it's omitted here.
    ports = [{"name": name, "area": area} for name, area in PORT_TO_AREA.items()]

    if query:
        q = query.lower()
        return [p for p in ports if q in p["name"].lower() or q in p["area"].lower()]
    return ports


def _resolve_distances_port(all_ports, name: str):
    name_l = name.lower()
    for p in all_ports:
        if p.name.lower() == name_l:
            return p
    for p in all_ports:
        if name_l in p.name.lower():
            return p
    return None


def get_distances(from_port: str, to_port: str) -> dict:
    """
    DistancesAPI.get_distance() was fabricated — it doesn't exist on the
    real SDK. Closest real equivalent is:

        get_port_to_port_distance(vessel_class, loading_condition_id,
                                   port_from, port_to) -> Optional[Decimal]

    which needs two things this wrapper's own signature (mirrors
    mock_vessels.get_distances(from_port, to_port), matching
    signal_server.py's MCP tool signature) has no room for:

    JUDGMENT CALLS (see coder report):
      - vessel_class: no class is passed in by the caller here, so a fixed
        stand-in class ("Aframax") is used. Sea distance between two ports
        is overwhelmingly a function of geography/canal transit, not
        vessel class, so this is a reasonable approximation — but it is a
        guess, not a verified-neutral default. Revisit if get_distances()
        ever needs to be class-aware.
      - loading_condition_id: defaults to the SDK's
        LoadingCondition.BALLAST. This function is used for geography
        scoring between a vessel's *open* position and a prospective load
        port (PRD 9.2) — i.e. the vessel would be sailing there empty —
        so ballast is the more representative condition than laden.

    Port names are resolved against DistancesAPI.get_ports() by exact
    (case-insensitive) match first, falling back to substring match —
    PortFilter/VesselClassFilter (imported but unused previously) turned
    out not to be the cleanest fit here since we need to resolve two
    independent single ports out of one full listing, so plain list
    filtering is used instead of two separate filtered API round-trips.
    """
    from signal_ocean.distances import LoadingCondition
    from signal_ocean.distances import VesselClassFilter as DistVesselClassFilter

    api = DistancesAPI(_connection())

    classes = api.get_vessel_classes(DistVesselClassFilter(name_like="Aframax"))
    if not classes:
        return {
            "from_port": from_port,
            "to_port": to_port,
            "distance_nm": None,
            "note": "Could not resolve a vessel class for the distance lookup",
        }
    vessel_class = classes[0]

    all_ports = api.get_ports()
    port_from = _resolve_distances_port(all_ports, from_port)
    port_to = _resolve_distances_port(all_ports, to_port)

    if port_from is None or port_to is None:
        missing = []
        if port_from is None:
            missing.append(f"from_port '{from_port}'")
        if port_to is None:
            missing.append(f"to_port '{to_port}'")
        return {
            "from_port": from_port,
            "to_port": to_port,
            "distance_nm": None,
            "note": f"Could not resolve against Signal Ocean's port list: {', '.join(missing)}",
        }

    distance = api.get_port_to_port_distance(
        vessel_class=vessel_class,
        loading_condition_id=LoadingCondition.BALLAST,
        port_from=port_from,
        port_to=port_to,
    )

    return {
        "from_port": from_port,
        "to_port": to_port,
        "distance_nm": float(distance) if distance is not None else None,
        "note": None,
    }


def get_vessel_classes() -> list[str]:
    return ["Handymax", "Panamax", "Capesize", "Aframax", "Suezmax", "VLCC"]


def refresh_status() -> dict:
    """Reads the most recent row from signal_cache_refreshes (FR-27)."""
    with _session() as session:
        latest = (
            session.query(SignalCacheRefresh)
            .order_by(SignalCacheRefresh.refreshed_at.desc())
            .first()
        )
        if not latest:
            return {"mode": "live", "last_refreshed": None, "vessel_count": 0, "success": False}
        return {
            "mode": "live",
            "last_refreshed": latest.refreshed_at.isoformat(),
            "vessel_count": latest.vessel_count,
            "success": latest.success,
            "duration_ms": latest.duration_ms,
        }
