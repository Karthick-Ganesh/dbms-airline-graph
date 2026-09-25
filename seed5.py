"""Seed POWERED_BY relationships, plus the schema's indexes and constraints.

POWERED_BY: no dataset links aircraft types to engines, so AIRCRAFT_ENGINES
below is a hand-written table (from type-certificate data) of each type's
engine options, default first. Each option is an engine-model prefix matched
against the ICAO databank's "Engine Identification"; the best matching
:Engine record is linked. The databank only covers turbofans, so turboprop
types (ATR, Dash 8, Caravan, ...) have no engine and are left unlinked.

Run after seed.py and seed2.py; uses the same NEO4J_* settings (env or .env).
"""

import argparse

import pandas as pd

from seed import ICAO_EDB_FILE, ICAO_EDB_SHEET, load, neo4j_driver
from seed2 import read_aircraft

# aircraft_type_id -> engine-model prefixes, default engine first.
AIRCRAFT_ENGINES = {
    # Airbus
    "BCS1": ["PW1519G"], "BCS3": ["PW1524G"],
    "A318": ["CFM56-5B9"],
    "A319": ["CFM56-5B5", "V2524-A5"], "A19N": ["LEAP-1A24", "PW1124G"],
    "A320": ["CFM56-5B4", "V2527-A5"], "A20N": ["LEAP-1A26", "PW1127G"],
    "A321": ["CFM56-5B3", "V2533-A5"], "A21N": ["LEAP-1A35", "PW1133G"],
    "A332": ["Trent 772", "CF6-80E1A4", "PW4168A"],
    "A333": ["Trent 772", "CF6-80E1A4", "PW4168A"],
    "A338": ["Trent 7000"], "A339": ["Trent 7000"],
    "A343": ["CFM56-5C4"], "A346": ["Trent 556"],
    "A359": ["Trent XWB-84"], "A35K": ["Trent XWB-97"],
    "A388": ["Trent 970", "GP7270"],
    "A306": ["CF6-80C2A5", "PW4158"], "A310": ["CF6-80C2A2", "PW4152"],
    # Boeing
    "B712": ["BR700-715"],
    "B732": ["JT8D-9", "JT8D-15"], "B722": ["JT8D-15", "JT8D-9"],
    "B733": ["CFM56-3"], "B734": ["CFM56-3"], "B735": ["CFM56-3"],
    "B736": ["CFM56-7B20"], "B737": ["CFM56-7B24"], "B738": ["CFM56-7B26"], "B739": ["CFM56-7B26"],
    "B37M": ["LEAP-1B25"], "B38M": ["LEAP-1B27"], "B39M": ["LEAP-1B28"], "B3XM": ["LEAP-1B28"],
    "B752": ["RB211-535E4", "PW2037"], "B753": ["RB211-535E4", "PW2043"],
    "B763": ["CF6-80C2B6", "PW4060", "RB211-524H"], "B764": ["CF6-80C2B8"],
    "B772": ["GE90-94B", "PW4090", "Trent 892"], "B77L": ["GE90-110B"], "B77W": ["GE90-115B"],
    "B778": ["GE9X"], "B779": ["GE9X"],
    "B788": ["GEnx-1B64", "Trent 1000"], "B789": ["GEnx-1B74", "Trent 1000"], "B78X": ["GEnx-1B76", "Trent 1000"],
    "B744": ["CF6-80C2B1F", "PW4056", "RB211-524G"], "B748": ["GEnx-2B67"],
    # Embraer / Bombardier / Comac / Sukhoi
    "E135": ["AE3007A"], "E140": ["AE3007A"], "E145": ["AE3007A"],
    "E170": ["CF34-8E"], "E75L": ["CF34-8E5"], "E190": ["CF34-10E"], "E195": ["CF34-10E"],
    "E752": ["PW1715G"], "E902": ["PW1919G"], "E952": ["PW1923G"],
    "CRJ1": ["CF34-3"], "CRJ2": ["CF34-3"], "CRJ7": ["CF34-8C1"], "CRJ9": ["CF34-8C5"], "CRJX": ["CF34-8C5"],
    "ARJ2": ["CF34-10A"], "C919": ["LEAP-1C"], "SU95": ["SaM146"],
    # McDonnell Douglas / Fokker / Dornier / others
    "MD82": ["JT8D-217"], "MD83": ["JT8D-219"], "MD87": ["JT8D-217C"], "MD88": ["JT8D-217C", "JT8D-219"],
    "MD90": ["V2525-D5"], "DC93": ["JT8D-9"], "DC10": ["CF6-50C"], "MD11": ["CF6-80C2D1F", "PW4460"],
    "F70": ["TAY 620"], "F100": ["TAY 650"], "J328": ["PW306B"],
    "A148": ["D-436-148"], "A158": ["D-436-148"], "A124": ["D-18T"], "A225": ["D-18T"],
    "IL96": ["PS-90A"], "T204": ["PS-90A", "RB211-535E4"],
    "B462": ["ALF 502R-5"], "RJ85": ["LF507"], "RJ1H": ["LF507"],
    "L101": ["RB211-22B", "RB211-524B"], "CONC": ["Olympus 593"],
    "C750": ["AE3007C"], "GLF6": ["BR700-725"],
}

CONSTRAINTS = [
    # Property existence and type constraints (Enterprise Edition).
    "CREATE CONSTRAINT airport_name_exists IF NOT EXISTS FOR (a:Airport) REQUIRE a.name IS NOT NULL",
    "CREATE CONSTRAINT airport_location_type IF NOT EXISTS FOR (a:Airport) REQUIRE a.location IS :: POINT",
    "CREATE CONSTRAINT flight_status_exists IF NOT EXISTS FOR (f:Flight) REQUIRE f.status IS NOT NULL",
    "CREATE CONSTRAINT flight_departure_type IF NOT EXISTS FOR (f:Flight) REQUIRE f.scheduled_departure IS :: ZONED DATETIME",
    "CREATE CONSTRAINT route_weight_time_type IF NOT EXISTS FOR ()-[r:ROUTE]-() REQUIRE r.weight_time IS :: FLOAT",
    "CREATE CONSTRAINT route_weight_carbon_type IF NOT EXISTS FOR ()-[r:ROUTE]-() REQUIRE r.weight_carbon IS :: FLOAT",
]

INDEXES = [
    "CREATE INDEX airport_region IF NOT EXISTS FOR (a:Airport) ON (a.region)",
    "CREATE INDEX airport_status IF NOT EXISTS FOR (a:Airport) ON (a.operational_status)",
    "CREATE INDEX airport_country IF NOT EXISTS FOR (a:Airport) ON (a.country)",
    "CREATE POINT INDEX airport_location IF NOT EXISTS FOR (a:Airport) ON (a.location)",
    "CREATE FULLTEXT INDEX airport_search IF NOT EXISTS FOR (a:Airport) ON EACH [a.name, a.city, a.airport_id]",
    "CREATE INDEX flight_status IF NOT EXISTS FOR (f:Flight) ON (f.status)",
    "CREATE INDEX flight_departure IF NOT EXISTS FOR (f:Flight) ON (f.scheduled_departure)",
    "CREATE INDEX flight_data_source IF NOT EXISTS FOR (f:Flight) ON (f.data_source)",
    "CREATE INDEX route_status IF NOT EXISTS FOR ()-[r:ROUTE]-() ON (r.corridor_status)",
    "CREATE INDEX event_start IF NOT EXISTS FOR (e:DisruptionEvent) ON (e.start_time)",
]

MERGE_POWERED_BY = """
UNWIND $rows AS row
MATCH (ac:Aircraft {aircraft_type_id: row.aircraft_type_id})
MATCH (e:Engine {engine_uid: row.engine_uid})
MERGE (ac)-[p:POWERED_BY]->(e)
SET p.is_default_engine = row.is_default_engine
"""


def read_engine_records(path=ICAO_EDB_FILE):
    df = pd.read_excel(path, sheet_name=ICAO_EDB_SHEET)
    df.columns = df.columns.str.strip()
    return pd.DataFrame({
        "engine_uid": df["UID No"].astype(str).str.strip(),
        "engine_model": df["Engine Identification"].astype(str).str.strip(),
        "superseded": df["Data Superseded"].astype(str).str.strip().str.lower().eq("yes"),
    })


def pick_engine(records, prefix):
    """Best databank record for an engine-model prefix, or None.

    Prefers an exact name, then a name continuing with a separator
    ("LEAP-1A26/26E1" over the corporate-jet "LEAP-1A26CJ"), then records
    that aren't superseded.
    """
    names = records["engine_model"].str.upper()
    p = prefix.upper()
    matches = records[names.str.startswith(p)]
    if matches.empty:
        return None
    rest = matches["engine_model"].str.upper().str[len(p):]
    fit = rest.map(lambda s: 0 if s == "" else 1 if not s[0].isalnum() else 2)
    return matches.assign(_fit=fit).sort_values(["_fit", "superseded", "engine_uid"]).iloc[0]


def build_powered_by(aircraft_ids, records):
    """Return (POWERED_BY rows, {aircraft id: unmatched prefixes}, turboprop ids)."""
    rows, unmatched = [], {}
    for ac in sorted(aircraft_ids):
        for i, prefix in enumerate(AIRCRAFT_ENGINES.get(ac, [])):
            rec = pick_engine(records, prefix)
            if rec is None:
                unmatched.setdefault(ac, []).append(prefix)
                continue
            rows.append({"aircraft_type_id": ac, "engine_uid": rec["engine_uid"],
                         "engine_model": rec["engine_model"], "is_default_engine": i == 0})
    # Default = first option that actually matched.
    seen = set()
    for r in rows:
        r["is_default_engine"] = r["aircraft_type_id"] not in seen
        seen.add(r["aircraft_type_id"])
    no_entry = sorted(set(aircraft_ids) - set(AIRCRAFT_ENGINES))
    return rows, unmatched, no_entry


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="parse source data and report, without touching Neo4j")
    args = parser.parse_args()

    aircraft = {r["aircraft_type_id"]: r for r in read_aircraft()}
    rows, unmatched, no_entry = build_powered_by(aircraft.keys(), read_engine_records())
    linked = {r["aircraft_type_id"] for r in rows}
    print(f"POWERED_BY: {len(rows)} relationships for {len(linked)} of {len(aircraft)} aircraft types")
    print(f"  no turbofan in the ICAO databank (turboprops, left unlinked): {', '.join(no_entry)}")
    print(f"  engine not in the databank: "
          + ", ".join(f"{ac} ({'/'.join(p)})" for ac, p in unmatched.items()))

    if args.dry_run:
        for ac in ("A320", "A20N", "B77W", "B789", "A388", "E190"):
            print(f"  {ac}:", [(r["engine_model"], r["engine_uid"], "default" if r["is_default_engine"] else "")
                              for r in rows if r["aircraft_type_id"] == ac])
        return

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            for stmt in CONSTRAINTS + INDEXES:
                session.run(stmt).consume()
            load(session, MERGE_POWERED_BY, [{k: r[k] for k in ("aircraft_type_id", "engine_uid", "is_default_engine")}
                                             for r in rows])
            n = session.run("MATCH ()-[p:POWERED_BY]->() RETURN count(p) AS n").single()["n"]
            n_constraints = len(session.run("SHOW CONSTRAINTS").data())
            n_indexes = len(session.run("SHOW INDEXES").data())
    print(f"POWERED_BY: {n} relationships in Neo4j")
    print(f"Schema: {n_constraints} constraints, {n_indexes} indexes")


if __name__ == "__main__":
    main()
