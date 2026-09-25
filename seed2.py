"""Seed :Aircraft nodes from commercial_aircraft_dataset.csv.

Uses the same Neo4j connection settings as seed.py (NEO4J_URI, NEO4J_USER,
NEO4J_PASSWORD). Creates nodes only; POWERED_BY is loaded separately.

CO2 is not stored on the node: it's derived in Cypher as
avg_fuel_burn_kg_per_km * 3.16 (kg CO2 per kg jet fuel).
"""

import argparse
import random

from seed import ROOT, _read_csv, _records, load, neo4j_driver

AIRCRAFT_FILE = ROOT / "commercial_aircraft_dataset.csv"
AIRCRAFT_COLUMNS = {
    "aircraft_id": "aircraft_type_id",
    "Manufacturer": "manufacturer",
    "Model": "model",
    "Category": "category",
    "Max Capacity": "seating_capacity",
    "Range (KM)": "max_range_km",
}

# Synthetic cruise fuel burn per category, kg/km (uniform draw).
FUEL_BURN_RANGE_KG_PER_KM = {
    "Widebody": (6.0, 9.0),
    "Narrowbody": (2.5, 4.0),
    "Regional": (1.5, 2.5),
    "Turboprop": (0.5, 1.2),
    "Private Jet": (1.0, 2.0),
    "Freighter": (8.0, 12.0),
    "Supersonic": (18.0, 22.0),
}
# Fixed seed so re-running doesn't reshuffle fuel burn (and every CO2 weight
# derived from it).
FUEL_BURN_SEED = 302

# First match wins; anything unmatched has 2 engines.
ENGINE_COUNT_EXACT = {"An-225 Mriya": 6}
ENGINE_COUNT_CONTAINS = [
    (("A380", "747", "An-124", "Concorde", "Il-96", "BAe 146", "Avro RJ"), 4),
    (("MD-11", "DC-10", "727", "L-1011"), 3),
    (("Caravan", "PC-12"), 1),
]
DEFAULT_ENGINE_COUNT = 2

CONSTRAINTS = [
    "CREATE CONSTRAINT aircraft_type_id IF NOT EXISTS "
    "FOR (ac:Aircraft) REQUIRE ac.aircraft_type_id IS UNIQUE",
]

MERGE_AIRCRAFT = """
UNWIND $rows AS row
MERGE (ac:Aircraft {aircraft_type_id: row.aircraft_type_id})
SET ac += row
REMOVE ac.co2_emission_factor_kg_per_km
"""


def engine_count(model):
    if model in ENGINE_COUNT_EXACT:
        return ENGINE_COUNT_EXACT[model]
    for needles, count in ENGINE_COUNT_CONTAINS:
        if any(n in model for n in needles):
            return count
    return DEFAULT_ENGINE_COUNT


def read_aircraft(path=AIRCRAFT_FILE):
    """Return :Aircraft property maps, one per aircraft_type_id."""
    df = _read_csv(path)[list(AIRCRAFT_COLUMNS)].rename(columns=AIRCRAFT_COLUMNS)
    for col in ("aircraft_type_id", "manufacturer", "model", "category"):
        df[col] = df[col].str.strip()
    df = df.dropna(subset=["aircraft_type_id"])

    unknown = set(df["category"]) - set(FUEL_BURN_RANGE_KG_PER_KM)
    if unknown:
        raise ValueError(f"no fuel burn range for categories: {sorted(unknown)}")

    # Variants can share an ICAO type code (A321, A321LR, A321XLR are all
    # "A321"); keep the base model, which is listed first.
    df = df.drop_duplicates("aircraft_type_id", keep="first")

    rng = random.Random(FUEL_BURN_SEED)
    df["avg_fuel_burn_kg_per_km"] = [
        round(rng.uniform(*FUEL_BURN_RANGE_KG_PER_KM[c]), 2) for c in df["category"]
    ]
    df["engine_count"] = df["model"].map(engine_count)

    return _records(df)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="parse source data and report, without touching Neo4j")
    args = parser.parse_args()

    aircraft = read_aircraft()
    print(f"Aircraft: {len(aircraft)} rows read")

    if args.dry_run:
        print("Aircraft sample:", aircraft[0])
        return

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt).consume()
            load(session, MERGE_AIRCRAFT, aircraft)
            count = session.run("MATCH (ac:Aircraft) RETURN count(ac) AS n").single()["n"]
    print(f"Aircraft: {count} nodes in Neo4j")


if __name__ == "__main__":
    main()
