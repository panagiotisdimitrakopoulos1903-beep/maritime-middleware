"""
tests/test_core.py — unit tests for parser, matching, and port normalisation.

Run with: pytest tests/ -v
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from core.ports import normalise_port, geography_score, get_area
from matching.engine import (
    MatchingEngine, vessel_size_score,
    date_overlap_score, cargo_type_score
)
from parser.llm_parser import ParsedOrder, FieldWithConfidence


# ── Port normalisation tests ───────────────────────────────────────────────────

class TestPortNormalisation:

    def test_common_abbreviations(self):
        assert normalise_port("ANT") == "Antwerp"
        assert normalise_port("RTM") == "Rotterdam"
        assert normalise_port("AMS") == "Amsterdam"
        assert normalise_port("SPORE") == "Singapore"
        assert normalise_port("NOVO") == "Novorossiysk"
        assert normalise_port("NVS") == "Novorossiysk"

    def test_case_insensitive(self):
        assert normalise_port("ant") == "Antwerp"
        assert normalise_port("Antwerp") == "Antwerp"
        assert normalise_port("ANTWERP") == "Antwerp"

    def test_full_names(self):
        assert normalise_port("Rotterdam") == "Rotterdam"
        assert normalise_port("Singapore") == "Singapore"
        assert normalise_port("Novorossiysk") == "Novorossiysk"

    def test_unknown_port_returns_none(self):
        assert normalise_port("XYZUNKNOWN") is None

    def test_empty_returns_none(self):
        assert normalise_port("") is None
        assert normalise_port(None) is None

    def test_geography_same_area(self):
        score = geography_score("Antwerp", "ARA")
        assert score == 1.0

    def test_geography_adjacent_area(self):
        score = geography_score("Antwerp", "Continent")
        assert score == 0.9

    def test_geography_distant_area(self):
        score = geography_score("Antwerp", "Far East")
        assert score == 0.2

    def test_geography_unknown_returns_neutral(self):
        score = geography_score("UnknownPort", "ARA")
        assert score == 0.5


# ── Vessel size scoring ────────────────────────────────────────────────────────

class TestVesselSizeScore:

    def test_perfect_fit(self):
        # 55000 MT cargo, 65000 DWT vessel = ratio 1.18 → perfect
        score = vessel_size_score(55000, 65000)
        assert score == 1.0

    def test_slightly_tight(self):
        # ratio just under 1.05
        score = vessel_size_score(60000, 62000)
        assert score == 0.85

    def test_oversized_vessel(self):
        # Very large vessel for small cargo
        score = vessel_size_score(20000, 80000)
        assert score < 0.6

    def test_undersized_vessel(self):
        # Vessel too small
        score = vessel_size_score(80000, 50000)
        assert score < 0.4

    def test_none_quantity_returns_neutral(self):
        assert vessel_size_score(None, 65000) == 0.5

    def test_none_dwt_returns_neutral(self):
        assert vessel_size_score(55000, None) == 0.5


# ── Date overlap scoring ───────────────────────────────────────────────────────

class TestDateOverlapScore:

    def test_vessel_opens_3_days_before_laycan(self):
        laycan_start = "2024-08-10"
        laycan_end = "2024-08-20"
        open_date = datetime(2024, 8, 7, tzinfo=timezone.utc)
        score = date_overlap_score(laycan_start, laycan_end, open_date)
        assert score == 1.0

    def test_vessel_opens_day_of_laycan(self):
        score = date_overlap_score(
            "2024-08-10", "2024-08-20",
            datetime(2024, 8, 10, tzinfo=timezone.utc)
        )
        assert 0.7 < score <= 0.85

    def test_vessel_opens_after_laycan_ends(self):
        score = date_overlap_score(
            "2024-08-10", "2024-08-20",
            datetime(2024, 8, 25, tzinfo=timezone.utc)
        )
        assert score < 0.2

    def test_vessel_opens_too_early(self):
        score = date_overlap_score(
            "2024-08-10", "2024-08-20",
            datetime(2024, 7, 1, tzinfo=timezone.utc)
        )
        assert score < 0.5

    def test_missing_dates_returns_neutral(self):
        assert date_overlap_score(None, None, None) == 0.5


# ── Cargo type scoring ─────────────────────────────────────────────────────────

class TestCargoTypeScore:

    def test_grain_panamax_perfect(self):
        assert cargo_type_score("grain", "Panamax") == 1.0

    def test_grain_capesize_perfect(self):
        assert cargo_type_score("grain", "Capesize") == 1.0

    def test_crude_vlcc_perfect(self):
        assert cargo_type_score("crude oil", "VLCC") == 1.0

    def test_grain_vlcc_incompatible(self):
        assert cargo_type_score("grain", "VLCC") == 0.0

    def test_crude_panamax_incompatible(self):
        assert cargo_type_score("crude oil", "Panamax") == 0.0

    def test_unknown_cargo_neutral(self):
        assert cargo_type_score("mystery cargo", "Panamax") == 0.5

    def test_none_cargo_neutral(self):
        assert cargo_type_score(None, "Panamax") == 0.5


# ── Matching engine integration ────────────────────────────────────────────────

class TestMatchingEngine:

    def _make_order(
        self,
        cargo="grain",
        qty=55000,
        load="Antwerp",
        disch="Japan",
        laycan_start="2024-08-10",
        laycan_end="2024-08-20",
    ) -> ParsedOrder:
        order = ParsedOrder()
        order.cargo_type = FieldWithConfidence(value=cargo, confidence=0.98)
        order.quantity_mt = FieldWithConfidence(value=qty, confidence=0.95)
        order.load_port = FieldWithConfidence(value=load, confidence=0.97)
        order.load_port_canonical = normalise_port(load) or load
        order.discharge_port = FieldWithConfidence(value=disch, confidence=0.95)
        order.laycan_start = FieldWithConfidence(value=laycan_start, confidence=0.95)
        order.laycan_end = FieldWithConfidence(value=laycan_end, confidence=0.95)
        return order

    def _make_vessel(self, **kwargs):
        from signal_client.client import VesselSnapshot
        defaults = {
            "vessel_id": "9999999",
            "vessel_name": "MV Test",
            "vessel_class": "Panamax",
            "dwt": 65000.0,
            "open_port": "Rotterdam",
            "open_port_area": "ARA",
            "open_date": datetime(2024, 8, 7, tzinfo=timezone.utc),
            "commercial_status": "available",
            "cargo_types": ["grain"],
            "built_year": 2015,
            "latitude": 51.9,
            "longitude": 4.5,
            "last_ais_update": datetime(2024, 8, 1, tzinfo=timezone.utc),
        }
        defaults.update(kwargs)
        return VesselSnapshot(**defaults)

    def test_perfect_match_scores_high(self):
        engine = MatchingEngine()
        order = self._make_order()
        vessel = self._make_vessel()
        total, *_ = engine.score_vessel(vessel, order)
        assert total >= 0.85

    def test_ranking_returns_correct_order(self):
        engine = MatchingEngine()
        order = self._make_order()
        good = self._make_vessel(vessel_id="111", vessel_name="Good Vessel", dwt=65000)
        poor = self._make_vessel(
            vessel_id="222", vessel_name="Poor Vessel",
            vessel_class="VLCC", dwt=300000,
            open_port_area="Far East",
            open_date=datetime(2024, 9, 1, tzinfo=timezone.utc)
        )
        results = engine.rank([poor, good], order)
        assert results[0].vessel_name == "Good Vessel"
        assert results[0].rank == 1
        assert results[1].rank == 2

    def test_ranking_respects_top_n(self):
        engine = MatchingEngine()
        order = self._make_order()
        vessels = [self._make_vessel(vessel_id=str(i)) for i in range(10)]
        results = engine.rank(vessels, order, top_n=3)
        assert len(results) == 3

    def test_empty_vessel_list(self):
        engine = MatchingEngine()
        order = self._make_order()
        results = engine.rank([], order)
        assert results == []

    def test_scores_sum_to_correct_total(self):
        engine = MatchingEngine()
        order = self._make_order()
        vessel = self._make_vessel()
        total, s_size, s_geo, s_date, s_cargo = engine.score_vessel(vessel, order)
        expected = (
            0.35 * s_size +
            0.30 * s_geo +
            0.20 * s_date +
            0.15 * s_cargo
        )
        assert abs(total - expected) < 0.001
