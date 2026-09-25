"""Node and relationship CRUD for the airline graph.

Every function takes a neo4j driver (see seed.neo4j_driver) and runs one
parameterised Cypher statement inside a managed transaction, so each call
is atomic. Datetimes are passed as ISO 8601 strings with a UTC offset and
converted with datetime() in Cypher. Reused by the Review 3 web backend.

Airports are never hard-deleted in normal use: close_airport() is the soft
delete the design relies on (queries and GDS projections filter on
operational_status = 'ACTIVE'). delete_airport() exists for test data only.
"""

from neo4j.spatial import Point
from neo4j.time import Date, DateTime


def plain(value):
    """Neo4j values -> plain Python (ISO strings, lat/lon dicts) for printing/JSON."""
    if isinstance(value, (DateTime, Date)):
        return value.to_native().isoformat()
    if isinstance(value, Point):
        return {"latitude": value.y, "longitude": value.x}
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def _write(driver, query, **params):
    with driver.session() as session:
        return plain(session.execute_write(lambda tx: tx.run(query, **params).data()))


def _read(driver, query, **params):
    with driver.session() as session:
        return plain(session.execute_read(lambda tx: tx.run(query, **params).data()))


def _one(rows):
    return rows[0] if rows else None


# ---------------------------------------------------------------- Airport

def create_airport(driver, airport_id, name, city, country, latitude, longitude, **props):
    """CREATE (not MERGE): a duplicate airport_id fails on the unique constraint."""
    return _one(_write(driver, """
        CREATE (a:Airport {airport_id: $airport_id})
        SET a += $props, a.name = $name, a.city = $city, a.country = $country,
            a.location = point({latitude: $latitude, longitude: $longitude}),
            a.operational_status = 'ACTIVE'
        RETURN a {.*} AS airport
        """, airport_id=airport_id, name=name, city=city, country=country,
        latitude=latitude, longitude=longitude, props=props))


def get_airport(driver, airport_id):
    return _one(_read(driver, """
        MATCH (a:Airport {airport_id: $airport_id})
        OPTIONAL MATCH (a)-[:HUB_OF]->(al:Airline)
        RETURN a {.*} AS airport,
               COUNT { (a)-[:ROUTE]->() } AS routes_out,
               collect(al.airline_id) AS hub_of
        """, airport_id=airport_id))


def list_airports(driver, region=None, country=None, status=None, limit=25):
    return _read(driver, """
        MATCH (a:Airport)
        WHERE ($region IS NULL OR a.region = $region)
          AND ($country IS NULL OR a.country = $country)
          AND ($status IS NULL OR a.operational_status = $status)
        RETURN a.airport_id AS airport_id, a.name AS name, a.city AS city,
               a.country AS country, a.operational_status AS status
        ORDER BY a.airport_id LIMIT $limit
        """, region=region, country=country, status=status, limit=limit)


def search_airports(driver, text, limit=10):
    """Full-text search on name / city / code (airport_search index)."""
    return _read(driver, """
        CALL db.index.fulltext.queryNodes('airport_search', $text) YIELD node, score
        RETURN node.airport_id AS airport_id, node.name AS name, node.city AS city, score
        LIMIT $limit
        """, text=text, limit=limit)


def update_airport(driver, airport_id, **props):
    return _one(_write(driver, """
        MATCH (a:Airport {airport_id: $airport_id})
        SET a += $props
        RETURN a {.*} AS airport
        """, airport_id=airport_id, props=props))


def close_airport(driver, airport_id, status="CLOSED"):
    """Soft delete: routes and GDS projections skip non-ACTIVE airports."""
    return update_airport(driver, airport_id, operational_status=status)


def reopen_airport(driver, airport_id):
    return update_airport(driver, airport_id, operational_status="ACTIVE")


def delete_airport(driver, airport_id):
    """Hard delete, for test data only (see module docstring)."""
    return _one(_write(driver, """
        MATCH (a:Airport {airport_id: $airport_id})
        DETACH DELETE a
        RETURN count(*) AS deleted
        """, airport_id=airport_id))


# ---------------------------------------------------------------- Airline

def create_airline(driver, airline_id, name, country, fleet_size=None, alliance=None):
    return _one(_write(driver, """
        CREATE (al:Airline {airline_id: $airline_id, name: $name, country: $country,
                            fleet_size: $fleet_size, alliance: $alliance})
        RETURN al {.*} AS airline
        """, airline_id=airline_id, name=name, country=country, fleet_size=fleet_size, alliance=alliance))


def get_airline(driver, airline_id):
    return _one(_read(driver, """
        MATCH (al:Airline {airline_id: $airline_id})
        OPTIONAL MATCH (hub:Airport)-[:HUB_OF]->(al)
        RETURN al {.*} AS airline, collect(hub.airport_id) AS hubs,
               COUNT { (al)-[:OPERATES]->() } AS flights
        """, airline_id=airline_id))


def update_airline(driver, airline_id, **props):
    return _one(_write(driver, """
        MATCH (al:Airline {airline_id: $airline_id})
        SET al += $props
        RETURN al {.*} AS airline
        """, airline_id=airline_id, props=props))


def delete_airline(driver, airline_id):
    return _one(_write(driver, """
        MATCH (al:Airline {airline_id: $airline_id})
        DETACH DELETE al
        RETURN count(*) AS deleted
        """, airline_id=airline_id))


# ---------------------------------------------------------------- Flight

def create_flight(driver, flight_id, flight_number, airline_id, origin, destination,
                  aircraft_type_id, scheduled_departure, scheduled_arrival,
                  tail_number=None, **props):
    """Creates the Flight and its OPERATES / DEPARTS_FROM / ARRIVES_AT /
    USES_AIRCRAFT relationships in one transaction."""
    return _one(_write(driver, """
        MATCH (al:Airline {airline_id: $airline_id})
        MATCH (o:Airport {airport_id: $origin})
        MATCH (d:Airport {airport_id: $destination})
        MATCH (ac:Aircraft {aircraft_type_id: $aircraft_type_id})
        CREATE (f:Flight {flight_id: $flight_id})
        SET f += $props, f.flight_number = $flight_number,
            f.scheduled_departure = datetime($scheduled_departure),
            f.scheduled_arrival = datetime($scheduled_arrival),
            f.status = coalesce($props.status, 'SCHEDULED')
        CREATE (al)-[:OPERATES]->(f), (f)-[:DEPARTS_FROM]->(o), (f)-[:ARRIVES_AT]->(d),
               (f)-[:USES_AIRCRAFT {tail_number: $tail_number}]->(ac)
        RETURN f {.*} AS flight
        """, flight_id=flight_id, flight_number=flight_number, airline_id=airline_id,
        origin=origin, destination=destination, aircraft_type_id=aircraft_type_id,
        scheduled_departure=scheduled_departure, scheduled_arrival=scheduled_arrival,
        tail_number=tail_number, props=props))


def get_flight(driver, flight_id):
    return _one(_read(driver, """
        MATCH (al:Airline)-[:OPERATES]->(f:Flight {flight_id: $flight_id}),
              (f)-[:DEPARTS_FROM]->(o:Airport), (f)-[:ARRIVES_AT]->(d:Airport),
              (f)-[u:USES_AIRCRAFT]->(ac:Aircraft)
        RETURN f {.*} AS flight, al.name AS airline, o.airport_id AS origin,
               d.airport_id AS destination, ac.model AS aircraft, u.tail_number AS tail_number
        """, flight_id=flight_id))


def list_flights(driver, airline_id=None, origin=None, status=None, data_source=None, limit=25):
    return _read(driver, """
        MATCH (al:Airline)-[:OPERATES]->(f:Flight)-[:DEPARTS_FROM]->(o:Airport),
              (f)-[:ARRIVES_AT]->(d:Airport)
        WHERE ($airline_id IS NULL OR al.airline_id = $airline_id)
          AND ($origin IS NULL OR o.airport_id = $origin)
          AND ($status IS NULL OR f.status = $status)
          AND ($data_source IS NULL OR f.data_source = $data_source)
        RETURN f.flight_id AS flight_id, o.airport_id AS origin, d.airport_id AS destination,
               f.scheduled_departure AS scheduled_departure, f.status AS status,
               f.delay_minutes AS delay_minutes
        ORDER BY f.scheduled_departure LIMIT $limit
        """, airline_id=airline_id, origin=origin, status=status, data_source=data_source, limit=limit)


def record_departure(driver, flight_id, actual_departure, delayed_at_min=15):
    """Sets actual_departure and derives delay_minutes and status from it."""
    return _one(_write(driver, """
        MATCH (f:Flight {flight_id: $flight_id})
        SET f.actual_departure = datetime($actual_departure),
            f.delay_minutes = duration.inSeconds(f.scheduled_departure, datetime($actual_departure)).seconds / 60
        SET f.status = CASE WHEN f.delay_minutes >= $delayed_at_min THEN 'DELAYED' ELSE 'COMPLETED' END
        RETURN f {.*} AS flight
        """, flight_id=flight_id, actual_departure=actual_departure, delayed_at_min=delayed_at_min))


def update_flight(driver, flight_id, **props):
    return _one(_write(driver, """
        MATCH (f:Flight {flight_id: $flight_id})
        SET f += $props
        RETURN f {.*} AS flight
        """, flight_id=flight_id, props=props))


def reassign_aircraft(driver, flight_id, aircraft_type_id, tail_number=None):
    """Replaces the USES_AIRCRAFT relationship."""
    return _one(_write(driver, """
        MATCH (f:Flight {flight_id: $flight_id})-[old:USES_AIRCRAFT]->()
        MATCH (ac:Aircraft {aircraft_type_id: $aircraft_type_id})
        DELETE old
        CREATE (f)-[u:USES_AIRCRAFT {tail_number: $tail_number}]->(ac)
        RETURN f.flight_id AS flight_id, ac.model AS aircraft, u.tail_number AS tail_number
        """, flight_id=flight_id, aircraft_type_id=aircraft_type_id, tail_number=tail_number))


def delete_flight(driver, flight_id):
    return _one(_write(driver, """
        MATCH (f:Flight {flight_id: $flight_id})
        DETACH DELETE f
        RETURN count(*) AS deleted
        """, flight_id=flight_id))


# ---------------------------------------------------------------- DisruptionEvent

def create_event(driver, event_id, event_type, severity, start_time, airports,
                 description, end_time=None, data_source="MANUAL"):
    """Creates the event and an AFFECTS relationship to each airport."""
    return _one(_write(driver, """
        CREATE (e:DisruptionEvent {event_id: $event_id, event_type: $event_type,
                severity: $severity, description: $description, data_source: $data_source,
                start_time: datetime($start_time)})
        SET e.end_time = datetime($end_time)
        WITH e
        UNWIND $airports AS code
        MATCH (a:Airport {airport_id: code})
        CREATE (e)-[:AFFECTS]->(a)
        RETURN e {.*} AS event, collect(a.airport_id) AS affects
        """, event_id=event_id, event_type=event_type, severity=severity, start_time=start_time,
        end_time=end_time, airports=airports, description=description, data_source=data_source))


def get_event(driver, event_id):
    return _one(_read(driver, """
        MATCH (e:DisruptionEvent {event_id: $event_id})
        OPTIONAL MATCH (e)-[:AFFECTS]->(a:Airport)
        RETURN e {.*} AS event, collect(a.airport_id) AS affects
        """, event_id=event_id))


def resolve_event(driver, event_id, end_time):
    return _one(_write(driver, """
        MATCH (e:DisruptionEvent {event_id: $event_id})
        SET e.end_time = datetime($end_time)
        RETURN e {.*} AS event
        """, event_id=event_id, end_time=end_time))


def delete_event(driver, event_id):
    return _one(_write(driver, """
        MATCH (e:DisruptionEvent {event_id: $event_id})
        DETACH DELETE e
        RETURN count(*) AS deleted
        """, event_id=event_id))


# ---------------------------------------------------------------- ROUTE

def create_route(driver, src, dst, distance_km, avg_duration_min, co2_per_seat_kg, weight_cost, **props):
    """Adds a directed ROUTE with its pathfinding weights."""
    return _one(_write(driver, """
        MATCH (a:Airport {airport_id: $src}), (b:Airport {airport_id: $dst})
        CREATE (a)-[r:ROUTE]->(b)
        SET r += $props,
            r.distance_km = toFloat($distance_km),
            r.avg_duration_min = $avg_duration_min,
            r.co2_per_seat_kg = toFloat($co2_per_seat_kg),
            r.weight_time = toFloat($avg_duration_min),
            r.weight_carbon = toFloat($co2_per_seat_kg),
            r.weight_cost = toFloat($weight_cost),
            r.corridor_status = 'ACTIVE',
            r.last_updated = datetime()
        RETURN a.airport_id AS src, b.airport_id AS dst, r {.*} AS route
        """, src=src, dst=dst, distance_km=distance_km, avg_duration_min=avg_duration_min,
        co2_per_seat_kg=co2_per_seat_kg, weight_cost=weight_cost, props=props))


def get_route(driver, src, dst):
    return _one(_read(driver, """
        MATCH (:Airport {airport_id: $src})-[r:ROUTE]->(:Airport {airport_id: $dst})
        RETURN r {.*} AS route
        """, src=src, dst=dst))


def update_route(driver, src, dst, **props):
    return _one(_write(driver, """
        MATCH (:Airport {airport_id: $src})-[r:ROUTE]->(:Airport {airport_id: $dst})
        SET r += $props, r.last_updated = datetime()
        RETURN r {.*} AS route
        """, src=src, dst=dst, props=props))


def set_corridor_status(driver, src, dst, status):
    """ACTIVE / DISRUPTED / CLOSED: the soft state routing filters on."""
    return update_route(driver, src, dst, corridor_status=status)


def delete_route(driver, src, dst):
    return _one(_write(driver, """
        MATCH (:Airport {airport_id: $src})-[r:ROUTE]->(:Airport {airport_id: $dst})
        DELETE r
        RETURN count(*) AS deleted
        """, src=src, dst=dst))


# ---------------------------------------------------------------- HUB_OF

def add_hub(driver, airport_id, airline_id, since=None):
    return _one(_write(driver, """
        MATCH (a:Airport {airport_id: $airport_id}), (al:Airline {airline_id: $airline_id})
        MERGE (a)-[h:HUB_OF]->(al)
        SET h.since = CASE WHEN $since IS NULL THEN h.since ELSE date($since) END
        RETURN a.airport_id AS airport, al.airline_id AS airline, h.since AS since
        """, airport_id=airport_id, airline_id=airline_id, since=since))


def remove_hub(driver, airport_id, airline_id):
    return _one(_write(driver, """
        MATCH (:Airport {airport_id: $airport_id})-[h:HUB_OF]->(:Airline {airline_id: $airline_id})
        DELETE h
        RETURN count(*) AS deleted
        """, airport_id=airport_id, airline_id=airline_id))
