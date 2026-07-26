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

from datetime import datetime, timezone

from signal_ocean import Connection
from signal_ocean.tonnage_list import VesselClassFilter, PortFilter, TonnageListAPI
from signal_ocean.distances import DistancesAPI

from config import settings
from database.models import CachedVessel  # SQLAlchemy model, per PRD 8.1
from database.session import get_session  # session factory, per PRD 8.x


def _connection() -> Connection:
    return Connection(settings.SIGNAL_OCEAN_API_KEY)


def refresh() -> dict:
    """
    Pull the current tonnage list from Signal Ocean and upsert it into
    the local cached_vessels table. Called by scheduler/jobs.py every
    SIGNAL_CACHE_REFRESH_MINUTES (default 5), per FR-15.
    """
    started = datetime.now(timezone.utc)
    api = TonnageListAPI(_connection())

    try:
        tonnage_list = api.get_tonnage_list()
    except Exception as e:
        return {
            "mode": "live",
            "last_refreshed": started.isoformat(),
            "vessel_count": 0,
            "success": False,
            "error": str(e),
            "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        }

    with get_session() as session:
        session.query(CachedVessel).delete()
        for v in tonnage_list.vessels:
            session.add(CachedVessel(
                vessel_id=v.imo,
                vessel_name=v.vessel_name,
                vessel_class=v.vessel_class,
                dwt=v.dwt,
                open_port=v.open_port,
                open_port_area=v.open_port_area,
                open_date=v.open_date,
                commercial_status=v.commercial_status,
                market_deployment=v.market_deployment,
                latitude=v.latitude,
                longitude=v.longitude,
                cargo_types=v.cargo_types,
                cached_at=started,
            ))
        session.commit()

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return {
        "mode": "live",
        "last_refreshed": started.isoformat(),
        "vessel_count": len(tonnage_list.vessels),
        "success": True,
        "duration_ms": duration_ms,
    }


def get_tonnage_list(vessel_class: str = None, area: str = None, cargo_type: str = None) -> list[dict]:
    """Reads from the local cache — never hits the live API directly."""
    with get_session() as session:
        query = session.query(CachedVessel)
        if vessel_class:
            query = query.filter(CachedVessel.vessel_class.ilike(vessel_class))
        if area:
            query = query.filter(CachedVessel.open_port_area.ilike(f"%{area}%"))
        if cargo_type:
            query = query.filter(CachedVessel.cargo_types.contains([cargo_type]))
        return [v.as_dict() for v in query.all()]


def get_vessel(vessel_id: str) -> dict:
    with get_session() as session:
        v = session.query(CachedVessel).filter(CachedVessel.vessel_id == vessel_id).first()
        return v.as_dict() if v else None


def get_ports(query: str = None) -> list[dict]:
    """Signal Ocean doesn't expose a simple port-lookup endpoint directly —
    this reads from core/ports.py's alias table, the same one the parser
    normalises against, so IMAP-parsed port names and Signal port names
    stay consistent."""
    from core.ports import PORT_ALIAS_TABLE
    if query:
        q = query.lower()
        return [p for p in PORT_ALIAS_TABLE if q in p["name"].lower() or q in p["area"].lower()]
    return PORT_ALIAS_TABLE


def get_distances(from_port: str, to_port: str) -> dict:
    api = DistancesAPI(_connection())
    result = api.get_distance(from_port, to_port)
    return {
        "from_port": from_port,
        "to_port": to_port,
        "distance_nm": result.distance,
        "note": None,
    }


def get_vessel_classes() -> list[str]:
    return ["Handymax", "Panamax", "Capesize", "Aframax", "Suezmax", "VLCC"]


def refresh_status() -> dict:
    """Reads the most recent row from signal_cache_refreshes (FR-27)."""
    from database.models import SignalCacheRefresh
    with get_session() as session:
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
