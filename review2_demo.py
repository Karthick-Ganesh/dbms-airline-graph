"""Review 2 walkthrough: database overview, CRUD cycle, advanced queries.

Prints numbered sections sized for terminal screenshots. The CRUD cycle only
touches its own test records (airport ZZZ, airline 9Z, flight 9Z101-20150131),
which it removes again. Usage:

    .venv/Scripts/python review2_demo.py            # everything
    .venv/Scripts/python review2_demo.py crud       # one part: overview | crud | queries | tx
    .venv/Scripts/python review2_demo.py queries 4  # a single query
"""

import sys

import crud
from queries import (OVERVIEW_NODES, OVERVIEW_RELS, QUERIES, disruption_demo,
                     project_active_network, run_query)
from seed import neo4j_driver

TEST_AIRPORT, TEST_AIRLINE, TEST_FLIGHT = "ZZZ", "9Z", "9Z101-20150131"


def heading(text):
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def step(text, result):
    print(f"\n-- {text}")
    if isinstance(result, dict):
        for k, v in result.items():
            print(f"   {k}: {v}")
    else:
        print(f"   {result}")


def table(rows, max_width=38):
    if not rows:
        print("   (no rows)")
        return
    cols = list(rows[0])

    def cell(v):
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        s = "" if v is None else str(v)
        return s if len(s) <= max_width else s[:max_width - 3] + "..."
    cells = [[cell(r[c]) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    print("   " + " | ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("   " + "-+-".join("-" * w for w in widths))
    for row in cells:
        print("   " + " | ".join(v.ljust(w) for v, w in zip(row, widths)))


def overview(driver):
    heading("DATABASE OVERVIEW")
    with driver.session() as s:
        table(crud.plain(s.run(OVERVIEW_NODES).data()))
        print()
        table(crud.plain(s.run(OVERVIEW_RELS).data()))
        constraints = s.run("SHOW CONSTRAINTS YIELD name, type RETURN type, count(*) AS n ORDER BY type").data()
        indexes = s.run("SHOW INDEXES YIELD name, type RETURN type, count(*) AS n ORDER BY type").data()
    print("\n   Constraints:", ", ".join(f"{r['type']} x{r['n']}" for r in constraints))
    print("   Indexes:    ", ", ".join(f"{r['type']} x{r['n']}" for r in indexes))


def crud_cycle(driver):
    # Leftovers from an interrupted earlier run would break the CREATEs.
    crud.delete_flight(driver, TEST_FLIGHT)
    crud.delete_airport(driver, TEST_AIRPORT)
    crud.delete_airline(driver, TEST_AIRLINE)

    heading("CRUD 1/4  NODES: Airport and Airline")
    step("CREATE airport ZZZ", crud.create_airport(
        driver, TEST_AIRPORT, "Demo Regional Airport", "Vellore", "IN", 12.9165, 79.1325,
        region="Asia", timezone="Asia/Kolkata", runway_count=1, is_hub=False, daily_capacity=100))
    step("CREATE airline 9Z", crud.create_airline(driver, TEST_AIRLINE, "Demo Air", "India", fleet_size=12))
    step("READ airport ZZZ", crud.get_airport(driver, TEST_AIRPORT))
    step("READ full-text search 'Vellore'", crud.search_airports(driver, "Vellore", limit=3))
    step("UPDATE airport ZZZ (runways, capacity)", crud.update_airport(driver, TEST_AIRPORT, runway_count=2, daily_capacity=150))
    step("UPDATE airline 9Z (fleet_size)", crud.update_airline(driver, TEST_AIRLINE, fleet_size=15))
    try:
        crud.create_airport(driver, TEST_AIRPORT, "Duplicate", "X", "IN", 0.0, 0.0)
    except Exception as e:  # unique constraint on airport_id
        step("CREATE duplicate airport ZZZ -> rejected by unique constraint", type(e).__name__)

    heading("CRUD 2/4  RELATIONSHIPS: ROUTE and HUB_OF")
    step("CREATE ROUTE ZZZ -> MAA", crud.create_route(
        driver, TEST_AIRPORT, "MAA", distance_km=127.0, avg_duration_min=55, co2_per_seat_kg=18.5,
        weight_cost=62.7, carriers=[TEST_AIRLINE], aircraft_types=["AT76"], fuel_burn_source="MANUAL"))
    step("CREATE ROUTE MAA -> ZZZ", crud.create_route(
        driver, "MAA", TEST_AIRPORT, distance_km=127.0, avg_duration_min=55, co2_per_seat_kg=18.5,
        weight_cost=62.7, carriers=[TEST_AIRLINE], aircraft_types=["AT76"], fuel_burn_source="MANUAL"))
    step("CREATE HUB_OF ZZZ -> 9Z", crud.add_hub(driver, TEST_AIRPORT, TEST_AIRLINE, since="2026-01-01"))
    step("READ ROUTE ZZZ -> MAA", crud.get_route(driver, TEST_AIRPORT, "MAA"))
    step("UPDATE ROUTE ZZZ -> MAA: corridor DISRUPTED", crud.set_corridor_status(driver, TEST_AIRPORT, "MAA", "DISRUPTED"))
    step("UPDATE ROUTE ZZZ -> MAA: back to ACTIVE", crud.set_corridor_status(driver, TEST_AIRPORT, "MAA", "ACTIVE"))

    heading("CRUD 3/4  FLIGHT (node + 4 relationships in one transaction)")
    step("CREATE flight 9Z101 ZZZ -> MAA", crud.create_flight(
        driver, TEST_FLIGHT, "9Z101", TEST_AIRLINE, TEST_AIRPORT, "MAA", "AT76",
        "2015-01-31T07:00:00+05:30", "2015-01-31T07:55:00+05:30", tail_number="VT-DMO",
        distance_km=127.0, duration_min=55, base_fare_usd=62.7, data_source="MANUAL"))
    step("READ flight with its airline, airports and aircraft", crud.get_flight(driver, TEST_FLIGHT))
    step("UPDATE record departure 07:25 (delay and status derived)", crud.record_departure(
        driver, TEST_FLIGHT, "2015-01-31T07:25:00+05:30"))
    step("UPDATE reassign aircraft (USES_AIRCRAFT replaced)", crud.reassign_aircraft(driver, TEST_FLIGHT, "DH8D", "VT-DMQ"))
    step("READ flights of airline 9Z", crud.list_flights(driver, airline_id=TEST_AIRLINE))

    heading("CRUD 4/4  DELETE (clean-up of every test record)")
    step("DELETE flight 9Z101 (DETACH DELETE)", crud.delete_flight(driver, TEST_FLIGHT))
    step("DELETE HUB_OF ZZZ -> 9Z", crud.remove_hub(driver, TEST_AIRPORT, TEST_AIRLINE))
    step("DELETE ROUTE ZZZ -> MAA", crud.delete_route(driver, TEST_AIRPORT, "MAA"))
    step("DELETE ROUTE MAA -> ZZZ", crud.delete_route(driver, "MAA", TEST_AIRPORT))
    step("SOFT DELETE airport ZZZ (operational_status = CLOSED)", crud.close_airport(driver, TEST_AIRPORT)["airport"]["operational_status"])
    step("HARD DELETE test airport ZZZ", crud.delete_airport(driver, TEST_AIRPORT))
    step("DELETE airline 9Z", crud.delete_airline(driver, TEST_AIRLINE))
    step("READ airport ZZZ after delete", crud.get_airport(driver, TEST_AIRPORT))


def run_queries(driver, only=None):
    heading("GDS in-memory projection of the ACTIVE network")
    step("gds.graph.project (Cypher aggregation, ACTIVE airports + corridors only)", project_active_network(driver))
    for q in QUERIES:
        if only and q.number != only:
            continue
        heading(f"QUERY {q.number}: {q.title}\n[{q.feature}]")
        table(run_query(driver, q))


def transaction_demo(driver):
    heading("TRANSACTIONS: simulate the closure of DXB, roll back a failing one, restore")
    out = disruption_demo(driver)
    print("\n-- BOM -> JFK before the closure")
    table(out["before"])
    print(f"\n-- Committed: DXB CLOSED, {out['disrupted_routes']} corridors DISRUPTED, "
          f"DisruptionEvent logged. BOM -> JFK re-routed:")
    table(out["after"])
    step("Failing transaction (close LHR + duplicate event_id) -> rolled back", out["rollback"])
    step("Restored DXB", out["restored"])


def main():
    part = sys.argv[1] if len(sys.argv) > 1 else "all"
    only = int(sys.argv[2]) if len(sys.argv) > 2 else None
    with neo4j_driver() as driver:
        driver.verify_connectivity()
        if part in ("all", "overview"):
            overview(driver)
        if part in ("all", "crud"):
            crud_cycle(driver)
        if part in ("all", "queries"):
            run_queries(driver, only)
        if part in ("all", "tx"):
            transaction_demo(driver)


if __name__ == "__main__":
    main()
