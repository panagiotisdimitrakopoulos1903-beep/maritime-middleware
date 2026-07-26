"""
core/ports.py — port name normalisation
Maps abbreviations and aliases to canonical Signal Ocean port names.
"""
from typing import Optional
import re

# ── Port alias table ──────────────────────────────────────────────────────────
# Format: alias (lowercase) → canonical name
PORT_ALIASES: dict[str, str] = {
    # ARA range
    "ant": "Antwerp",
    "antwrp": "Antwerp",
    "antwerp": "Antwerp",
    "ams": "Amsterdam",
    "amsterdam": "Amsterdam",
    "rtm": "Rotterdam",
    "rot": "Rotterdam",
    "rotterdam": "Rotterdam",
    "ara": "ARA Range",
    "ara range": "ARA Range",

    # Mediterranean
    "aug": "Augusta",
    "augusta": "Augusta",
    "trieste": "Trieste",
    "genoa": "Genoa",
    "gen": "Genoa",
    "piraeus": "Piraeus",
    "pir": "Piraeus",
    "istanbul": "Istanbul",
    "constanza": "Constanza",
    "odessa": "Odessa",

    # Black Sea
    "novo": "Novorossiysk",
    "novorossiysk": "Novorossiysk",
    "nvs": "Novorossiysk",
    "tuapse": "Tuapse",
    "tps": "Tuapse",
    "yuzhny": "Yuzhny",
    "yuz": "Yuzhny",
    "kavkaz": "Kavkaz",

    # Middle East Gulf
    "ras tanura": "Ras Tanura",
    "rast": "Ras Tanura",
    "kharg": "Kharg Island",
    "basrah": "Basrah",
    "mina al ahmadi": "Mina Al Ahmadi",
    "maa": "Mina Al Ahmadi",
    "fujairah": "Fujairah",
    "fuj": "Fujairah",

    # Far East
    "spore": "Singapore",
    "sin": "Singapore",
    "singapore": "Singapore",
    "japan": "Japan (any)",
    "jpn": "Japan (any)",
    "jap": "Japan (any)",
    "yokohama": "Yokohama",
    "yok": "Yokohama",
    "chiba": "Chiba",
    "kawasaki": "Kawasaki",
    "korea": "South Korea (any)",
    "kor": "South Korea (any)",
    "china": "China (any)",
    "chn": "China (any)",

    # US Gulf / Atlantic
    "usg": "US Gulf",
    "houston": "Houston",
    "hou": "Houston",
    "new orleans": "New Orleans",
    "nola": "New Orleans",
    "usac": "US Atlantic Coast",

    # West Africa
    "bonny": "Bonny",
    "qua iboe": "Qua Iboe",
    "qi": "Qua Iboe",
    "cabinda": "Cabinda",
    "cab": "Cabinda",

    # Baltic
    "primorsk": "Primorsk",
    "pri": "Primorsk",
    "tallinn": "Tallinn",
    "klaipeda": "Klaipeda",
    "gdansk": "Gdansk",
    "ventspils": "Ventspils",
}

# ── Geographic areas for scoring ──────────────────────────────────────────────
PORT_TO_AREA: dict[str, str] = {
    "Antwerp": "ARA",
    "Amsterdam": "ARA",
    "Rotterdam": "ARA",
    "ARA Range": "ARA",
    "Hamburg": "Continent",
    "Bremen": "Continent",
    "Novorossiysk": "Black Sea",
    "Tuapse": "Black Sea",
    "Yuzhny": "Black Sea",
    "Kavkaz": "Black Sea",
    "Odessa": "Black Sea",
    "Augusta": "Mediterranean",
    "Genoa": "Mediterranean",
    "Trieste": "Mediterranean",
    "Piraeus": "Mediterranean",
    "Istanbul": "Mediterranean",
    "Ras Tanura": "Middle East Gulf",
    "Kharg Island": "Middle East Gulf",
    "Basrah": "Middle East Gulf",
    "Mina Al Ahmadi": "Middle East Gulf",
    "Fujairah": "Middle East Gulf",
    "Singapore": "Far East",
    "Japan (any)": "Far East",
    "Yokohama": "Far East",
    "Chiba": "Far East",
    "South Korea (any)": "Far East",
    "China (any)": "Far East",
    "US Gulf": "US Gulf",
    "Houston": "US Gulf",
    "New Orleans": "US Gulf",
    "US Atlantic Coast": "US Atlantic",
    "Bonny": "West Africa",
    "Qua Iboe": "West Africa",
    "Cabinda": "West Africa",
    "Primorsk": "Baltic",
    "Tallinn": "Baltic",
    "Klaipeda": "Baltic",
    "Gdansk": "Baltic",
    "Ventspils": "Baltic",
}

# ── Area proximity matrix ─────────────────────────────────────────────────────
# Score 1.0 = same area, decreasing by sea routing logic
AREA_PROXIMITY: dict[tuple[str, str], float] = {
    ("ARA", "ARA"): 1.0,
    ("ARA", "Continent"): 0.9,
    ("ARA", "Baltic"): 0.75,
    ("ARA", "Mediterranean"): 0.6,
    ("ARA", "Black Sea"): 0.5,
    ("ARA", "Middle East Gulf"): 0.3,
    ("ARA", "West Africa"): 0.4,
    ("ARA", "US Gulf"): 0.4,
    ("ARA", "Far East"): 0.2,
    ("Black Sea", "Black Sea"): 1.0,
    ("Black Sea", "Mediterranean"): 0.8,
    ("Black Sea", "ARA"): 0.5,
    ("Black Sea", "Middle East Gulf"): 0.45,
    ("Mediterranean", "Mediterranean"): 1.0,
    ("Mediterranean", "Black Sea"): 0.8,
    ("Mediterranean", "ARA"): 0.6,
    ("Mediterranean", "Middle East Gulf"): 0.5,
    ("Middle East Gulf", "Middle East Gulf"): 1.0,
    ("Middle East Gulf", "Far East"): 0.7,
    ("Middle East Gulf", "Mediterranean"): 0.5,
    ("Far East", "Far East"): 1.0,
    ("Far East", "Middle East Gulf"): 0.7,
    ("US Gulf", "US Gulf"): 1.0,
    ("US Gulf", "US Atlantic"): 0.8,
    ("US Gulf", "ARA"): 0.4,
    ("West Africa", "West Africa"): 1.0,
    ("West Africa", "ARA"): 0.45,
    ("West Africa", "Mediterranean"): 0.4,
    ("Baltic", "Baltic"): 1.0,
    ("Baltic", "ARA"): 0.75,
    ("Baltic", "Continent"): 0.8,
}


def normalise_port(raw: str) -> Optional[str]:
    """
    Convert any broker shorthand to a canonical port name.
    Returns None if the port cannot be resolved.
    """
    if not raw:
        return None
    cleaned = raw.strip().lower()
    cleaned = re.sub(r"[^a-z0-9\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return PORT_ALIASES.get(cleaned) or PORT_ALIASES.get(cleaned.split()[0])


def get_area(canonical_port: str) -> Optional[str]:
    return PORT_TO_AREA.get(canonical_port)


def geography_score(load_port_canonical: str, vessel_open_area: str) -> float:
    """
    Score how well a vessel's open area matches the load port area.
    Returns 0.0–1.0.
    """
    load_area = get_area(load_port_canonical)
    if not load_area or not vessel_open_area:
        return 0.5  # unknown — neutral score

    key = (load_area, vessel_open_area)
    rev_key = (vessel_open_area, load_area)
    return AREA_PROXIMITY.get(key) or AREA_PROXIMITY.get(rev_key, 0.2)
