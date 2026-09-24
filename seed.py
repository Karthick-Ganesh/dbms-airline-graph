"""Seed the Neo4j airline graph.

Connection settings come from the environment:
    NEO4J_URI       (default bolt://localhost:7687)
    NEO4J_USER      (default neo4j)
    NEO4J_PASSWORD  (required unless --dry-run)
"""

import argparse
import math
import os
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

BATCH_SIZE = 500

CONSTRAINTS = [
    "CREATE CONSTRAINT engine_uid IF NOT EXISTS "
    "FOR (e:Engine) REQUIRE e.engine_uid IS UNIQUE",
]

MERGE_ENGINES = """
UNWIND $rows AS row
MERGE (e:Engine {engine_uid: row.engine_uid})
SET e += row
"""


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
        {k: v for k, v in rec.items() if not (isinstance(v, float) and math.isnan(v))}
        for rec in df.to_dict("records")
    ]


def load_engines(session, engines):
    for i in range(0, len(engines), BATCH_SIZE):
        session.execute_write(
            lambda tx, rows: tx.run(MERGE_ENGINES, rows=rows).consume(),
            engines[i:i + BATCH_SIZE],
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="parse source data and report, without touching Neo4j")
    args = parser.parse_args()

    engines = read_engines()
    print(f"Engine: {len(engines)} rows read from '{ICAO_EDB_SHEET}'")

    if args.dry_run:
        print("sample:", engines[0])
        return

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        parser.error("set NEO4J_PASSWORD (or pass --dry-run)")

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt).consume()
            load_engines(session, engines)
            count = session.run("MATCH (e:Engine) RETURN count(e) AS n").single()["n"]
    print(f"Engine: {count} nodes in Neo4j")


if __name__ == "__main__":
    main()
