"""
matching/engine.py — scores and ranks vessels against a parsed order.

Scoring is a weighted sum across four dimensions:
  - vessel_size   (0.35)
  - geography     (0.30)
  - date_overlap  (0.20)
  - cargo_type    (0.15)
"""
import structlog
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel

from config import settings
from core.ports import geography_score
from parser.llm_parser import ParsedOrder
from signal_client.client import VesselSnapshot

log = structlog.get_logger()


# ── Cargo type compatibility ──────────────────────────────────────────────────

CARGO_VESSEL_COMPATIBILITY: dict[str, list[str]] = {
    # Tanker cargoes
    "crude oil": ["VLCC", "Suezmax", "Aframax"],
    "crude": ["VLCC", "Suezmax", "Aframax"],
    "fuel oil": ["Aframax", "Suezmax", "VLCC"],
    "fuel": ["Aframax", "Suezmax", "VLCC"],
    "gasoline": ["Aframax", "MR Tanker", "LR1"],
    "naphtha": ["Aframax", "MR Tanker", "LR1"],
    "jet fuel": ["Aframax", "MR Tanker"],
    "diesel": ["Aframax", "MR Tanker"],
    "lpg": ["VLGC", "LGC"],
    "lng": ["LNG Carrier"],
    # Dry bulk cargoes
    "grain": ["Capesize", "Panamax", "Handymax", "Handysize"],
    "wheat": ["Panamax", "Handymax", "Handysize"],
    "corn": ["Panamax", "Handymax", "Handysize"],
    "soybeans": ["Panamax", "Handymax", "Handysize"],
    "coal": ["Capesize", "Panamax", "Handymax"],
    "iron ore": ["Capesize", "Panamax"],
    "bauxite": ["Capesize", "Panamax"],
    "fertilizer": ["Handymax", "Handysize", "Panamax"],
    "fertiliser": ["Handymax", "Handysize", "Panamax"],
    "sugar": ["Handymax", "Handysize"],
    "salt": ["Handysize", "Handymax"],
    "cement": ["Handysize", "Handymax"],
    "steel": ["Handysize", "Handymax", "Panamax"],
}


def cargo_type_score(cargo_type: Optional[str], vessel_class: Optional[str]) -> float:
    """
    Score compatibility between cargo type and vessel class.
    1.0 = perfect match, 0.5 = plausible, 0.0 = incompatible.
    """
    if not cargo_type or not vessel_class:
        return 0.5  # unknown — neutral

    cargo_lower = cargo_type.lower().strip()
    vessel_upper = vessel_class.strip()

    compatible = CARGO_VESSEL_COMPATIBILITY.get(cargo_lower, [])
    if not compatible:
        return 0.5  # unknown cargo type — neutral

    if vessel_upper in compatible:
        return 1.0
    # Adjacent class — partial credit
    if any(c.lower() in vessel_upper.lower() for c in compatible):
        return 0.6
    return 0.0


# ── DWT scoring ───────────────────────────────────────────────────────────────

def vessel_size_score(required_mt: Optional[float], vessel_dwt: Optional[float]) -> float:
    """
    Score how well vessel DWT fits the required cargo quantity.
    A vessel is ideal when its DWT is ~110–130% of the cargo quantity
    (to account for ballast, stores, and fuel).
    """
    if not required_mt or not vessel_dwt or vessel_dwt <= 0:
        return 0.5

    ratio = vessel_dwt / required_mt

    if 1.05 <= ratio <= 1.35:
        return 1.0           # ideal fit
    elif 0.95 <= ratio < 1.05:
        return 0.85          # slightly tight but workable
    elif 1.35 < ratio <= 1.60:
        return 0.75          # vessel is larger than needed
    elif 0.80 <= ratio < 0.95:
        return 0.60          # vessel is slightly too small
    elif 1.60 < ratio <= 2.00:
        return 0.50          # vessel is significantly oversized
    else:
        return 0.20          # poor fit


# ── Date overlap scoring ──────────────────────────────────────────────────────

def date_overlap_score(
    laycan_start: Optional[str],
    laycan_end: Optional[str],
    vessel_open_date: Optional[datetime],
) -> float:
    """
    Score how well the vessel's open date fits within the laycan window.
    Best case: vessel opens 2-5 days before laycan start (positioning time).
    """
    if not laycan_start or not laycan_end or not vessel_open_date:
        return 0.5

    try:
        ls = datetime.fromisoformat(str(laycan_start)).replace(tzinfo=timezone.utc)
        le = datetime.fromisoformat(str(laycan_end)).replace(tzinfo=timezone.utc)

        if vessel_open_date.tzinfo is None:
            vessel_open_date = vessel_open_date.replace(tzinfo=timezone.utc)

        # Ideal: vessel opens 0–7 days before laycan start
        pre_window_start = ls - timedelta(days=7)
        pre_window_end = ls

        if pre_window_start <= vessel_open_date <= pre_window_end:
            return 1.0  # perfect positioning

        if ls <= vessel_open_date <= le:
            # Opens inside laycan — still workable
            days_in = (vessel_open_date - ls).days
            window_days = max((le - ls).days, 1)
            return 0.85 * (1 - days_in / window_days)

        if vessel_open_date < pre_window_start:
            # Opens too early — vessel will be waiting
            days_early = (pre_window_start - vessel_open_date).days
            return max(0.3, 0.8 - days_early * 0.05)

        if vessel_open_date > le:
            # Opens after laycan ends — misses the window
            days_late = (vessel_open_date - le).days
            return max(0.0, 0.4 - days_late * 0.08)

    except (ValueError, TypeError):
        return 0.5

    return 0.0


# ── Scored vessel ─────────────────────────────────────────────────────────────

class ScoredVessel(BaseModel):
    # Vessel identity
    vessel_id: str
    vessel_name: Optional[str]
    vessel_class: Optional[str]
    dwt: Optional[float]
    open_port: Optional[str]
    open_port_area: Optional[str]
    open_date: Optional[datetime]
    commercial_status: Optional[str]
    built_year: Optional[int]

    # Scoring
    rank: int
    total_score: float
    score_vessel_size: float
    score_geography: float
    score_date_overlap: float
    score_cargo_type: float

    class Config:
        arbitrary_types_allowed = True


# ── Main matching engine ──────────────────────────────────────────────────────

class MatchingEngine:

    def __init__(self):
        self.w_size = settings.weight_vessel_size
        self.w_geo = settings.weight_geography
        self.w_date = settings.weight_date_overlap
        self.w_cargo = settings.weight_cargo_type

        # Validate weights sum to 1.0
        total = self.w_size + self.w_geo + self.w_date + self.w_cargo
        assert abs(total - 1.0) < 0.001, f"Scoring weights must sum to 1.0, got {total}"

    def score_vessel(
        self,
        vessel: VesselSnapshot,
        order: ParsedOrder,
    ) -> tuple[float, float, float, float, float]:
        """
        Score a single vessel against an order.
        Returns (total, size, geo, date, cargo) scores.
        """
        # Determine quantity for DWT scoring (use midpoint if range given)
        qty = None
        if order.quantity_mt.value:
            qty = float(order.quantity_mt.value)
        elif order.quantity_min_mt.value and order.quantity_max_mt.value:
            qty = (float(order.quantity_min_mt.value) + float(order.quantity_max_mt.value)) / 2

        s_size = vessel_size_score(qty, vessel.dwt)

        s_geo = geography_score(
            order.load_port_canonical or str(order.load_port.value or ""),
            vessel.open_port_area or "",
        )

        s_date = date_overlap_score(
            order.laycan_start.value,
            order.laycan_end.value,
            vessel.open_date,
        )

        s_cargo = cargo_type_score(
            str(order.cargo_type.value) if order.cargo_type.value else None,
            vessel.vessel_class,
        )

        total = (
            self.w_size * s_size +
            self.w_geo * s_geo +
            self.w_date * s_date +
            self.w_cargo * s_cargo
        )

        return (
            round(total, 4),
            round(s_size, 4),
            round(s_geo, 4),
            round(s_date, 4),
            round(s_cargo, 4),
        )

    def rank(
        self,
        vessels: list[VesselSnapshot],
        order: ParsedOrder,
        top_n: int = None,
    ) -> list[ScoredVessel]:
        """
        Score all vessels and return the top N ranked results.
        """
        if top_n is None:
            top_n = settings.match_top_n

        if not vessels:
            log.warning("matching_engine.no_vessels")
            return []

        scored = []
        for vessel in vessels:
            total, s_size, s_geo, s_date, s_cargo = self.score_vessel(vessel, order)
            scored.append((total, s_size, s_geo, s_date, s_cargo, vessel))

        # Sort descending by total score
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for rank_pos, (total, s_size, s_geo, s_date, s_cargo, vessel) in enumerate(
            scored[:top_n], start=1
        ):
            results.append(ScoredVessel(
                vessel_id=vessel.vessel_id,
                vessel_name=vessel.vessel_name,
                vessel_class=vessel.vessel_class,
                dwt=vessel.dwt,
                open_port=vessel.open_port,
                open_port_area=vessel.open_port_area,
                open_date=vessel.open_date,
                commercial_status=vessel.commercial_status,
                built_year=vessel.built_year,
                rank=rank_pos,
                total_score=total,
                score_vessel_size=s_size,
                score_geography=s_geo,
                score_date_overlap=s_date,
                score_cargo_type=s_cargo,
            ))

        log.info(
            "matching_engine.ranked",
            order_cargo=order.cargo_type.value,
            vessels_evaluated=len(vessels),
            top_result_score=results[0].total_score if results else None,
        )

        return results
