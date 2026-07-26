"""
signal_client/client.py — Signal Ocean API wrapper with local cache.

Maintains a continuously refreshed local cache of available vessels.
All matching queries run against the cache, not the live API.
"""
import structlog
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session

from config import settings
from database.models import CachedVessel, SignalCacheRefresh

log = structlog.get_logger()


# ── Data transfer objects ─────────────────────────────────────────────────────

class VesselSnapshot:
    """Lightweight vessel data used by the matching engine."""
    __slots__ = [
        "vessel_id", "vessel_name", "vessel_class", "dwt",
        "open_port", "open_port_area", "open_date",
        "commercial_status", "cargo_types", "built_year",
        "latitude", "longitude", "last_ais_update",
    ]

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        return (
            f"<VesselSnapshot {self.vessel_name} "
            f"({self.vessel_class}, {self.dwt:.0f} DWT) "
            f"open {self.open_port} {self.open_date}>"
        )


# ── Signal Ocean cache client ─────────────────────────────────────────────────

class SignalCacheClient:
    """
    Wraps the Signal Ocean SDK and manages the local PostgreSQL cache.

    The refresh() method is called by APScheduler every N minutes.
    The get_available_vessels() method is called by the matching engine
    and reads only from the local cache — never the live API.
    """

    def __init__(self, session: Session):
        self.session = session
        self._api = None

    def _get_api(self):
        """Lazy initialise Signal Ocean SDK connection."""
        if self._api is None:
            try:
                from signal_ocean import Connection
                from signal_ocean.tonnage_list import TonnageListAPI
                conn = Connection(api_key=settings.signal_ocean_api_key)
                self._api = TonnageListAPI(conn)
                log.info("signal_client.connected")
            except Exception as e:
                log.error("signal_client.connection_failed", error=str(e))
                raise
        return self._api

    def refresh(self) -> int:
        """
        Pull fresh tonnage data from Signal Ocean and update local cache.
        Returns the number of vessels cached.
        Called by the background scheduler — never in the request path.
        """
        started = datetime.now(timezone.utc)
        vessel_count = 0
        error_msg = None

        try:
            from signal_ocean.tonnage_list import (
                VesselFilter, MarketDeployment,
                CommercialStatus, VesselClassFilter
            )

            api = self._get_api()
            today = datetime.now(timezone.utc)

            # Vessel classes to cache — extend for your broker's segments
            target_classes = [
                "Aframax", "Suezmax", "VLCC",
                "Panamax", "Capesize", "Handymax", "Handysize"
            ]

            vessel_filter = VesselFilter(
                market_deployments=[
                    MarketDeployment.SPOT,
                    MarketDeployment.RELET,
                ],
                commercial_statuses=[
                    CommercialStatus.AVAILABLE,
                    CommercialStatus.CANCELLED,
                    CommercialStatus.FAILED,
                ],
                latest_ais_since=7,  # only vessels with AIS in last 7 days
            )

            fetched_ids = set()

            for class_name in target_classes:
                try:
                    class_filter = VesselClassFilter(name_like=class_name)
                    classes = api.get_vessel_classes(class_filter)
                    if not classes:
                        continue

                    vessel_class = classes[0]

                    # Get current tonnage list (no historical range = current only)
                    tonnage_list = api.get_tonnage_list(
                        loading_port=None,
                        vessel_class=vessel_class,
                        laycan_end_in_days=settings.signal_laycan_window_days,
                        vessel_filter=vessel_filter,
                    )

                    for vessel in tonnage_list.vessels:
                        self._upsert_vessel(vessel, class_name)
                        fetched_ids.add(str(vessel.imo))
                        vessel_count += 1

                except Exception as e:
                    log.warning(
                        "signal_client.class_fetch_failed",
                        vessel_class=class_name,
                        error=str(e)
                    )
                    continue

            # Remove vessels no longer appearing in Signal (likely fixed)
            self._prune_stale(fetched_ids)

            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._log_refresh(vessel_count, True, None, duration_ms)

            log.info(
                "signal_client.refresh_complete",
                vessel_count=vessel_count,
                duration_ms=duration_ms
            )

        except Exception as e:
            error_msg = str(e)
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._log_refresh(0, False, error_msg, duration_ms)
            log.error("signal_client.refresh_failed", error=error_msg)

        return vessel_count

    def _upsert_vessel(self, vessel, vessel_class_name: str):
        """Insert or update a vessel in the local cache."""
        try:
            vessel_id = str(vessel.imo)
            existing = self.session.query(CachedVessel).filter_by(
                vessel_id=vessel_id
            ).first()

            data = {
                "vessel_id": vessel_id,
                "vessel_name": getattr(vessel, "name", None),
                "vessel_class": vessel_class_name,
                "dwt": getattr(vessel, "deadweight", None),
                "built_year": getattr(vessel, "year_built", None),
                "open_port": getattr(vessel, "open_port_name", None),
                "open_port_area": getattr(vessel, "open_narrow_area", None),
                "open_date": getattr(vessel, "open_date", None),
                "commercial_status": str(getattr(vessel, "commercial_status", "")),
                "market_deployment": str(getattr(vessel, "market_deployment", "")),
                "latitude": getattr(vessel, "latitude", None),
                "longitude": getattr(vessel, "longitude", None),
                "last_ais_update": getattr(vessel, "latest_ais", None),
                "cached_at": datetime.now(timezone.utc),
                "raw_signal_data": {},
            }

            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
            else:
                self.session.add(CachedVessel(**data))

            self.session.commit()

        except Exception as e:
            self.session.rollback()
            log.warning("signal_client.upsert_failed", error=str(e))

    def _prune_stale(self, current_ids: set):
        """Remove vessels from cache that no longer appear in Signal."""
        try:
            all_cached = self.session.query(CachedVessel).all()
            for v in all_cached:
                if v.vessel_id not in current_ids:
                    self.session.delete(v)
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            log.warning("signal_client.prune_failed", error=str(e))

    def _log_refresh(
        self,
        count: int,
        success: bool,
        error: Optional[str],
        duration_ms: int
    ):
        try:
            self.session.add(SignalCacheRefresh(
                vessel_count=count,
                success=success,
                error_message=error,
                duration_ms=duration_ms,
            ))
            self.session.commit()
        except Exception:
            self.session.rollback()

    def get_available_vessels(
        self,
        vessel_class: Optional[str] = None,
        area: Optional[str] = None,
        open_before: Optional[datetime] = None,
    ) -> list[VesselSnapshot]:
        """
        Query the local cache. Never calls Signal API.
        Optionally filter by vessel class, area, or open date window.
        """
        try:
            query = self.session.query(CachedVessel).filter(
                CachedVessel.commercial_status.in_([
                    "CommercialStatus.AVAILABLE",
                    "available",
                    "CommercialStatus.CANCELLED",
                    "CommercialStatus.FAILED",
                ])
            )

            if vessel_class:
                query = query.filter(
                    CachedVessel.vessel_class.ilike(f"%{vessel_class}%")
                )

            if area:
                query = query.filter(
                    CachedVessel.open_port_area.ilike(f"%{area}%")
                )

            if open_before:
                query = query.filter(
                    CachedVessel.open_date <= open_before
                )

            rows = query.all()

            snapshots = []
            for row in rows:
                snapshots.append(VesselSnapshot(
                    vessel_id=row.vessel_id,
                    vessel_name=row.vessel_name,
                    vessel_class=row.vessel_class,
                    dwt=row.dwt or 0.0,
                    open_port=row.open_port,
                    open_port_area=row.open_port_area,
                    open_date=row.open_date,
                    commercial_status=row.commercial_status,
                    cargo_types=row.cargo_types or [],
                    built_year=row.built_year,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    last_ais_update=row.last_ais_update,
                ))

            return snapshots

        except Exception as e:
            log.error("signal_client.get_vessels_failed", error=str(e))
            return []

    def get_last_refresh(self) -> Optional[datetime]:
        """Return timestamp of the most recent successful cache refresh."""
        try:
            result = (
                self.session.query(SignalCacheRefresh)
                .filter_by(success=True)
                .order_by(SignalCacheRefresh.refreshed_at.desc())
                .first()
            )
            return result.refreshed_at if result else None
        except Exception:
            return None
