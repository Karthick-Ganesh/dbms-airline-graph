"""The project's analytical Cypher queries (Review 2 advanced features).

Each query is a Query(number, title, feature, cypher, params). run_query()
executes one and returns plain rows. The GDS queries run on an in-memory
projection of the *active* network only (ACTIVE airports joined by ACTIVE
ROUTE edges), so a simulated disruption changes their results. The
transaction demo is in disruption_demo().
"""

from dataclasses import dataclass, field

from neo4j.exceptions import Neo4jError

from crud import plain

ACTIVE_GRAPH = "active_network"


@dataclass
class Query:
    number: int
    title: str
    feature: str
    cypher: str
    params: dict = field(default_factory=dict)


def run_query(driver, q, **overrides):
    with driver.session() as session:
        return plain(session.run(q.cypher, {**q.params, **overrides}).data())


# ---------------------------------------------------------------- GDS projection

PROJECT_ACTIVE = """
MATCH (s:Airport {operational_status: 'ACTIVE'})-[r:ROUTE {corridor_status: 'ACTIVE'}]->
      (t:Airport {operational_status: 'ACTIVE'})
WITH gds.graph.project($graph, s, t, {
       relationshipProperties: r {.weight_time, .weight_carbon, .weight_cost, .distance_km}
     }) AS g
RETURN g.graphName AS graph, g.nodeCount AS nodes, g.relationshipCount AS relationships
"""


def project_active_network(driver, graph=ACTIVE_GRAPH):
    """(Re)build the in-memory GDS graph of the currently active network."""
    with driver.session() as session:
        session.run("CALL gds.graph.drop($graph, false) YIELD graphName RETURN graphName", graph=graph).consume()
        return session.run(PROJECT_ACTIVE, graph=graph).data()[0]


# ---------------------------------------------------------------- Queries

OVERVIEW_NODES = "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC"
OVERVIEW_RELS = "MATCH ()-[r]->() RETURN type(r) AS relationship, count(*) AS count ORDER BY count DESC"

QUERIES = [
    Query(1, "Direct routes from Delhi, longest first", "Graph traversal (1 hop) + relationship properties", """
MATCH (d:Airport {airport_id: $src})-[r:ROUTE {corridor_status: 'ACTIVE'}]->(x:Airport)
RETURN x.airport_id AS dest, x.city AS city, x.country AS country,
       r.distance_km AS km, r.avg_duration_min AS minutes,
       r.co2_per_seat_kg AS co2_per_seat_kg, r.carriers AS carriers
ORDER BY km DESC LIMIT 10
""", {"src": "DEL"}),

    Query(2, "Destinations reachable from Bengaluru with at most one stop", "Variable-length traversal [:ROUTE*1..2]", """
MATCH p = (b:Airport {airport_id: $src})-[:ROUTE*1..2]->(x:Airport)
WHERE x <> b AND all(r IN relationships(p) WHERE r.corridor_status = 'ACTIVE')
WITH x, min(length(p)) AS flights
RETURN x.region AS region,
       sum(CASE flights WHEN 1 THEN 1 ELSE 0 END) AS nonstop,
       sum(CASE flights WHEN 2 THEN 1 ELSE 0 END) AS one_stop,
       count(*) AS total
ORDER BY total DESC
""", {"src": "BLR"}),

    Query(3, "Fewest-connections itinerary from Leh to Lisbon", "Cypher shortestPath (unweighted hops)", """
MATCH (a:Airport {airport_id: $src}), (b:Airport {airport_id: $dst})
MATCH p = shortestPath((a)-[:ROUTE*..8]->(b))
WHERE all(r IN relationships(p) WHERE r.corridor_status = 'ACTIVE')
RETURN [n IN nodes(p) | n.airport_id + ' (' + n.city + ')'] AS itinerary,
       length(p) AS flights,
       round(reduce(km = 0.0, r IN relationships(p) | km + r.distance_km)) AS total_km
""", {"src": "IXL", "dst": "LIS"}),

    Query(4, "Fastest vs greenest route (GDS Dijkstra on two weights)", "GDS shortest path: gds.shortestPath.dijkstra", """
MATCH (s:Airport {airport_id: $src}), (t:Airport {airport_id: $dst})
UNWIND ['weight_time', 'weight_carbon'] AS weight
CALL gds.shortestPath.dijkstra.stream($graph, {
  sourceNode: s, targetNode: t, relationshipWeightProperty: weight
}) YIELD nodeIds
WITH weight, gds.util.asNodes(nodeIds) AS stops
UNWIND range(0, size(stops) - 2) AS i
WITH weight, stops, stops[i] AS a, stops[i + 1] AS b
MATCH (a)-[r:ROUTE]->(b)
WITH weight, stops, sum(r.avg_duration_min) AS flight_minutes,
     sum(r.co2_per_seat_kg) AS co2_per_seat_kg, sum(r.distance_km) AS km
RETURN CASE weight WHEN 'weight_time' THEN 'FASTEST' ELSE 'GREENEST' END AS optimised_for,
       [n IN stops | n.airport_id] AS path, flight_minutes,
       round(co2_per_seat_kg, 1) AS co2_per_seat_kg, round(km) AS km
""", {"src": "DEL", "dst": "JFK", "graph": ACTIVE_GRAPH}),

    Query(5, "Most critical transfer hubs (single points of failure)", "GDS Betweenness centrality", """
CALL gds.betweenness.stream($graph, {samplingSize: 1000, samplingSeed: 42})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS a, score
RETURN a.airport_id AS airport, a.city AS city, a.country AS country, round(score) AS betweenness
ORDER BY betweenness DESC LIMIT 10
""", {"graph": ACTIVE_GRAPH}),

    Query(6, "Most influential airports", "GDS PageRank + degree centrality", """
CALL gds.pageRank.stream($graph, {maxIterations: 20, dampingFactor: 0.85})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS a, score
ORDER BY score DESC LIMIT 10
RETURN a.airport_id AS airport, a.city AS city, round(score, 2) AS pagerank,
       COUNT { (a)-[:ROUTE {corridor_status: 'ACTIVE'}]->() } AS out_degree,
       COUNT { (a)<-[:ROUTE {corridor_status: 'ACTIVE'}]-() } AS in_degree
ORDER BY pagerank DESC
""", {"graph": ACTIVE_GRAPH}),

    Query(7, "Airline on-time performance", "Aggregation across Airline-OPERATES-Flight", """
MATCH (al:Airline)-[:OPERATES]->(f:Flight)
WITH al, f.data_source AS source, count(f) AS flights,
     avg(f.delay_minutes) AS avg_delay,
     sum(CASE f.status WHEN 'CANCELLED' THEN 1 ELSE 0 END) AS cancelled,
     sum(CASE f.status WHEN 'DELAYED' THEN 1 ELSE 0 END) AS delayed
WHERE flights >= $min_flights
RETURN al.airline_id AS airline, al.name AS name, source, flights,
       round(avg_delay, 1) AS avg_delay_min,
       round(100.0 * delayed / flights, 1) AS delayed_pct,
       round(100.0 * cancelled / flights, 1) AS cancelled_pct
ORDER BY cancelled_pct DESC, delayed_pct DESC LIMIT 12
""", {"min_flights": 10}),

    Query(8, "Impact of Winter Storm Juno on departures", "Multi-hop pattern + temporal filter + aggregation", """
MATCH (e:DisruptionEvent {event_id: $event})-[:AFFECTS]->(a:Airport)<-[:DEPARTS_FROM]-(f:Flight)
WHERE f.scheduled_departure >= e.start_time AND f.scheduled_departure <= e.end_time
RETURN a.airport_id AS airport, count(f) AS departures_in_window,
       sum(CASE f.status WHEN 'CANCELLED' THEN 1 ELSE 0 END) AS cancelled,
       round(avg(f.delay_minutes), 1) AS avg_delay_min_of_operated
ORDER BY cancelled DESC
""", {"event": "EVT-2015-0126-WEATHER"}),

    Query(9, "Airports within 300 km of Chennai", "Spatial query (point.distance, point index)", """
MATCH (c:Airport {airport_id: $center})
WITH c.location AS here
MATCH (a:Airport)
WHERE point.distance(a.location, here) < $metres AND a.location <> here
RETURN a.airport_id AS airport, a.name AS name, a.city AS city,
       round(point.distance(a.location, here) / 1000) AS km,
       COUNT { (a)-[:ROUTE]->() } AS routes
ORDER BY km LIMIT 10
""", {"center": "MAA", "metres": 300000}),

    Query(10, "Carbon efficiency by aircraft category", "Aggregation with the CO2 constant applied in Cypher", """
MATCH (ac:Aircraft) WHERE ac.seating_capacity > 0
WITH ac.category AS category, count(*) AS types,
     avg(ac.avg_fuel_burn_kg_per_km * 3.16 / ac.seating_capacity * 1000) AS g_co2_per_seat_km
RETURN category, types, round(g_co2_per_seat_km, 1) AS g_co2_per_seat_km
ORDER BY g_co2_per_seat_km
"""),

    Query(11, "Engines of the aircraft flying Delhi-Mumbai, most efficient first", "4-label traversal: ROUTE -> Aircraft -POWERED_BY-> Engine", """
MATCH (:Airport {airport_id: $src})-[r:ROUTE]->(:Airport {airport_id: $dst})
UNWIND r.aircraft_types AS type_id
MATCH (ac:Aircraft {aircraft_type_id: type_id})-[p:POWERED_BY]->(e:Engine)
RETURN ac.model AS aircraft, e.engine_model AS engine, e.manufacturer AS maker,
       round(e.bypass_ratio, 2) AS bypass_ratio, round(e.fuel_lto_cycle_kg) AS lto_fuel_kg,
       p.is_default_engine AS default_engine
ORDER BY bypass_ratio DESC
""", {"src": "DEL", "dst": "BOM"}),

    Query(12, "Hub dependency: airlines whose network relies most on one hub", "Pattern comprehension + aggregation over HUB_OF and ROUTE", """
MATCH ()-[r:ROUTE]->()
UNWIND r.carriers AS carrier
WITH carrier, count(*) AS all_routes
WHERE all_routes >= $min_routes
MATCH (hub:Airport)-[:HUB_OF]->(al:Airline {airline_id: carrier})
WITH al, all_routes, hub,
     COUNT { (hub)-[h:ROUTE]->() WHERE al.airline_id IN h.carriers } AS hub_routes
WITH al, all_routes, collect(hub.airport_id) AS hubs, sum(hub_routes) AS via_hubs
RETURN al.airline_id AS airline, al.name AS name, all_routes, hubs,
       round(100.0 * via_hubs / all_routes, 1) AS pct_routes_from_hubs
ORDER BY pct_routes_from_hubs DESC LIMIT 10
""", {"min_routes": 100}),
]


# ---------------------------------------------------------------- Transaction demo

DEMO_EVENT_ID = "EVT-DEMO-DXB-CLOSURE"


def _close_airport(tx, code, event_id):
    """Soft-close an airport and its corridors, and log the event: one unit of work."""
    tx.run("MATCH (a:Airport {airport_id: $code}) SET a.operational_status = 'CLOSED'", code=code)
    tx.run("""
        MATCH (:Airport {airport_id: $code})-[r:ROUTE]-()
        WHERE r.corridor_status = 'ACTIVE'
        SET r.corridor_status = 'DISRUPTED', r.disrupted_by = $event_id
        """, code=code, event_id=event_id)


def disruption_demo(driver, code="DXB", src="BOM", dst="JFK"):
    """Explicit transactions: commit a closure, show a rollback, then restore.

    Returns a dict of before/after results for printing.
    """
    out = {"before": None, "after": None, "rollback": None, "restored": None}
    route_q = next(q for q in QUERIES if q.number == 4)

    project_active_network(driver)
    out["before"] = run_query(driver, route_q, src=src, dst=dst)

    with driver.session() as session:
        # 1. Committed transaction: closure + audit event together.
        with session.begin_transaction() as tx:
            _close_airport(tx, code, DEMO_EVENT_ID)
            tx.run("""
                MERGE (e:DisruptionEvent {event_id: $event_id})
                SET e.event_type = 'OTHER', e.severity = 'CRITICAL', e.data_source = 'MANUAL',
                    e.description = 'Demo: simulated closure of ' + $code,
                    e.start_time = datetime(), e.end_time = null
                WITH e MATCH (a:Airport {airport_id: $code}) MERGE (e)-[:AFFECTS]->(a)
                """, event_id=DEMO_EVENT_ID, code=code)
            tx.commit()
        disrupted = session.run("MATCH ()-[r:ROUTE {disrupted_by: $id}]-() RETURN count(DISTINCT r) AS n",
                                id=DEMO_EVENT_ID).single()["n"]

    project_active_network(driver)
    out["after"] = run_query(driver, route_q, src=src, dst=dst)
    out["disrupted_routes"] = disrupted

    with driver.session() as session:
        # 2. Failing transaction: the second statement violates the unique
        #    constraint on event_id, so the closure of LHR is rolled back too.
        try:
            with session.begin_transaction() as tx:
                _close_airport(tx, "LHR", "EVT-2015-0126-WEATHER")
                tx.run("CREATE (:DisruptionEvent {event_id: 'EVT-2015-0126-WEATHER'})")
                tx.commit()
        except Neo4jError as e:
            out["rollback"] = {
                "error": e.code,
                "LHR status after rollback": session.run(
                    "MATCH (a:Airport {airport_id: 'LHR'}) RETURN a.operational_status AS s").single()["s"],
            }

        # 3. Restore: reopen the airport and exactly the corridors this event disrupted.
        with session.begin_transaction() as tx:
            tx.run("MATCH (a:Airport {airport_id: $code}) SET a.operational_status = 'ACTIVE'", code=code)
            tx.run("""
                MATCH ()-[r:ROUTE {disrupted_by: $id}]-()
                SET r.corridor_status = 'ACTIVE' REMOVE r.disrupted_by
                """, id=DEMO_EVENT_ID)
            tx.run("MATCH (e:DisruptionEvent {event_id: $id}) SET e.end_time = datetime()", id=DEMO_EVENT_ID)
            tx.commit()
        out["restored"] = session.run("""
            MATCH (a:Airport {airport_id: $code})
            RETURN a.operational_status AS status,
                   COUNT { (a)-[:ROUTE {corridor_status: 'ACTIVE'}]-() } AS active_routes,
                   COUNT { ()-[:ROUTE {disrupted_by: $id}]-() } AS still_disrupted
            """, code=code, id=DEMO_EVENT_ID).single().data()

    project_active_network(driver)
    return out
