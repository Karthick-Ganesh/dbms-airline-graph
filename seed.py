"""Seed the Neo4j airline graph.

Connection settings come from the environment:
    NEO4J_URI       (default bolt://localhost:7687)
    NEO4J_USER      (default neo4j)
    NEO4J_PASSWORD  (required unless --dry-run)
"""

import argparse
import json
import math
import os
import random
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent

# ICAO Aircraft Engine Emissions Databank -> :Engine
ICAO_EDB_FILE = ROOT / "edb-emissions-databank_v32__web_.xlsx"
ICAO_EDB_SHEET = "Gaseous Emissions and Smoke"
# Source header -> Engine property. Note the source "Fuel LTO Cycle (kg)  "
# header has two trailing spaces; headers are stripped before renaming.
ENGINE_COLUMNS = {
    "UID No": "engine_uid",
    "Manufacturer": "manufacturer",
    "Engine Identification": "engine_model",
    "B/P Ratio": "bypass_ratio",
    "Rated Thrust (kN)": "rated_thrust_kn",
    "Fuel LTO Cycle (kg)": "fuel_lto_cycle_kg",
}

# OurAirports -> :Airport
AIRPORTS_FILE = ROOT / "airports_new.csv"
RUNWAYS_FILE = ROOT / "runways.csv"
FT_TO_M = 0.3048
# Tie-break when several rows share an IATA code (e.g. MUC is both Munich
# Airport and the closed Munich-Riem): lower rank wins.
AIRPORT_TYPE_RANK = {"large_airport": 0, "medium_airport": 1, "small_airport": 2,
                     "seaplane_base": 3, "heliport": 4, "closed": 5}
# OurAirports continent code -> Airport.region. "NA" is North America here.
CONTINENT_NAMES = {"AF": "Africa", "AN": "Antarctica", "AS": "Asia", "EU": "Europe",
                   "NA": "North America", "OC": "Oceania", "SA": "South America"}

# Per-airport route JSON: routes are loaded by seed3.py; seed.py only takes
# each airport's IANA timezone from it.
ROUTES_JSON_FILE = ROOT / "airline_routes.json"
# If the JSON's coordinates for an IATA code are this far from ours, it's a
# different airport (a reassigned code, e.g. IAL is Salinas in the JSON but
# Ialibu in OurAirports) or the JSON's location is corrupt: don't trust it.
AIRPORT_MISMATCH_KM = 50

# OpenFlights airlines.dat (headerless) -> :Airline
AIRLINES_FILE = ROOT / "airlines.csv"
AIRLINE_COLUMNS = {1: "name", 3: "airline_id", 6: "country", 7: "active"}
OPENFLIGHTS_NULL = "\\N"
FLEET_SIZE_RANGE = (10, 300)
# Fixed seed so re-running the seed script doesn't reshuffle fleet sizes.
FLEET_SIZE_SEED = 302

BATCH_SIZE = 500

CONSTRAINTS = [
    "CREATE CONSTRAINT engine_uid IF NOT EXISTS "
    "FOR (e:Engine) REQUIRE e.engine_uid IS UNIQUE",
    "CREATE CONSTRAINT airport_id IF NOT EXISTS "
    "FOR (a:Airport) REQUIRE a.airport_id IS UNIQUE",
    "CREATE CONSTRAINT airline_id IF NOT EXISTS "
    "FOR (al:Airline) REQUIRE al.airline_id IS UNIQUE",
]

MERGE_ENGINES = """
UNWIND $rows AS row
MERGE (e:Engine {engine_uid: row.engine_uid})
SET e += row
"""

MERGE_AIRPORTS = """
UNWIND $rows AS row
MERGE (a:Airport {airport_id: row.iata_code})
SET a.name = row.name,
    a.city = row.municipality,
    a.country = row.iso_country,
    a.elevation_m = row.elevation_m,
    a.runway_count = row.runway_count,
    a.is_hub = row.is_hub,
    a.daily_capacity = row.daily_capacity,
    a.region = row.region,
    a.timezone = row.timezone,
    a.location = point({latitude: row.latitude_deg, longitude: row.longitude_deg}),
    // Keep any simulated disruption across re-seeds.
    a.operational_status = coalesce(a.operational_status, 'ACTIVE')
"""

MERGE_AIRLINES = """
UNWIND $rows AS row
MERGE (al:Airline {airline_id: row.IATA})
SET al.name = row.name,
    al.country = row.country,
    al.fleet_size = row.fleet_size
"""


def _records(df):
    """DataFrame -> list of dicts, with NaN turned into None (Cypher null)."""
    return [
        {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in rec.items()}
        for rec in df.to_dict("records")
    ]


def _read_csv(path, **kwargs):
    # keep_default_na=False: otherwise pandas reads Namibia's iso_country
    # "NA" as missing. Only empty fields count as null.
    return pd.read_csv(path, keep_default_na=False, na_values=[""], encoding="utf-8", **kwargs)


def read_engines(path=ICAO_EDB_FILE):
    """Return :Engine property maps extracted from the ICAO databank."""
    df = pd.read_excel(path, sheet_name=ICAO_EDB_SHEET)
    df.columns = df.columns.str.strip()
    df = df[list(ENGINE_COLUMNS)].rename(columns=ENGINE_COLUMNS)

    for col in ("engine_uid", "manufacturer", "engine_model"):
        df[col] = df[col].astype(str).str.strip()
    for col in ("bypass_ratio", "rated_thrust_kn", "fuel_lto_cycle_kg"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

    # Drop missing values so Neo4j simply omits the property.
    return [
        {k: v for k, v in rec.items() if v is not None}
        for rec in _records(df)
    ]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def mismatched_airports(js, airports):
    """IATA codes whose route-JSON location disagrees with our airport row.

    `airports` maps IATA code -> row with latitude_deg / longitude_deg.
    """
    bad = {}
    for code, a in js.items():
        if code in airports and a.get("latitude") and a.get("longitude"):
            ours = airports[code]
            km = haversine_km(float(a["latitude"]), float(a["longitude"]),
                              ours["latitude_deg"], ours["longitude_deg"])
            if km > AIRPORT_MISMATCH_KM:
                bad[code] = (a.get("name"), a.get("country_code"), ours["name"], ours["iso_country"], round(km))
    return bad


def read_airports(airports_path=AIRPORTS_FILE, runways_path=RUNWAYS_FILE,
                  routes_json_path=ROUTES_JSON_FILE):
    """Return :Airport rows from OurAirports, one per IATA code."""
    runway_counts = (
        _read_csv(runways_path).groupby("airport_ident").size().rename("runway_count")
    )
    df = _read_csv(airports_path)
    df = df.merge(runway_counts, left_on="ident", right_index=True, how="left")
    df["runway_count"] = df["runway_count"].fillna(0).astype(int)

    df = df.dropna(subset=["iata_code"])
    # Also drops placeholder codes such as "0", "-" and "BR-".
    df = df[df["iata_code"].str.fullmatch(r"[A-Z0-9]{3}")]

    # Several rows can share an IATA code; keep the one that's actually in
    # service rather than whichever happens to come last in the file.
    df = df.assign(
        _type_rank=df["type"].map(AIRPORT_TYPE_RANK).fillna(len(AIRPORT_TYPE_RANK)),
        _no_service=df["scheduled_service"].ne("yes"),
    ).sort_values(["iata_code", "_type_rank", "_no_service"], kind="stable")
    df = df.drop_duplicates("iata_code", keep="first")

    df["elevation_m"] = (df["elevation_ft"] * FT_TO_M).round(1)
    df["is_hub"] = df["type"].eq("large_airport")
    df["daily_capacity"] = df["is_hub"].map({True: 500, False: 100})
    df["region"] = df["continent"].map(CONTINENT_NAMES)
    js = read_json(routes_json_path)
    mismatched = mismatched_airports(js, df.set_index("iata_code").to_dict("index"))
    df["timezone"] = df["iata_code"].map(
        {code: a.get("timezone") for code, a in js.items() if code not in mismatched})

    return _records(df[[
        "iata_code", "name", "municipality", "iso_country", "elevation_m",
        "runway_count", "is_hub", "daily_capacity", "region", "timezone",
        "latitude_deg", "longitude_deg",
    ]])


GENERIC_AIRLINE_WORDS = {"air", "airlines", "airline", "airways", "aviation", "the", "fly", "flying"}


def _name_key(name):
    """First distinctive word: 'Air India Limited' and 'Air India' -> 'india'."""
    words = [w for w in str(name).lower().replace("-", " ").split() if w not in GENERIC_AIRLINE_WORDS]
    return words[0] if words else str(name).lower()


def read_current_carriers(path=ROUTES_JSON_FILE):
    """IATA code -> current airline name, from the route JSON's carriers."""
    return {c["iata"]: c["name"]
            for a in read_json(path).values() for r in a.get("routes") or []
            for c in r.get("carriers") or [] if c.get("iata") and c.get("name")}


def read_airlines(path=AIRLINES_FILE, routes_json_path=ROUTES_JSON_FILE):
    """Return :Airline rows, one per IATA code.

    OpenFlights (~2014) is the base. IATA codes get reassigned (D8 was
    Djibouti Airlines, now Norwegian), so for codes flying in the current
    route JSON its carrier name wins, the stale country is dropped when the
    code clearly changed hands, and carriers missing from OpenFlights are added.
    """
    df = _read_csv(path, header=None)[list(AIRLINE_COLUMNS)].rename(columns=AIRLINE_COLUMNS)
    df = df.replace(OPENFLIGHTS_NULL, None)

    df = df.dropna(subset=["airline_id"])
    df = df[df["airline_id"] != "-"]
    # Also drops junk codes such as "++", "??" and Cyrillic "ЯП".
    df = df[df["airline_id"].str.fullmatch(r"[A-Z0-9]{2}")]

    # IATA codes get reassigned over time; prefer the active carrier
    # (e.g. LH -> Lufthansa, not a defunct holder of the code).
    df = df.assign(_inactive=df["active"].str.upper().ne("Y"))
    df = df.sort_values(["airline_id", "_inactive"], kind="stable")
    df = df.drop_duplicates("airline_id", keep="first")

    rng = random.Random(FLEET_SIZE_SEED)
    df["fleet_size"] = [rng.randint(*FLEET_SIZE_RANGE) for _ in range(len(df))]

    current = read_current_carriers(routes_json_path)
    new_name = df["airline_id"].map(current)
    reassigned = new_name.notna() & (new_name.map(_name_key) != df["name"].map(_name_key))
    df.loc[reassigned, "country"] = None
    df["name"] = new_name.fillna(df["name"])

    # Generated after the existing rows so their fleet sizes don't change.
    missing = sorted(c for c in current if c not in set(df["airline_id"]) and len(c) == 2)
    df = pd.concat([df, pd.DataFrame({
        "airline_id": missing, "name": [current[c] for c in missing], "country": None,
        "fleet_size": [rng.randint(*FLEET_SIZE_RANGE) for _ in missing],
    })], ignore_index=True)

    df = df.rename(columns={"airline_id": "IATA"})
    return _records(df[["IATA", "name", "country", "fleet_size"]])


def load(session, query, rows):
    for i in range(0, len(rows), BATCH_SIZE):
        session.execute_write(
            lambda tx, batch: tx.run(query, rows=batch).consume(),
            rows[i:i + BATCH_SIZE],
        )


def _load_dotenv(path=ROOT / ".env"):
    """Put KEY=VALUE lines from .env into os.environ (real env vars win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def neo4j_driver():
    """Driver built from NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD (env or .env)."""
    _load_dotenv()
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise SystemExit("set NEO4J_PASSWORD in .env or the environment (or pass --dry-run)")
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    return GraphDatabase.driver(uri, auth=(user, password))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="parse source data and report, without touching Neo4j")
    args = parser.parse_args()

    datasets = [
        ("Engine", MERGE_ENGINES, read_engines()),
        ("Airport", MERGE_AIRPORTS, read_airports()),
        ("Airline", MERGE_AIRLINES, read_airlines()),
    ]
    for label, _, rows in datasets:
        print(f"{label}: {len(rows)} rows read")

    if args.dry_run:
        for label, _, rows in datasets:
            print(f"{label} sample:", rows[0])
        return

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt).consume()
            for label, query, rows in datasets:
                load(session, query, rows)
                count = session.run(f"MATCH (n:{label}) RETURN count(n) AS n").single()["n"]
                print(f"{label}: {count} nodes in Neo4j")


if __name__ == "__main__":
    main()
