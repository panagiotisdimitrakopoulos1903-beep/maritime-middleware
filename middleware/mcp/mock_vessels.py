"""
Mock Signal Ocean data source for the Signal Ocean MCP server.

Used when SIGNAL_MODE=mock (see .env). Lets Agents 3/4 build and test the
matching engine before OQ-01 is resolved (confirming the broker's Signal
Ocean subscription covers Tonnage List / Distances / Scraped Data APIs).
Swapping SIGNAL_MODE=live in .env switches to signal_client.py with zero
code changes elsewhere.

Vessel data shape mirrors the Signal Ocean SDK's tonnage list response
closely enough to develop the matching engine (PRD section 9) against it.
"""

from datetime import datetime, timedelta, timezone

_NOW = datetime.now(timezone.utc)


def _days_from_now(d: int) -> str:
    return (_NOW + timedelta(days=d)).isoformat()


# 14 vessels spanning the classes referenced in the PRD glossary
# (Handymax, Panamax, Capesize, Aframax, Suezmax, VLCC), spread across
# open ports so geography scoring (PRD 9.2) has real variance to work with.
MOCK_VESSELS = [
    {"vessel_id": "v-001", "vessel_name": "Ocean Harvest", "vessel_class": "Handymax", "dwt": 52000,
     "open_port": "Odessa", "open_port_area": "Black Sea", "open_date": _days_from_now(3),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 46.48, "longitude": 30.72,
     "cargo_types": ["grain", "fertiliser", "sugar"]},
    {"vessel_id": "v-002", "vessel_name": "Baltic Trader", "vessel_class": "Handymax", "dwt": 48000,
     "open_port": "Alexandria", "open_port_area": "East Mediterranean", "open_date": _days_from_now(7),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 31.2, "longitude": 29.92,
     "cargo_types": ["grain", "wheat"]},
    {"vessel_id": "v-003", "vessel_name": "Antwerp Grace", "vessel_class": "Panamax", "dwt": 74000,
     "open_port": "Antwerp", "open_port_area": "ARA", "open_date": _days_from_now(-1),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 51.22, "longitude": 4.4,
     "cargo_types": ["grain", "coal"]},
    {"vessel_id": "v-004", "vessel_name": "Panamax Star", "vessel_class": "Panamax", "dwt": 76500,
     "open_port": "Yokohama", "open_port_area": "Far East", "open_date": _days_from_now(9),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 35.44, "longitude": 139.64,
     "cargo_types": ["grain"]},
    {"vessel_id": "v-005", "vessel_name": "Cape Endeavour", "vessel_class": "Capesize", "dwt": 178000,
     "open_port": "Tubarao", "open_port_area": "East Coast South America", "open_date": _days_from_now(4),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": -20.28, "longitude": -40.26,
     "cargo_types": ["iron ore", "coal"]},
    {"vessel_id": "v-006", "vessel_name": "Cape Fortune", "vessel_class": "Capesize", "dwt": 182000,
     "open_port": "Qingdao", "open_port_area": "Far East", "open_date": _days_from_now(11),
     "commercial_status": "On subs", "market_deployment": "Spot", "latitude": 36.07, "longitude": 120.38,
     "cargo_types": ["iron ore"]},
    {"vessel_id": "v-007", "vessel_name": "Ras Voyager", "vessel_class": "Aframax", "dwt": 105000,
     "open_port": "Ras Tanura", "open_port_area": "Middle East Gulf", "open_date": _days_from_now(2),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 26.65, "longitude": 50.16,
     "cargo_types": ["crude"]},
    {"vessel_id": "v-008", "vessel_name": "Gulf Pioneer", "vessel_class": "Aframax", "dwt": 112000,
     "open_port": "Rotterdam", "open_port_area": "ARA", "open_date": _days_from_now(6),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 51.9, "longitude": 4.48,
     "cargo_types": ["crude", "fuel oil"]},
    {"vessel_id": "v-009", "vessel_name": "Atlantic Suez", "vessel_class": "Suezmax", "dwt": 158000,
     "open_port": "Lagos", "open_port_area": "West Africa", "open_date": _days_from_now(8),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 6.45, "longitude": 3.39,
     "cargo_types": ["crude"]},
    {"vessel_id": "v-010", "vessel_name": "Suez Horizon", "vessel_class": "Suezmax", "dwt": 161000,
     "open_port": "Houston", "open_port_area": "US Gulf", "open_date": _days_from_now(12),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 29.75, "longitude": -95.36,
     "cargo_types": ["crude"]},
    {"vessel_id": "v-011", "vessel_name": "Global Crude I", "vessel_class": "VLCC", "dwt": 305000,
     "open_port": "Fujairah", "open_port_area": "Middle East Gulf", "open_date": _days_from_now(15),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 25.12, "longitude": 56.34,
     "cargo_types": ["crude"]},
    {"vessel_id": "v-012", "vessel_name": "Pacific Titan", "vessel_class": "VLCC", "dwt": 298000,
     "open_port": "Singapore", "open_port_area": "SE Asia", "open_date": _days_from_now(18),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 1.29, "longitude": 103.85,
     "cargo_types": ["crude"]},
    {"vessel_id": "v-013", "vessel_name": "Danube Spirit", "vessel_class": "Handymax", "dwt": 45000,
     "open_port": "Constanta", "open_port_area": "Black Sea", "open_date": _days_from_now(5),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": 44.17, "longitude": 28.65,
     "cargo_types": ["grain", "sugar"]},
    {"vessel_id": "v-014", "vessel_name": "Ore Navigator", "vessel_class": "Capesize", "dwt": 175000,
     "open_port": "Port Hedland", "open_port_area": "Australia", "open_date": _days_from_now(10),
     "commercial_status": "Open", "market_deployment": "Spot", "latitude": -20.31, "longitude": 118.6,
     "cargo_types": ["iron ore"]},
]

VESSEL_CLASSES = ["Handymax", "Panamax", "Capesize", "Aframax", "Suezmax", "VLCC"]

# Minimal port/area lookup — a stand-in for the 400+ entry alias table
# (core/ports.py) the parser normalises against.
PORTS = [
    {"name": "Odessa", "area": "Black Sea", "country": "Ukraine"},
    {"name": "Constanta", "area": "Black Sea", "country": "Romania"},
    {"name": "Alexandria", "area": "East Mediterranean", "country": "Egypt"},
    {"name": "Antwerp", "area": "ARA", "country": "Belgium"},
    {"name": "Rotterdam", "area": "ARA", "country": "Netherlands"},
    {"name": "Yokohama", "area": "Far East", "country": "Japan"},
    {"name": "Qingdao", "area": "Far East", "country": "China"},
    {"name": "Tubarao", "area": "East Coast South America", "country": "Brazil"},
    {"name": "Ras Tanura", "area": "Middle East Gulf", "country": "Saudi Arabia"},
    {"name": "Fujairah", "area": "Middle East Gulf", "country": "UAE"},
    {"name": "Lagos", "area": "West Africa", "country": "Nigeria"},
    {"name": "Houston", "area": "US Gulf", "country": "USA"},
    {"name": "Singapore", "area": "SE Asia", "country": "Singapore"},
    {"name": "Port Hedland", "area": "Australia", "country": "Australia"},
]

# Straight-line-ish placeholder distances in nautical miles between a
# few common port pairs. The real Distances API returns actual sea
# routes; this only exists to unblock geography-scoring development.
_DISTANCES = {
    ("Odessa", "Alexandria"): 720,
    ("Rotterdam", "Ras Tanura"): 6100,
    ("Tubarao", "Qingdao"): 11500,
    ("Ras Tanura", "Rotterdam"): 6100,
}


def get_tonnage_list(vessel_class: str = None, area: str = None, cargo_type: str = None):
    results = MOCK_VESSELS
    if vessel_class:
        results = [v for v in results if v["vessel_class"].lower() == vessel_class.lower()]
    if area:
        results = [v for v in results if area.lower() in v["open_port_area"].lower()]
    if cargo_type:
        results = [v for v in results if any(cargo_type.lower() in c for c in v["cargo_types"])]
    return results


def get_vessel(vessel_id: str):
    for v in MOCK_VESSELS:
        if v["vessel_id"] == vessel_id:
            return v
    return None


def get_ports(query: str = None):
    if query:
        q = query.lower()
        return [p for p in PORTS if q in p["name"].lower() or q in p["area"].lower()]
    return PORTS


def get_distances(from_port: str, to_port: str):
    key = (from_port, to_port)
    reverse_key = (to_port, from_port)
    distance = _DISTANCES.get(key) or _DISTANCES.get(reverse_key)
    return {
        "from_port": from_port,
        "to_port": to_port,
        "distance_nm": distance if distance is not None else None,
        "note": None if distance is not None else "No mock distance for this pair — real Distances API required",
    }


def get_vessel_classes():
    return VESSEL_CLASSES


def refresh_status():
    return {
        "mode": "mock",
        "last_refreshed": _NOW.isoformat(),
        "vessel_count": len(MOCK_VESSELS),
        "success": True,
        "duration_ms": 0,
    }
