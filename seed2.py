"""Seed :Aircraft nodes from commercial_aircraft_dataset.csv.

Uses the same Neo4j connection settings as seed.py (NEO4J_URI, NEO4J_USER,
NEO4J_PASSWORD). Creates nodes only; POWERED_BY is loaded separately.

CO2 is not stored on the node: it's derived in Cypher as
avg_fuel_burn_kg_per_km * 3.16 (kg CO2 per kg jet fuel).

The dataset's "Max Capacity" is the certified maximum (all-economy) seating,
kept as seating_capacity. Airlines fly fewer seats, so typical_seats applies a
layout factor per category; per-seat CO2 uses typical_seats. Fuel burn scales
with aircraft size: a seeded per-seat efficiency (kg fuel per seat-km, close
to published averages) times typical_seats, so an A380 burns ~13 kg/km and a
787-9 ~6, as in reality.
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

# Typical airline layout as a share of the certified maximum seating
# (e.g. A380 853 -> ~550, 777-300ER 550 -> ~360, A320 180 -> ~160).
TYPICAL_LAYOUT_FACTOR = {
    "Widebody": 0.65,
    "Narrowbody": 0.90,
    "Regional": 0.92,
    "Turboprop": 0.92,
    "Private Jet": 0.80,
    "Supersonic": 0.78,
    "Freighter": 1.0,
}
# Synthetic cruise fuel efficiency per category, kg fuel per seat-km (uniform
# draw); avg_fuel_burn_kg_per_km = draw x typical_seats. Jets land at roughly
# 55-90 g CO2 per seat-km, in line with published airline averages.
FUEL_PER_SEAT_KM_RANGE = {
    "Widebody": (0.019, 0.028),
    "Narrowbody": (0.017, 0.024),
    "Regional": (0.026, 0.036),
    "Turboprop": (0.019, 0.026),
    "Private Jet": (0.10, 0.25),
    "Supersonic": (0.12, 0.16),
}
# Freighters have no seats, so their burn is drawn per km directly.
FREIGHTER_FUEL_KG_PER_KM = (8.0, 12.0)
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

    unknown = set(df["category"]) - set(TYPICAL_LAYOUT_FACTOR)
    if unknown:
        raise ValueError(f"no layout factor / fuel range for categories: {sorted(unknown)}")

    # Variants can share an ICAO type code (A321, A321LR, A321XLR are all
    # "A321"); keep the base model, which is listed first.
    df = df.drop_duplicates("aircraft_type_id", keep="first")

    df["typical_seats"] = [round(seats * TYPICAL_LAYOUT_FACTOR[c])
                           for seats, c in zip(df["seating_capacity"], df["category"])]
    rng = random.Random(FUEL_BURN_SEED)
    df["avg_fuel_burn_kg_per_km"] = [
        round(rng.uniform(*FREIGHTER_FUEL_KG_PER_KM) if c not in FUEL_PER_SEAT_KM_RANGE or seats == 0
              else rng.uniform(*FUEL_PER_SEAT_KM_RANGE[c]) * seats, 2)
        for seats, c in zip(df["typical_seats"], df["category"])
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
