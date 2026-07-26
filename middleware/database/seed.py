"""
database/seed.py — populate the database with realistic test data.

Run with:
    python -m database.seed

Purpose: lets the frontend developer build and test the Electron panel
without needing live API keys or a running mail server.
Produces data that mirrors exactly what the live system would generate.
"""
import uuid
import random
from datetime import datetime, timedelta, timezone

# ── Minimal standalone mode ───────────────────────────────────────────────────
# The seed script uses SQLAlchemy directly so it can run without
# the full app stack (no FastAPI, no scheduler, no Milter).

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from database.models import (
    Base, InboundOrder, CachedVessel,
    SignalCacheRefresh, MatchResult, create_tables
)

# ── Config ────────────────────────────────────────────────────────────────────

try:
    from config import settings
    DB_URL = settings.database_url
except Exception:
    DB_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql://middleware:middleware@localhost:5432/maritime_middleware"
    )

NOW = datetime.now(timezone.utc)


# ── Vessel fleet ──────────────────────────────────────────────────────────────

VESSELS = [
    # Aframax tankers
    {
        "vessel_id": "9100001", "vessel_name": "MV Artemis Star",
        "vessel_class": "Aframax", "dwt": 113000, "built_year": 2016,
        "open_port": "Rotterdam", "open_port_area": "ARA",
        "open_date": NOW + timedelta(days=4),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 51.92, "longitude": 4.48,
        "cargo_types": ["crude oil", "fuel oil"],
    },
    {
        "vessel_id": "9100002", "vessel_name": "MV Poseidon Spirit",
        "vessel_class": "Aframax", "dwt": 107000, "built_year": 2014,
        "open_port": "Novorossiysk", "open_port_area": "Black Sea",
        "open_date": NOW + timedelta(days=7),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 44.72, "longitude": 37.77,
        "cargo_types": ["crude oil", "fuel oil"],
    },
    {
        "vessel_id": "9100003", "vessel_name": "MV Kronos Bay",
        "vessel_class": "Aframax", "dwt": 115000, "built_year": 2018,
        "open_port": "Augusta", "open_port_area": "Mediterranean",
        "open_date": NOW + timedelta(days=2),
        "commercial_status": "available", "market_deployment": "relet",
        "latitude": 37.22, "longitude": 15.22,
        "cargo_types": ["crude oil", "naphtha"],
    },
    # Suezmax tankers
    {
        "vessel_id": "9200001", "vessel_name": "MV Helios Voyager",
        "vessel_class": "Suezmax", "dwt": 158000, "built_year": 2017,
        "open_port": "Ras Tanura", "open_port_area": "Middle East Gulf",
        "open_date": NOW + timedelta(days=9),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 26.64, "longitude": 50.16,
        "cargo_types": ["crude oil"],
    },
    {
        "vessel_id": "9200002", "vessel_name": "MV Triton Pioneer",
        "vessel_class": "Suezmax", "dwt": 162000, "built_year": 2015,
        "open_port": "Rotterdam", "open_port_area": "ARA",
        "open_date": NOW + timedelta(days=6),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 51.94, "longitude": 4.12,
        "cargo_types": ["crude oil", "fuel oil"],
    },
    # VLCC tankers
    {
        "vessel_id": "9300001", "vessel_name": "MV Olympian Glory",
        "vessel_class": "VLCC", "dwt": 298000, "built_year": 2019,
        "open_port": "Fujairah", "open_port_area": "Middle East Gulf",
        "open_date": NOW + timedelta(days=12),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 25.12, "longitude": 56.34,
        "cargo_types": ["crude oil"],
    },
    # Panamax bulk carriers
    {
        "vessel_id": "9400001", "vessel_name": "MV Aegean Harvest",
        "vessel_class": "Panamax", "dwt": 76000, "built_year": 2013,
        "open_port": "Amsterdam", "open_port_area": "ARA",
        "open_date": NOW + timedelta(days=5),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 52.38, "longitude": 4.91,
        "cargo_types": ["grain", "coal", "fertilizer"],
    },
    {
        "vessel_id": "9400002", "vessel_name": "MV Baltic Trader",
        "vessel_class": "Panamax", "dwt": 78500, "built_year": 2016,
        "open_port": "Hamburg", "open_port_area": "Continent",
        "open_date": NOW + timedelta(days=3),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 53.54, "longitude": 9.99,
        "cargo_types": ["grain", "coal", "steel"],
    },
    {
        "vessel_id": "9400003", "vessel_name": "MV Doric Fortune",
        "vessel_class": "Panamax", "dwt": 74000, "built_year": 2012,
        "open_port": "Gdansk", "open_port_area": "Baltic",
        "open_date": NOW + timedelta(days=8),
        "commercial_status": "available", "market_deployment": "relet",
        "latitude": 54.40, "longitude": 18.67,
        "cargo_types": ["grain", "fertilizer"],
    },
    # Capesize bulk carriers
    {
        "vessel_id": "9500001", "vessel_name": "MV Titan Bulk",
        "vessel_class": "Capesize", "dwt": 178000, "built_year": 2018,
        "open_port": "Rotterdam", "open_port_area": "ARA",
        "open_date": NOW + timedelta(days=11),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 51.90, "longitude": 4.50,
        "cargo_types": ["coal", "iron ore", "grain"],
    },
    {
        "vessel_id": "9500002", "vessel_name": "MV Colossus Star",
        "vessel_class": "Capesize", "dwt": 182000, "built_year": 2020,
        "open_port": "Singapore", "open_port_area": "Far East",
        "open_date": NOW + timedelta(days=6),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 1.29, "longitude": 103.85,
        "cargo_types": ["coal", "iron ore"],
    },
    # Handymax bulk carriers
    {
        "vessel_id": "9600001", "vessel_name": "MV Cyclades Wind",
        "vessel_class": "Handymax", "dwt": 56000, "built_year": 2014,
        "open_port": "Antwerp", "open_port_area": "ARA",
        "open_date": NOW + timedelta(days=2),
        "commercial_status": "available", "market_deployment": "spot",
        "latitude": 51.22, "longitude": 4.40,
        "cargo_types": ["grain", "fertilizer", "sugar"],
    },
    {
        "vessel_id": "9600002", "vessel_name": "MV Ionian Star",
        "vessel_class": "Handymax", "dwt": 58000, "built_year": 2017,
        "open_port": "Piraeus", "open_port_area": "Mediterranean",
        "open_date": NOW + timedelta(days=5),
        "commercial_status": "available", "market_deployment": "relet",
        "latitude": 37.94, "longitude": 23.64,
        "cargo_types": ["grain", "fertilizer", "cement"],
    },
    # A vessel on subs (should score lower / be excluded depending on filter)
    {
        "vessel_id": "9700001", "vessel_name": "MV Hermes Carrier",
        "vessel_class": "Panamax", "dwt": 77000, "built_year": 2015,
        "open_port": "Rotterdam", "open_port_area": "ARA",
        "open_date": NOW + timedelta(days=4),
        "commercial_status": "on_subs", "market_deployment": "spot",
        "latitude": 51.92, "longitude": 4.47,
        "cargo_types": ["grain", "coal"],
    },
]


# ── Raw inbound messages ──────────────────────────────────────────────────────

INBOUND_MESSAGES = [
    {
        "sender": "chartering@olympus-trading.com",
        "subject": "GRAIN CARGO ENQUIRY - ARA/JAPAN",
        "raw_body": (
            "Gents,\n\n"
            "We have the following order for your consideration:\n\n"
            "55/60,000 MT GRAIN\n"
            "LOAD: ANTWERP OR AMSTERDAM\n"
            "DISCH: JAPAN (ANY SAFE PORT)\n"
            "LAYCAN: AUG 10-20 2024\n"
            "WANTS: BEST MARKET\n\n"
            "Please offer soonest.\n\n"
            "Best regards,\nOlympus Trading"
        ),
        "cargo_type": "grain", "quantity_mt": 57500,
        "quantity_min_mt": 55000, "quantity_max_mt": 60000,
        "load_port": "Antwerp", "load_port_canonical": "Antwerp",
        "discharge_port": "Japan", "discharge_port_canonical": "Japan (any)",
        "laycan_start": NOW + timedelta(days=10),
        "laycan_end": NOW + timedelta(days=20),
        "vessel_type": "Panamax",
        "parse_confidence": 0.96,
        "has_low_confidence": False,
        "confidence_scores": {
            "cargo_type": 0.99, "quantity_mt": 0.95, "load_port": 0.97,
            "discharge_port": 0.93, "laycan_start": 0.96, "laycan_end": 0.96,
        },
        "best_match_class": "Panamax",
    },
    {
        "sender": "ops@nordic-bulk.no",
        "subject": "Coal cargo ARA/Korea",
        "raw_body": (
            "Hi,\n\n"
            "pls offer 75k coal rtm/korea lc aug 15-25\n"
            "wants wsg\n\n"
            "rgds"
        ),
        "cargo_type": "coal", "quantity_mt": 75000,
        "quantity_min_mt": None, "quantity_max_mt": None,
        "load_port": "Rotterdam", "load_port_canonical": "Rotterdam",
        "discharge_port": "Korea", "discharge_port_canonical": "South Korea (any)",
        "laycan_start": NOW + timedelta(days=15),
        "laycan_end": NOW + timedelta(days=25),
        "vessel_type": "Panamax",
        "parse_confidence": 0.91,
        "has_low_confidence": False,
        "confidence_scores": {
            "cargo_type": 0.97, "quantity_mt": 0.94, "load_port": 0.96,
            "discharge_port": 0.88, "laycan_start": 0.92, "laycan_end": 0.92,
        },
        "best_match_class": "Panamax",
    },
    {
        "sender": "trading@blacksea-exports.com",
        "subject": "Crude oil cargo - Novo/Med",
        "raw_body": (
            "Dear Brokers,\n\n"
            "HV CRUDE OIL CARGO 80,000 MT\n"
            "LOAD NOVO\n"
            "DISCH MED (TBN)\n"
            "LAYCAN: 5-12 AUGUST 2024\n"
            "RATE: WS 65\n\n"
            "Revert with tonnage.\n"
            "Black Sea Exports"
        ),
        "cargo_type": "crude oil", "quantity_mt": 80000,
        "quantity_min_mt": None, "quantity_max_mt": None,
        "load_port": "Novorossiysk", "load_port_canonical": "Novorossiysk",
        "discharge_port": "Mediterranean", "discharge_port_canonical": "Mediterranean",
        "laycan_start": NOW + timedelta(days=5),
        "laycan_end": NOW + timedelta(days=12),
        "vessel_type": "Aframax",
        "freight_rate": "WS 65",
        "parse_confidence": 0.94,
        "has_low_confidence": False,
        "confidence_scores": {
            "cargo_type": 0.98, "quantity_mt": 0.96, "load_port": 0.95,
            "discharge_port": 0.82, "laycan_start": 0.97, "laycan_end": 0.97,
        },
        "best_match_class": "Aframax",
    },
    {
        "sender": "chartering@eastern-grain.sg",
        "subject": "Fwd: grain enquiry",
        "raw_body": (
            "pls see below and offer\n\n"
            "----\n"
            "50t grain ant/spore aug 8/18\n"
            "any offer?"
        ),
        "cargo_type": "grain", "quantity_mt": 50000,
        "quantity_min_mt": None, "quantity_max_mt": None,
        "load_port": "Antwerp", "load_port_canonical": "Antwerp",
        "discharge_port": "Singapore", "discharge_port_canonical": "Singapore",
        "laycan_start": NOW + timedelta(days=8),
        "laycan_end": NOW + timedelta(days=18),
        "vessel_type": "Handymax",
        "parse_confidence": 0.87,
        "has_low_confidence": True,
        "confidence_scores": {
            "cargo_type": 0.95, "quantity_mt": 0.91, "load_port": 0.96,
            "discharge_port": 0.93, "laycan_start": 0.71, "laycan_end": 0.71,
        },
        "best_match_class": "Handymax",
    },
    {
        "sender": "ops@levant-shipping.com",
        "subject": "Fertiliser - Hamburg/Black Sea",
        "raw_body": (
            "Gents,\n\n"
            "We need a vessel for:\n"
            "25-30,000 MT FERTILISER\n"
            "LOAD: HAMBURG\n"
            "DISCH: ODESSA OR YUZHNY\n"
            "LAYCAN: AUG 20-31\n\n"
            "Thanks"
        ),
        "cargo_type": "fertilizer", "quantity_mt": 27500,
        "quantity_min_mt": 25000, "quantity_max_mt": 30000,
        "load_port": "Hamburg", "load_port_canonical": "Hamburg",
        "discharge_port": "Odessa", "discharge_port_canonical": "Odessa",
        "laycan_start": NOW + timedelta(days=20),
        "laycan_end": NOW + timedelta(days=31),
        "vessel_type": "Handymax",
        "parse_confidence": 0.93,
        "has_low_confidence": False,
        "confidence_scores": {
            "cargo_type": 0.97, "quantity_mt": 0.93, "load_port": 0.96,
            "discharge_port": 0.89, "laycan_start": 0.94, "laycan_end": 0.94,
        },
        "best_match_class": "Handymax",
    },
]


# ── Scoring helpers (mirrors matching/engine.py logic) ────────────────────────

def _size_score(qty, dwt):
    if not qty or not dwt:
        return 0.5
    r = dwt / qty
    if 1.05 <= r <= 1.35: return 1.0
    if 0.95 <= r < 1.05: return 0.85
    if 1.35 < r <= 1.60: return 0.75
    if 0.80 <= r < 0.95: return 0.60
    if 1.60 < r <= 2.00: return 0.50
    return 0.20

AREA_PROXIMITY = {
    ("ARA","ARA"):1.0,("ARA","Continent"):0.9,("ARA","Baltic"):0.75,
    ("ARA","Mediterranean"):0.6,("ARA","Black Sea"):0.5,
    ("ARA","Far East"):0.2,("ARA","Middle East Gulf"):0.3,
    ("Black Sea","Black Sea"):1.0,("Black Sea","Mediterranean"):0.8,
    ("Black Sea","ARA"):0.5,("Mediterranean","Mediterranean"):1.0,
    ("Mediterranean","Black Sea"):0.8,("Mediterranean","ARA"):0.6,
    ("Middle East Gulf","Middle East Gulf"):1.0,
    ("Middle East Gulf","Far East"):0.7,("Far East","Far East"):1.0,
    ("Continent","ARA"):0.9,("Baltic","Baltic"):1.0,("Baltic","ARA"):0.75,
}
PORT_TO_AREA = {
    "Antwerp":"ARA","Amsterdam":"ARA","Rotterdam":"ARA",
    "Hamburg":"Continent","Bremen":"Continent","Gdansk":"Baltic",
    "Novorossiysk":"Black Sea","Odessa":"Black Sea","Yuzhny":"Black Sea",
    "Augusta":"Mediterranean","Piraeus":"Mediterranean",
    "Ras Tanura":"Middle East Gulf","Fujairah":"Middle East Gulf",
    "Singapore":"Far East","Japan (any)":"Far East",
    "South Korea (any)":"Far East","Mediterranean":"Mediterranean",
}
CARGO_COMPAT = {
    "crude oil":["VLCC","Suezmax","Aframax"],
    "fuel oil":["Aframax","Suezmax","VLCC"],
    "grain":["Capesize","Panamax","Handymax","Handysize"],
    "coal":["Capesize","Panamax","Handymax"],
    "fertilizer":["Handymax","Handysize","Panamax"],
    "iron ore":["Capesize","Panamax"],
}

def _geo_score(load_port, vessel_area):
    load_area = PORT_TO_AREA.get(load_port)
    if not load_area or not vessel_area: return 0.5
    return AREA_PROXIMITY.get((load_area, vessel_area)) or \
           AREA_PROXIMITY.get((vessel_area, load_area), 0.2)

def _date_score(laycan_start, open_date):
    if not laycan_start or not open_date: return 0.5
    if open_date.tzinfo is None:
        open_date = open_date.replace(tzinfo=timezone.utc)
    pre = laycan_start - timedelta(days=7)
    if pre <= open_date <= laycan_start: return 1.0
    if open_date < pre:
        days = (pre - open_date).days
        return max(0.3, 0.8 - days * 0.05)
    days = (open_date - laycan_start).days
    return max(0.0, 0.85 - days * 0.04)

def _cargo_score(cargo, vessel_class):
    if not cargo or not vessel_class: return 0.5
    compat = CARGO_COMPAT.get(cargo.lower(), [])
    if not compat: return 0.5
    if vessel_class in compat: return 1.0
    return 0.0

def _total_score(s, g, d, c):
    return round(0.35*s + 0.30*g + 0.20*d + 0.15*c, 4)


# ── Seed function ─────────────────────────────────────────────────────────────

def seed(db_url: str = DB_URL, clear_existing: bool = True):
    engine = create_engine(db_url, pool_pre_ping=True)
    create_tables(engine)

    with Session(engine) as session:
        if clear_existing:
            print("  Clearing existing seed data...")
            session.query(MatchResult).delete()
            session.query(InboundOrder).delete()
            session.query(SignalCacheRefresh).delete()
            session.query(CachedVessel).delete()
            session.commit()

        # ── 1. Seed vessels ───────────────────────────────────────────────────
        print(f"  Seeding {len(VESSELS)} vessels...")
        vessel_rows = {}
        for v in VESSELS:
            row = CachedVessel(
                id=uuid.uuid4(),
                cached_at=NOW,
                vessel_id=v["vessel_id"],
                vessel_name=v["vessel_name"],
                vessel_class=v["vessel_class"],
                dwt=v["dwt"],
                built_year=v["built_year"],
                open_port=v["open_port"],
                open_port_area=v["open_port_area"],
                open_date=v["open_date"].replace(tzinfo=None),
                commercial_status=v["commercial_status"],
                market_deployment=v["market_deployment"],
                latitude=v["latitude"],
                longitude=v["longitude"],
                last_ais_update=(NOW - timedelta(hours=random.randint(1, 12))).replace(tzinfo=None),
                cargo_types=v["cargo_types"],
                raw_signal_data={},
            )
            session.add(row)
            vessel_rows[v["vessel_id"]] = (row, v)
        session.commit()

        # ── 2. Seed cache refresh log ─────────────────────────────────────────
        print("  Seeding cache refresh log...")
        for i in range(6):
            session.add(SignalCacheRefresh(
                id=uuid.uuid4(),
                refreshed_at=(NOW - timedelta(minutes=5 * i)).replace(tzinfo=None),
                vessel_count=len(VESSELS),
                success=True,
                duration_ms=random.randint(800, 2400),
            ))
        session.commit()

        # ── 3. Seed inbound orders + matches ──────────────────────────────────
        print(f"  Seeding {len(INBOUND_MESSAGES)} inbound orders with matches...")
        for idx, msg in enumerate(INBOUND_MESSAGES):
            order_id = uuid.uuid4()
            age_minutes = idx * 18
            received = (NOW - timedelta(minutes=age_minutes)).replace(tzinfo=None)

            order_row = InboundOrder(
                id=order_id,
                received_at=received,
                sender=msg["sender"],
                subject=msg["subject"],
                raw_body=msg["raw_body"],
                cargo_type=msg["cargo_type"],
                quantity_mt=msg["quantity_mt"],
                quantity_min_mt=msg.get("quantity_min_mt"),
                quantity_max_mt=msg.get("quantity_max_mt"),
                load_port=msg["load_port"],
                load_port_canonical=msg["load_port_canonical"],
                discharge_port=msg["discharge_port"],
                discharge_port_canonical=msg["discharge_port_canonical"],
                laycan_start=msg["laycan_start"].replace(tzinfo=None),
                laycan_end=msg["laycan_end"].replace(tzinfo=None),
                vessel_type=msg["vessel_type"],
                freight_rate=msg.get("freight_rate"),
                parse_confidence=msg["parse_confidence"],
                has_low_confidence_fields=msg["has_low_confidence"],
                confidence_scores=msg["confidence_scores"],
            )
            session.add(order_row)
            session.flush()

            # Score every available vessel against this order
            scores = []
            for vessel_id, (vrow, vdata) in vessel_rows.items():
                if vdata["commercial_status"] == "on_subs":
                    continue
                s = _size_score(msg["quantity_mt"], vdata["dwt"])
                g = _geo_score(msg["load_port_canonical"], vdata["open_port_area"])
                d = _date_score(msg["laycan_start"], vdata["open_date"])
                c = _cargo_score(msg["cargo_type"], vdata["vessel_class"])
                t = _total_score(s, g, d, c)
                scores.append((t, s, g, d, c, vrow, vdata))

            scores.sort(key=lambda x: x[0], reverse=True)

            for rank, (t, s, g, d, c, vrow, vdata) in enumerate(scores[:5], start=1):
                session.add(MatchResult(
                    id=uuid.uuid4(),
                    order_id=order_id,
                    matched_at=received,
                    vessel_id=vdata["vessel_id"],
                    vessel_name=vdata["vessel_name"],
                    vessel_class=vdata["vessel_class"],
                    dwt=vdata["dwt"],
                    open_port=vdata["open_port"],
                    open_date=vdata["open_date"].replace(tzinfo=None),
                    rank=rank,
                    total_score=t,
                    score_vessel_size=s,
                    score_geography=g,
                    score_date_overlap=d,
                    score_cargo_type=c,
                ))

        session.commit()
        print()
        print("  ✓  Seed complete.")
        print(f"     {len(VESSELS)} vessels in cache")
        print(f"     {len(INBOUND_MESSAGES)} inbound orders")
        print(f"     {len(INBOUND_MESSAGES) * 5} match results")
        print()
        print("  The frontend developer can now connect to:")
        print("     GET  /orders/latest    — order list")
        print("     GET  /matches/{id}     — ranked matches")
        print("     GET  /status           — cache health")
        print("     WS   /ws               — live push")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed the maritime middleware database")
    parser.add_argument("--db", default=DB_URL, help="PostgreSQL connection URL")
    parser.add_argument("--no-clear", action="store_true", help="Keep existing data")
    args = parser.parse_args()
    print(f"\nSeeding database: {args.db}\n")
    seed(args.db, clear_existing=not args.no_clear)
