"""The project's analytical Cypher queries, route search and disruptions.

Each query is a Query(number, title, feature, cypher, params). run_query()
executes one and returns plain rows. The GDS queries run on an in-memory
projection of the *active* network only (ACTIVE airports joined by ACTIVE
ROUTE edges), so a simulated disruption changes their results.

Also used by the web app (app.py): find_routes() for the route finder,
simulate_disruption() / restore_disruption() for the disruption simulator
(disruption_demo() is the Review 2 transaction demo built on them), and the
*_graph() functions, which return {nodes, edges} for the graph explorer.
"""

import statistics
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    params = {**q.params, **overrides}
    if "graph" not in params:
        with driver.session() as session:
            return plain(session.run(q.cypher, params).data())
    # GDS query: hold the lock so the projection can't be rebuilt mid-query.
    with _projection_lock:
        ensure_projection(driver, params["graph"])
        with driver.session() as session:
            return plain(session.run(q.cypher, params).data())


# ---------------------------------------------------------------- GDS projection

PROJECT_ACTIVE = """
MATCH (s:Airport {operational_status: 'ACTIVE'})-[r:ROUTE {corridor_status: 'ACTIVE'}]->
      (t:Airport {operational_status: 'ACTIVE'})
WITH gds.graph.project($graph, s, t, {
       sourceNodeProperties: {latitude: s.location.latitude, longitude: s.location.longitude},
       targetNodeProperties: {latitude: t.location.latitude, longitude: t.location.longitude},
       relationshipProperties: r {.weight_time, .weight_carbon, .weight_cost, .distance_km,
                                  weight_trip: r.weight_time + $connection_min}
     }) AS g
RETURN g.graphName AS graph, g.nodeCount AS nodes, g.relationshipCount AS relationships
"""

# Minimum connection time per stop. weight_trip = weight_time + this, so a
# path's total is flight time + one connection per leg; journey time is that
# minus one connection. Without it a one-stop itinerary can "beat" a nonstop.
CONNECTION_MIN = 90

# One projection is shared by every web request; this lock keeps a rebuild
# from running while another request is querying it.
_projection_lock = threading.RLock()


def project_active_network(driver, graph=ACTIVE_GRAPH):
    """(Re)build the in-memory GDS graph of the currently active network."""
    with _projection_lock, driver.session() as session:
        session.run("CALL gds.graph.drop($graph, false) YIELD graphName RETURN graphName", graph=graph).consume()
        return session.run(PROJECT_ACTIVE, graph=graph, connection_min=float(CONNECTION_MIN)).data()[0]


def ensure_projection(driver, graph=ACTIVE_GRAPH):
    """Project the active network only if it isn't in memory yet."""
    with _projection_lock:
        with driver.session() as session:
            exists = session.run("CALL gds.graph.exists($graph) YIELD exists RETURN exists",
                                 graph=graph).single()["exists"]
        if not exists:
            return project_active_network(driver, graph)


def invalidate_projection(driver, graph=ACTIVE_GRAPH):
    """Drop the projection after a network change; the next GDS query rebuilds
    it once (ensure_projection), instead of re-projecting after every write."""
    with _projection_lock, driver.session() as session:
        session.run("CALL gds.graph.drop($graph, false) YIELD graphName RETURN graphName", graph=graph).consume()


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
MATCH (ac:Aircraft) WHERE ac.typical_seats > 0
WITH ac.category AS category, count(*) AS types,
     avg(ac.avg_fuel_burn_kg_per_km * 3.16 / ac.typical_seats * 1000) AS g_co2_per_seat_km
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

    Query(13, "Shortest-distance route from Delhi to New York", "GDS A* (haversine heuristic on Airport coordinates)", """
MATCH (s:Airport {airport_id: $src}), (t:Airport {airport_id: $dst})
CALL gds.shortestPath.astar.stream($graph, {
  sourceNode: s, targetNode: t, relationshipWeightProperty: 'distance_km',
  latitudeProperty: 'latitude', longitudeProperty: 'longitude'
}) YIELD nodeIds, totalCost
RETURN [n IN gds.util.asNodes(nodeIds) | n.airport_id] AS path,
       size(nodeIds) - 1 AS flights, round(totalCost) AS km
""", {"src": "DEL", "dst": "JFK", "graph": ACTIVE_GRAPH}),

    Query(14, "Top 3 fastest itineraries from Delhi to New York", "GDS Yen's k-shortest paths (route recommendation)", """
MATCH (s:Airport {airport_id: $src}), (t:Airport {airport_id: $dst})
CALL gds.shortestPath.yens.stream($graph, {
  sourceNode: s, targetNode: t, k: $k, relationshipWeightProperty: 'weight_trip'
}) YIELD index, nodeIds, totalCost
RETURN index + 1 AS rank, [n IN gds.util.asNodes(nodeIds) | n.airport_id] AS path,
       size(nodeIds) - 1 AS flights, round(totalCost - $connection_min) AS journey_minutes
ORDER BY rank
""", {"src": "DEL", "dst": "JFK", "k": 3, "connection_min": CONNECTION_MIN, "graph": ACTIVE_GRAPH}),
]


# ---------------------------------------------------------------- Route finder

PATH_DIJKSTRA = """
MATCH (s:Airport {airport_id: $src}), (t:Airport {airport_id: $dst})
CALL gds.shortestPath.dijkstra.stream($graph, {
  sourceNode: s, targetNode: t, relationshipWeightProperty: $weight
}) YIELD nodeIds
RETURN 0 AS index, [n IN gds.util.asNodes(nodeIds) | n.airport_id] AS path
"""

# Each leg of a path: the active ROUTE edge between consecutive stops.
PATH_LEGS = """
UNWIND range(0, size($path) - 2) AS i
MATCH (a:Airport {airport_id: $path[i]}), (b:Airport {airport_id: $path[i + 1]})
CALL (a, b) {
  MATCH (a)-[r:ROUTE {corridor_status: 'ACTIVE'}]->(b)
  RETURN r ORDER BY r.weight_time LIMIT 1
}
RETURN a.airport_id AS from, b.airport_id AS to, r.avg_duration_min AS minutes,
       r.distance_km AS km, r.co2_per_seat_kg AS co2_per_seat_kg, r.carriers AS carriers
ORDER BY i
"""

PATH_STOPS = """
UNWIND $codes AS code
MATCH (a:Airport {airport_id: code})
RETURN a.airport_id AS code, a.name AS name, a.city AS city, a.country AS country,
       a.location AS location, a.operational_status AS status
"""

# Bookable itineraries: every nonstop, 1-stop and 2-stop path between two
# airports over ACTIVE corridors, found exactly by Cypher pattern matching.
# GDS Dijkstra can't limit the number of stops (it happily chains 3-5 legs for
# a few kg of CO2), so the route finder enumerates instead: with at most two
# stops the search space is small. Stops must stay within DETOUR x the direct
# distance, which prunes hubs on the far side of the world.
DETOUR = 2.0
MAX_EXTRA_MIN = 600   # never more than 10 h longer than the fastest

_LEG = "ROUTE {corridor_status: 'ACTIVE'}"
_NEAR = ("{m}.operational_status = 'ACTIVE' AND "
         "point.distance({m}.location, s.location) + point.distance({m}.location, t.location) <= budget")
ITINERARY_PATTERNS = [
    f"MATCH (s)-[r1:{_LEG}]->(t) RETURN [s.airport_id, t.airport_id] AS path, [r1] AS rs",
    f"""MATCH (s)-[r1:{_LEG}]->(m1:Airport)-[r2:{_LEG}]->(t)
    WHERE {_NEAR.format(m="m1")}
    RETURN [s.airport_id, m1.airport_id, t.airport_id] AS path, [r1, r2] AS rs""",
    f"""MATCH (s)-[r1:{_LEG}]->(m1:Airport)-[r2:{_LEG}]->(m2:Airport)-[r3:{_LEG}]->(t)
    WHERE m1 <> t AND m2 <> s AND {_NEAR.format(m="m1")} AND {_NEAR.format(m="m2")}
    RETURN [s.airport_id, m1.airport_id, m2.airport_id, t.airport_id] AS path, [r1, r2, r3] AS rs""",
]

ITINERARIES = """
MATCH (s:Airport {{airport_id: $src}}), (t:Airport {{airport_id: $dst}})
WITH s, t, $detour * point.distance(s.location, t.location) AS budget
CALL (s, t, budget) {{
  {patterns}
}}
RETURN path, size(rs) AS legs,
       reduce(m = 0, r IN rs | m + r.avg_duration_min) + $connection * (size(rs) - 1) AS journey,
       reduce(c = 0.0, r IN rs | c + r.co2_per_seat_kg) AS co2,
       reduce(k = 0.0, r IN rs | k + r.distance_km) AS km
"""


def extra_time_allowed(fastest_min):
    """How much longer than the fastest a greener/shorter itinerary may be:
    75% of the fastest journey under 15 h, 50% from 15 h, never over 10 h."""
    share = 0.75 if fastest_min < 15 * 60 else 0.5
    return min(MAX_EXTRA_MIN, round(share * fastest_min))


def _option(session, key, label, algorithm, path):
    legs = session.run(PATH_LEGS, path=path).data()
    return {
        "key": key, "label": label, "algorithm": algorithm, "path": path, "legs": legs,
        "flights": len(legs),
        "minutes": sum(leg["minutes"] or 0 for leg in legs),
        "journey_minutes": sum(leg["minutes"] or 0 for leg in legs) + CONNECTION_MIN * max(len(legs) - 1, 0),
        "km": round(sum(leg["km"] or 0 for leg in legs)),
        "co2_per_seat_kg": round(sum(leg["co2_per_seat_kg"] or 0 for leg in legs), 1),
    }


def find_routes(driver, src, dst, k=3, max_stops=2, max_extra_min=None, graph=ACTIVE_GRAPH):
    """Bookable fastest, greenest and shortest itineraries, plus k alternatives.

    Only itineraries with at most `max_stops` stops count, and the greenest and
    shortest may be at most `max_extra_min` longer than the fastest (default:
    extra_time_allowed()). Alternatives are the next-fastest bookable ones.
    "unconstrained" is GDS Dijkstra's greenest path over the whole network,
    reported when it differs (usually more stops or a far longer trip).
    """
    max_stops = max(0, min(2, max_stops))
    cypher = ITINERARIES.format(patterns="\n  UNION ALL\n  ".join(ITINERARY_PATTERNS[:max_stops + 1]))
    options, alternatives = [], []
    with _projection_lock:
        ensure_projection(driver, graph)
        with driver.session() as session:
            found = session.run(cypher, src=src, dst=dst, detour=DETOUR,
                                connection=CONNECTION_MIN).data()
            limits = {"max_stops": max_stops, "candidates": len(found)}
            if found:
                fastest = min(found, key=lambda c: (c["journey"], c["legs"], c["co2"]))
                extra = max_extra_min if max_extra_min is not None else extra_time_allowed(fastest["journey"])
                within = [c for c in found if c["journey"] <= fastest["journey"] + extra]
                greenest = min(within, key=lambda c: (c["co2"], c["journey"]))
                shortest = min(within, key=lambda c: (c["km"], c["journey"]))
                co2 = [c["co2"] for c in within]
                limits.update(fastest_min=fastest["journey"], max_extra_min=extra, within_limit=len(within),
                              co2_min=round(min(co2), 1), co2_typical=round(statistics.median(co2), 1),
                              co2_max=round(max(co2), 1))
                stops = f"≤{max_stops} stop{'s' if max_stops != 1 else ''}"
                for key, label, pick, how in [
                        ("fastest", "Fastest", fastest, f"least journey time, {stops}"),
                        ("greenest", "Greenest", greenest, "least CO₂ per seat within the time limit"),
                        ("shortest", "Shortest distance", shortest, "least km within the time limit")]:
                    options.append(_option(session, key, label, how, pick["path"]))
                shown = {tuple(o["path"]) for o in options}
                for c in sorted(within, key=lambda c: (c["journey"], c["co2"])):
                    if len(alternatives) == k:
                        break
                    if tuple(c["path"]) not in shown:
                        shown.add(tuple(c["path"]))
                        alternatives.append(_option(session, f"alt{len(alternatives) + 1}",
                                                    f"Alternative {len(alternatives) + 1}",
                                                    "next fastest bookable itinerary", c["path"]))

            # Reference: the unconstrained carbon optimum from GDS Dijkstra.
            rows = session.run(PATH_DIJKSTRA, src=src, dst=dst, graph=graph, weight="weight_carbon").data()
            unconstrained = None
            if rows and rows[0]["path"] and (not options or rows[0]["path"] != options[1]["path"]):
                unconstrained = _option(session, "unconstrained", "Unconstrained optimum",
                                        "GDS Dijkstra on weight_carbon, no stop or time limit", rows[0]["path"])
            # How each itinerary compares with the typical (median) bookable one.
            for o in options + alternatives + ([unconstrained] if unconstrained else []):
                if limits.get("co2_typical"):
                    o["vs_typical_pct"] = round(100 * (o["co2_per_seat_kg"] - limits["co2_typical"]) / limits["co2_typical"])
            codes = sorted({c for o in options + alternatives + [unconstrained or {"path": []}] for c in o["path"]}
                           | {src, dst})
            stops = {r["code"]: r for r in plain(session.run(PATH_STOPS, codes=codes).data())}
    return {"src": src, "dst": dst, "airports": stops, "options": options, "alternatives": alternatives,
            "unconstrained": unconstrained, "limits": limits}


# ---------------------------------------------------------------- Disruptions

DEMO_EVENT_ID = "EVT-DEMO-DXB-CLOSURE"


def _close_airports(tx, codes, event_id):
    """Soft-close ACTIVE airports and their ACTIVE corridors, tagging both with
    the event so restore_disruption() undoes exactly this closure."""
    closed = tx.run("""
        MATCH (a:Airport) WHERE a.airport_id IN $codes AND a.operational_status = 'ACTIVE'
        SET a.operational_status = 'CLOSED', a.disrupted_by = $event_id
        RETURN collect(a.airport_id) AS codes
        """, codes=codes, event_id=event_id).single()["codes"]
    tx.run("""
        MATCH (a:Airport)-[r:ROUTE]-() WHERE a.airport_id IN $codes AND r.corridor_status = 'ACTIVE'
        SET r.corridor_status = 'DISRUPTED', r.disrupted_by = $event_id
        """, codes=closed, event_id=event_id)
    return closed


def _close_airport(tx, code, event_id):
    return _close_airports(tx, [code], event_id)


def simulate_disruption(driver, airports=(), region=None, severity="CRITICAL", event_type="OTHER",
                        description=None, event_id=None, impact=True):
    """Close airports (by code and/or a whole region) in one explicit transaction.

    The closure and its DisruptionEvent (linked by AFFECTS) commit together or
    not at all. With impact=True the network-wide damage is measured too (see
    disruption impact below) and its headline numbers stored on the event;
    otherwise the GDS projection is just dropped and rebuilt on next use.
    """
    event_id = event_id or f"EVT-WEB-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    sample = before = None
    if impact:
        with driver.session() as session:
            to_close = session.run(CLOSURE_SELECTION, airports=list(airports), region=region).single()["codes"]
        sample = _impact_sample(driver, to_close)
        before = _impact_costs(driver, *sample) if sample[0] else None
    with driver.session() as session:
        with session.begin_transaction() as tx:
            codes = tx.run("""
                MATCH (a:Airport) WHERE a.airport_id IN $airports OR a.region = $region
                RETURN collect(a.airport_id) AS codes
                """, airports=list(airports), region=region).single()["codes"]
            closed = _close_airports(tx, codes, event_id)
            if not closed:
                raise ValueError("no ACTIVE airport matches that selection")
            what = region if region and not airports else ", ".join(closed[:5]) + ("..." if len(closed) > 5 else "")
            tx.run("""
                MERGE (e:DisruptionEvent {event_id: $event_id})
                SET e.event_type = $event_type, e.severity = $severity, e.data_source = 'MANUAL',
                    e.description = coalesce($description, 'Simulated closure of ' + $what),
                    e.start_time = datetime(), e.end_time = null
                WITH e
                MATCH (a:Airport) WHERE a.airport_id IN $closed
                MERGE (e)-[:AFFECTS]->(a)
                """, event_id=event_id, event_type=event_type, severity=severity,
                   description=description, what=what, closed=closed)
            tx.commit()
        disrupted = session.run("MATCH ()-[r:ROUTE {disrupted_by: $id}]->() RETURN count(r) AS n",
                                id=event_id).single()["n"]
    result = {"event_id": event_id, "closed_airports": closed, "disrupted_routes": disrupted}
    if not impact:
        invalidate_projection(driver)
        return result
    project_active_network(driver)   # needed now for the "after" measurements
    after = _impact_costs(driver, *sample) if before else None
    result["impact"] = _summarise_impact(driver, event_id, closed, before, after)
    return result


def restore_disruption(driver, event_id):
    """Reopen exactly the airports and corridors an event closed, and end the event."""
    with driver.session() as session:
        with session.begin_transaction() as tx:
            reopened = tx.run("""
                MATCH (a:Airport {disrupted_by: $id})
                SET a.operational_status = 'ACTIVE' REMOVE a.disrupted_by
                RETURN count(a) AS n
                """, id=event_id).single()["n"]
            restored = tx.run("""
                MATCH ()-[r:ROUTE {disrupted_by: $id}]->()
                SET r.corridor_status = 'ACTIVE' REMOVE r.disrupted_by
                RETURN count(r) AS n
                """, id=event_id).single()["n"]
            tx.run("MATCH (e:DisruptionEvent {event_id: $id}) SET e.end_time = datetime()", id=event_id)
            tx.commit()
    invalidate_projection(driver)
    return {"event_id": event_id, "reopened_airports": reopened, "restored_routes": restored}


def disruption_demo(driver, code="DXB", src="BOM", dst="JFK"):
    """Explicit transactions: commit a closure, show a rollback, then restore.

    Returns a dict of before/after results for printing.
    """
    out = {"before": None, "after": None, "rollback": None, "restored": None}
    route_q = next(q for q in QUERIES if q.number == 4)

    project_active_network(driver)
    out["before"] = run_query(driver, route_q, src=src, dst=dst)

    # 1. Committed transaction: closure + audit event together.
    closure = simulate_disruption(driver, [code], event_id=DEMO_EVENT_ID,
                                  description="Demo: simulated closure of " + code, impact=False)
    out["after"] = run_query(driver, route_q, src=src, dst=dst)
    out["disrupted_routes"] = closure["disrupted_routes"]

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
    restore_disruption(driver, DEMO_EVENT_ID)
    with driver.session() as session:
        out["restored"] = session.run("""
            MATCH (a:Airport {airport_id: $code})
            RETURN a.operational_status AS status,
                   COUNT { (a)-[:ROUTE {corridor_status: 'ACTIVE'}]-() } AS active_routes,
                   COUNT { ()-[:ROUTE {disrupted_by: $id}]-() } AS still_disrupted
            """, code=code, id=DEMO_EVENT_ID).single().data()
    return out


# ---------------------------------------------------------------- Disruption impact
#
# How much damage does a closure do to the rest of the network? Trips are
# sampled from the IMPACT_ORIGINS open airports that depend most on the closed
# ones (largest share of their routes going there) to the IMPACT_TARGETS
# busiest open airports. GDS single-source Dijkstra (allShortestPaths) gives the
# fastest (weight_trip) and greenest (weight_carbon) cost of every sampled trip
# in one call per origin, before and after the closure. Trips whose fastest
# path went through a closed airport are the affected ones.

IMPACT_ORIGINS = 25
IMPACT_TARGETS = 50

CLOSURE_SELECTION = """
MATCH (a:Airport {operational_status: 'ACTIVE'}) WHERE a.airport_id IN $airports OR a.region = $region
RETURN collect(a.airport_id) AS codes
"""

IMPACT_ORIGINS_Q = """
MATCH (c:Airport)-[:ROUTE]-(n:Airport {operational_status: 'ACTIVE'})
WHERE c.airport_id IN $closed AND NOT n.airport_id IN $closed
WITH DISTINCT n
WITH n, COUNT { (n)-[:ROUTE]->() } AS total,
     COUNT { (n)-[:ROUTE]->(x:Airport) WHERE x.airport_id IN $closed } AS to_closed
RETURN n.airport_id AS code ORDER BY 1.0 * to_closed / total DESC, total DESC LIMIT $limit
"""

IMPACT_TARGETS_Q = """
MATCH (a:Airport {operational_status: 'ACTIVE'}) WHERE NOT a.airport_id IN $closed
RETURN a.airport_id AS code ORDER BY COUNT { (a)-[:ROUTE]->() } DESC LIMIT $limit
"""

TRIP_COSTS = """
MATCH (t:Airport) WHERE t.airport_id IN $targets
WITH collect(id(t)) AS tids
UNWIND $origins AS code
MATCH (s:Airport {airport_id: code})
// An airport left with no active route isn't in the projection: its trips have no route at all.
WHERE s.operational_status = 'ACTIVE'
  AND EXISTS { (s)-[:ROUTE {corridor_status: 'ACTIVE'}]-(:Airport {operational_status: 'ACTIVE'}) }
CALL gds.allShortestPaths.dijkstra.stream($graph, {sourceNode: s, relationshipWeightProperty: $weight})
YIELD targetNode, totalCost, nodeIds
WITH code, targetNode, totalCost, nodeIds, tids WHERE targetNode IN tids
RETURN code AS src, gds.util.asNode(targetNode).airport_id AS dst, totalCost AS cost,
       CASE WHEN $paths THEN [n IN gds.util.asNodes(nodeIds) | n.airport_id] END AS path
"""

# Daily flights and seats lost: one departure per (route, carrier) a day, as in
# the Live Traffic timetable, times the typical seats of the route's aircraft.
DISRUPTED_CAPACITY = """
MATCH ()-[r:ROUTE {disrupted_by: $id}]->()
CALL (r) {
  UNWIND r.aircraft_types AS type_id
  MATCH (ac:Aircraft {aircraft_type_id: type_id}) WHERE ac.typical_seats > 0
  RETURN avg(ac.typical_seats) AS seats
}
RETURN sum(size(r.carriers)) AS flights, round(sum(size(r.carriers) * coalesce(seats, 0))) AS seats
"""

# Open airports that lost at least half of their routes.
CUT_OFF = """
MATCH (c:Airport)-[:ROUTE]-(a:Airport {operational_status: 'ACTIVE'})
WHERE c.airport_id IN $closed
WITH DISTINCT a
WITH a, COUNT { (a)-[:ROUTE]->() } AS total,
     COUNT { (a)-[:ROUTE {corridor_status: 'ACTIVE'}]->(:Airport {operational_status: 'ACTIVE'}) } AS left
WHERE 2 * left <= total
RETURN a.airport_id AS code, a.city AS city, total, left
ORDER BY left, total DESC
"""

STORE_IMPACT = """
MATCH (e:DisruptionEvent {event_id: $id})
SET e.impact_flights_per_day = $flights, e.impact_seats_per_day = $seats,
    e.impact_cut_off_airports = $cut_off, e.impact_affected_trips = $affected,
    e.impact_lost_trips = $lost, e.impact_avg_extra_min = $extra_min
"""


def _impact_sample(driver, closed):
    with driver.session() as session:
        origins = [r["code"] for r in session.run(IMPACT_ORIGINS_Q, closed=closed, limit=IMPACT_ORIGINS)]
        targets = [r["code"] for r in session.run(IMPACT_TARGETS_Q, closed=closed, limit=IMPACT_TARGETS)]
    return origins, targets


def _impact_costs(driver, origins, targets):
    """{(src, dst): (cost, path)} for the fastest trips and {(src, dst): cost} for the greenest."""
    with _projection_lock:
        ensure_projection(driver)
        with driver.session(notifications_disabled_classifications=["DEPRECATION"]) as session:
            run = lambda weight, paths: session.run(TRIP_COSTS, origins=origins, targets=targets,
                                                    graph=ACTIVE_GRAPH, weight=weight, paths=paths)
            fastest = {(r["src"], r["dst"]): (r["cost"], r["path"]) for r in run("weight_trip", True)}
            greenest = {(r["src"], r["dst"]): r["cost"] for r in run("weight_carbon", False)}
    return fastest, greenest


def _summarise_impact(driver, event_id, closed, before, after):
    closed_set = set(closed)
    with driver.session() as session:
        cap = session.run(DISRUPTED_CAPACITY, id=event_id).single()
        cut = session.run(CUT_OFF, closed=closed).data()
        cities = {}
    report = {"flights_per_day": cap["flights"] or 0, "seats_per_day": int(cap["seats"] or 0),
              "cut_off_count": len(cut), "isolated_count": sum(1 for a in cut if a["left"] == 0),
              "cut_off": cut[:12], "sampled_trips": 0, "affected_trips": 0, "slower_trips": 0,
              "lost_trips": 0, "avg_extra_min": 0, "avg_extra_co2_kg": 0.0, "shifts": [], "worst": []}
    if before:
        (fast_b, green_b), (fast_a, green_a) = before, after
        affected = [pair for pair, (_, path) in fast_b.items() if closed_set & set(path[1:-1])]
        extra_min, extra_co2, shifts, worst = [], [], Counter(), []
        for pair in affected:
            cost_b, path_b = fast_b[pair]
            if pair not in fast_a:
                worst.append({"src": pair[0], "dst": pair[1], "before_min": round(cost_b - CONNECTION_MIN),
                              "after_min": None, "before_path": path_b, "after_path": []})
                continue
            cost_a, path_a = fast_a[pair]
            extra_min.append(cost_a - cost_b)
            shifts.update(path_a[1:-1])
            if pair in green_b and pair in green_a:
                extra_co2.append(green_a[pair] - green_b[pair])
            worst.append({"src": pair[0], "dst": pair[1], "before_min": round(cost_b - CONNECTION_MIN),
                          "after_min": round(cost_a - CONNECTION_MIN), "before_path": path_b, "after_path": path_a})
        # Hardest-hit examples: a couple of stranded trips, then the biggest delays.
        stranded = [w for w in worst if w["after_min"] is None]
        slowed = sorted((w for w in worst if w["after_min"] is not None),
                        key=lambda w: w["before_min"] - w["after_min"])
        worst = stranded[:2] + slowed[:6 - min(2, len(stranded))]
        with driver.session() as session:
            cities = {r["code"]: r["city"] for r in session.run(
                "UNWIND $codes AS c MATCH (a:Airport {airport_id: c}) RETURN c AS code, a.city AS city",
                codes=[h for h, _ in shifts.most_common(6)])}
        report.update(
            sampled_trips=len(fast_b), affected_trips=len(affected),
            slower_trips=sum(1 for m in extra_min if m > 0.5), lost_trips=len(affected) - len(extra_min),
            avg_extra_min=round(statistics.mean(extra_min)) if extra_min else 0,
            avg_extra_co2_kg=round(statistics.mean(extra_co2), 1) if extra_co2 else 0.0,
            shifts=[{"code": h, "city": cities.get(h), "trips": n} for h, n in shifts.most_common(6)],
            worst=worst[:6])
    with driver.session() as session:
        session.run(STORE_IMPACT, id=event_id, flights=report["flights_per_day"], seats=report["seats_per_day"],
                    cut_off=report["cut_off_count"], affected=report["affected_trips"],
                    lost=report["lost_trips"], extra_min=report["avg_extra_min"]).consume()
    return report


# ---------------------------------------------------------------- Graph explorer

CAPTIONS = {"Airport": "airport_id", "Airline": "airline_id", "Aircraft": "model",
            "Engine": "engine_model", "Flight": "flight_number", "DisruptionEvent": "event_type"}

AIRPORT_GRAPH = """
MATCH (a:Airport {airport_id: $code})
CALL (a) {
  MATCH p = (a)-[r:ROUTE {corridor_status: 'ACTIVE'}]->(:Airport)
  RETURN p ORDER BY size(r.carriers) DESC, r.distance_km LIMIT $limit
  UNION
  MATCH p = (a)-[:HUB_OF]->(:Airline)
  RETURN p
  UNION
  MATCH p = (a)
  RETURN p
}
RETURN p
"""

FLIGHT_GRAPH = """
MATCH (al:Airline)-[op:OPERATES]->(f:Flight {flight_id: $flight_id}),
      (f)-[dep:DEPARTS_FROM]->(o:Airport), (f)-[arr:ARRIVES_AT]->(d:Airport),
      (f)-[u:USES_AIRCRAFT]->(ac:Aircraft)
OPTIONAL MATCH (ac)-[pb:POWERED_BY]->(e:Engine)
OPTIONAL MATCH (o)-[r:ROUTE]->(d)
OPTIONAL MATCH (ev:DisruptionEvent)-[af:AFFECTS]->(x:Airport)
  WHERE x IN [o, d] AND f.scheduled_departure >= ev.start_time
    AND (ev.end_time IS NULL OR f.scheduled_departure <= ev.end_time)
RETURN al, op, f, dep, o, arr, d, u, ac, collect(DISTINCT [pb, e]) AS engines, r,
       collect(DISTINCT [ev, af, x]) AS events
"""

EVENT_GRAPH = """
MATCH (e:DisruptionEvent {event_id: $event_id})
OPTIONAL MATCH (e)-[af:AFFECTS]->(a:Airport)
OPTIONAL MATCH (a)<-[dep:DEPARTS_FROM]-(f:Flight)
  WHERE f.status = 'CANCELLED' AND f.scheduled_departure >= e.start_time
    AND (e.end_time IS NULL OR f.scheduled_departure <= e.end_time)
WITH e, collect(DISTINCT [af, a]) AS affected, collect([dep, f])[..$limit] AS cancelled
RETURN e, affected, cancelled
"""


def _graph(driver, cypher, **params):
    """Run a query and return every node and relationship in its result as
    {nodes: [{id, label, caption, props}], edges: [{id, from, to, type, props}]}."""
    def work(tx):
        g = tx.run(cypher, **params).graph()
        nodes = []
        for n in g.nodes:
            label = next(iter(n.labels), "")
            nodes.append({"id": n.element_id, "label": label,
                          "caption": str(n.get(CAPTIONS.get(label, ""), "") or ""), "props": plain(dict(n))})
        edges = [{"id": r.element_id, "from": r.start_node.element_id, "to": r.end_node.element_id,
                  "type": r.type, "props": plain(dict(r))} for r in g.relationships]
        return {"nodes": nodes, "edges": edges}
    with driver.session() as session:
        return session.execute_read(work)


def airport_graph(driver, code, limit=40):
    """An airport, its busiest ACTIVE routes and the airlines it is a hub of."""
    return _graph(driver, AIRPORT_GRAPH, code=code, limit=limit)


def flight_graph(driver, flight_id):
    """A flight with its airline, airports, aircraft, engines, the ROUTE edge it
    flies and any disruption active at its airports when it departed."""
    return _graph(driver, FLIGHT_GRAPH, flight_id=flight_id)


def event_graph(driver, event_id, limit=60):
    """A disruption event, the airports it affects and the flights cancelled there."""
    return _graph(driver, EVENT_GRAPH, event_id=event_id, limit=limit)
