"""Web app backend (Review 3): a JSON API over crud.py and queries.py, plus the
static frontend in static/.

    .venv/Scripts/python -m uvicorn app:app --reload     # http://localhost:8000

Anyone can search routes, explore the graph and view analytics. Writes (CRUD
and disruption simulation) need the admin login, configured in .env:
ADMIN_USER, ADMIN_PASSWORD and SESSION_SECRET (signs the session cookie).
Every write that can change the ACTIVE network re-projects the GDS graph.
"""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from neo4j.exceptions import ConstraintError, Neo4jError, ServiceUnavailable
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

import crud
import queries
import traffic
from seed import ROOT, _load_dotenv, neo4j_driver

_load_dotenv()
log = logging.getLogger("uvicorn.error")

AirportStatus = Literal["ACTIVE", "RESTRICTED", "CLOSED"]
CorridorStatus = Literal["ACTIVE", "DISRUPTED", "CLOSED"]
Severity = Literal["MODERATE", "SEVERE", "CRITICAL"]
EventType = Literal["WEATHER", "ATC_FAILURE", "FOG", "STRIKE", "TECHNICAL", "OTHER"]


@asynccontextmanager
async def lifespan(app):
    # Short timeouts: if Neo4j is down, requests fail fast with a 503 instead of hanging.
    app.state.driver = neo4j_driver(connection_timeout=5, connection_acquisition_timeout=10,
                                    max_transaction_retry_time=5)
    try:
        queries.ensure_projection(app.state.driver)
        traffic.warm(app.state.driver)
    except ServiceUnavailable:
        pass  # Neo4j not started yet: requests fail with 503 until it is.
    except Neo4jError as e:  # e.g. out of memory: the projection is retried on first use
        log.warning("startup projection failed: %s", e.message)
    yield
    app.state.driver.close()


app = FastAPI(title="Airline Route Optimization", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET") or secrets.token_hex(32),
                   same_site="strict", max_age=8 * 3600)
app.add_middleware(GZipMiddleware, minimum_size=2000)  # the live-traffic list compresses ~4x


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Pages, JS and CSS are revalidated on every load (cheap 304s), so an
    edited file is never served stale from the browser cache."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(ServiceUnavailable)
def neo4j_down(request, exc):
    return JSONResponse({"detail": "Neo4j is not running. Start the DBMS in Neo4j Desktop."}, status_code=503)


@app.exception_handler(ConstraintError)
def constraint_failed(request, exc):
    return JSONResponse({"detail": f"Constraint violated: {exc.message}"}, status_code=409)


@app.exception_handler(Neo4jError)
def neo4j_failed(request, exc):
    return JSONResponse({"detail": exc.message or exc.code}, status_code=400)


@app.exception_handler(ValueError)
def bad_value(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def db(request: Request):
    return request.app.state.driver


def found(result, what):
    if not result:
        raise HTTPException(404, f"{what} not found")
    return result


def deleted(result, what):
    if not result or not result["deleted"]:
        raise HTTPException(404, f"{what} not found")
    return result


def network_changed(driver):
    """After a write that changes the ACTIVE network, drop the GDS projection
    and the live-traffic timetable; both rebuild lazily on next use. The write
    has already committed, so a failure here is logged, not reported."""
    try:
        queries.invalidate_projection(driver)
    except Neo4jError as e:
        log.warning("could not drop the GDS projection: %s", e.message)
    traffic.invalidate()


# ---------------------------------------------------------------- Auth

class Login(BaseModel):
    username: str
    password: str


def require_admin(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(401, "Admin login required")


@app.post("/api/login")
def login(body: Login, request: Request):
    user, password = os.environ.get("ADMIN_USER"), os.environ.get("ADMIN_PASSWORD")
    if not user or not password:
        raise HTTPException(503, "Admin login is not configured: set ADMIN_USER and ADMIN_PASSWORD in .env")
    ok_user = secrets.compare_digest(body.username.encode(), user.encode())
    ok_password = secrets.compare_digest(body.password.encode(), password.encode())
    if not (ok_user and ok_password):
        raise HTTPException(401, "Wrong username or password")
    request.session["admin"] = True
    return {"admin": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"admin": False}


@app.get("/api/me")
def me(request: Request):
    return {"admin": bool(request.session.get("admin"))}


admin = [Depends(require_admin)]


# ---------------------------------------------------------------- Overview, search, routes

@app.get("/api/stats")
def stats(driver=Depends(db)):
    with driver.session() as session:
        return {"nodes": session.run(queries.OVERVIEW_NODES).data(),
                "relationships": session.run(queries.OVERVIEW_RELS).data()}


@app.get("/api/airports/search")
def search_airports(q: str, limit: int = 10, driver=Depends(db)):
    text = " ".join(w + "*" for w in q.replace("-", " ").split() if w.isalnum())
    return crud.search_airports(driver, text, limit) if text else []


@app.get("/api/routes/find")
def find_routes(src: str, dst: str, k: int = Query(3, ge=1, le=5), max_stops: int = Query(2, ge=0, le=2),
                max_extra: Optional[int] = Query(None, ge=0, le=queries.MAX_EXTRA_MIN), driver=Depends(db)):
    """Bookable itineraries; max_extra (minutes) overrides the automatic extra-time rule."""
    src, dst = src.upper(), dst.upper()
    for code in (src, dst):
        airport = found(crud.get_airport(driver, code), f"Airport {code}")
        if airport["airport"]["operational_status"] != "ACTIVE":
            raise HTTPException(409, f"{code} is {airport['airport']['operational_status']}: "
                                     "restore it in the Disruption Simulator first")
    return queries.find_routes(driver, src, dst, k, max_stops, max_extra)


# ---------------------------------------------------------------- Graph explorer

@app.get("/api/graph/airport/{code}")
def airport_graph(code: str, limit: int = 40, driver=Depends(db)):
    g = queries.airport_graph(driver, code.upper(), min(limit, 150))
    return found(g["nodes"] and g, f"Airport {code}")


@app.get("/api/graph/flight/{flight_id}")
def flight_graph(flight_id: str, driver=Depends(db)):
    g = queries.flight_graph(driver, flight_id)
    return found(g["nodes"] and g, f"Flight {flight_id}")


@app.get("/api/graph/event/{event_id}")
def event_graph(event_id: str, driver=Depends(db)):
    g = queries.event_graph(driver, event_id)
    return found(g["nodes"] and g, f"Event {event_id}")


# ---------------------------------------------------------------- Analytics

# Query parameters the UI must not change (they have to match the projection).
FIXED_PARAMS = {"graph", "connection_min"}


def _query(n):
    return found(next((q for q in queries.QUERIES if q.number == n), None), f"Query {n}")


@app.get("/api/analytics")
def analytics_list():
    return [{"number": q.number, "title": q.title, "feature": q.feature, "cypher": q.cypher.strip(),
             "params": {k: v for k, v in q.params.items() if k not in FIXED_PARAMS}} for q in queries.QUERIES]


@app.get("/api/analytics/{n}")
def analytics_run(n: int, request: Request, driver=Depends(db)):
    q = _query(n)
    overrides = {}
    for key, value in request.query_params.items():
        if key in q.params and key not in FIXED_PARAMS:
            default = q.params[key]
            overrides[key] = type(default)(value.upper() if isinstance(default, str) else value)
    return {"number": q.number, "title": q.title, "rows": queries.run_query(driver, q, **overrides)}


# ---------------------------------------------------------------- Airports

class AirportIn(BaseModel):
    airport_id: str = Field(pattern=r"^[A-Z0-9]{3}$")
    name: str
    city: str
    country: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    region: Optional[str] = None
    timezone: Optional[str] = None
    elevation_m: Optional[float] = None
    runway_count: Optional[int] = Field(None, ge=0)
    is_hub: bool = False
    daily_capacity: int = Field(100, ge=0)


class AirportUpdate(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    timezone: Optional[str] = None
    elevation_m: Optional[float] = None
    runway_count: Optional[int] = Field(None, ge=0)
    is_hub: Optional[bool] = None
    daily_capacity: Optional[int] = Field(None, ge=0)


@app.get("/api/airports")
def list_airports(region: Optional[str] = None, country: Optional[str] = None,
                  status: Optional[AirportStatus] = None, limit: int = 50, driver=Depends(db)):
    return crud.list_airports(driver, region, country, status, min(limit, 500))


@app.get("/api/airports/{code}")
def get_airport(code: str, driver=Depends(db)):
    return found(crud.get_airport(driver, code.upper()), f"Airport {code}")


@app.post("/api/airports", dependencies=admin, status_code=201)
def create_airport(body: AirportIn, driver=Depends(db)):
    result = crud.create_airport(driver, **body.model_dump(exclude_none=True))
    network_changed(driver)
    return result


@app.put("/api/airports/{code}", dependencies=admin)
def update_airport(code: str, body: AirportUpdate, driver=Depends(db)):
    return found(crud.update_airport(driver, code.upper(), **body.model_dump(exclude_unset=True)), f"Airport {code}")


class StatusIn(BaseModel):
    status: AirportStatus


@app.put("/api/airports/{code}/status", dependencies=admin)
def set_airport_status(code: str, body: StatusIn, driver=Depends(db)):
    result = found(crud.close_airport(driver, code.upper(), body.status), f"Airport {code}")
    network_changed(driver)
    return result


@app.delete("/api/airports/{code}", dependencies=admin)
def delete_airport(code: str, driver=Depends(db)):
    """Hard delete: only for airports without routes or flights (test data)."""
    airport = found(crud.get_airport(driver, code.upper()), f"Airport {code}")
    if airport["routes_out"]:
        raise HTTPException(409, f"{code} has {airport['routes_out']} routes: close it instead of deleting it")
    result = deleted(crud.delete_airport(driver, code.upper()), f"Airport {code}")
    network_changed(driver)
    return result


# ---------------------------------------------------------------- Airlines

class AirlineIn(BaseModel):
    airline_id: str = Field(pattern=r"^[A-Z0-9]{2}$")
    name: str
    country: str
    fleet_size: Optional[int] = Field(None, ge=0)
    alliance: Optional[str] = None


class AirlineUpdate(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    fleet_size: Optional[int] = Field(None, ge=0)
    alliance: Optional[str] = None


@app.get("/api/airlines")
def list_airlines(q: Optional[str] = None, limit: int = 25, driver=Depends(db)):
    return crud.list_airlines(driver, q or None, min(limit, 200))


@app.get("/api/airlines/{airline_id}")
def get_airline(airline_id: str, driver=Depends(db)):
    return found(crud.get_airline(driver, airline_id.upper()), f"Airline {airline_id}")


@app.post("/api/airlines", dependencies=admin, status_code=201)
def create_airline(body: AirlineIn, driver=Depends(db)):
    return crud.create_airline(driver, **body.model_dump())


@app.put("/api/airlines/{airline_id}", dependencies=admin)
def update_airline(airline_id: str, body: AirlineUpdate, driver=Depends(db)):
    return found(crud.update_airline(driver, airline_id.upper(), **body.model_dump(exclude_unset=True)),
                 f"Airline {airline_id}")


@app.delete("/api/airlines/{airline_id}", dependencies=admin)
def delete_airline(airline_id: str, driver=Depends(db)):
    return deleted(crud.delete_airline(driver, airline_id.upper()), f"Airline {airline_id}")


# ---------------------------------------------------------------- Aircraft and flights

@app.get("/api/aircraft")
def list_aircraft(driver=Depends(db)):
    return crud.list_aircraft(driver)


class FlightIn(BaseModel):
    flight_id: str = Field(pattern=r"^[A-Z0-9]{2}\d{1,4}-\d{8}$")
    flight_number: str
    airline_id: str
    origin: str
    destination: str
    aircraft_type_id: str
    scheduled_departure: str
    scheduled_arrival: str
    tail_number: Optional[str] = None


class FlightUpdate(BaseModel):
    status: Optional[Literal["SCHEDULED", "COMPLETED", "DELAYED", "CANCELLED", "DIVERTED"]] = None
    cancellation_reason: Optional[str] = None


class DepartureIn(BaseModel):
    actual_departure: str


class AircraftIn(BaseModel):
    aircraft_type_id: str
    tail_number: Optional[str] = None


@app.get("/api/flights")
def list_flights(airline_id: Optional[str] = None, origin: Optional[str] = None, status: Optional[str] = None,
                 data_source: Optional[str] = None, limit: int = 50, driver=Depends(db)):
    return crud.list_flights(driver, airline_id and airline_id.upper(), origin and origin.upper(),
                             status, data_source, min(limit, 500))


@app.get("/api/flights/{flight_id}")
def get_flight(flight_id: str, driver=Depends(db)):
    return found(crud.get_flight(driver, flight_id), f"Flight {flight_id}")


@app.post("/api/flights", dependencies=admin, status_code=201)
def create_flight(body: FlightIn, driver=Depends(db)):
    result = crud.create_flight(driver, **body.model_dump(), data_source="MANUAL")
    return found(result, "Airline, airport or aircraft")


@app.put("/api/flights/{flight_id}", dependencies=admin)
def update_flight(flight_id: str, body: FlightUpdate, driver=Depends(db)):
    return found(crud.update_flight(driver, flight_id, **body.model_dump(exclude_unset=True)), f"Flight {flight_id}")


@app.put("/api/flights/{flight_id}/departure", dependencies=admin)
def record_departure(flight_id: str, body: DepartureIn, driver=Depends(db)):
    return found(crud.record_departure(driver, flight_id, body.actual_departure), f"Flight {flight_id}")


@app.put("/api/flights/{flight_id}/aircraft", dependencies=admin)
def reassign_aircraft(flight_id: str, body: AircraftIn, driver=Depends(db)):
    return found(crud.reassign_aircraft(driver, flight_id, body.aircraft_type_id, body.tail_number),
                 f"Flight {flight_id} or aircraft {body.aircraft_type_id}")


@app.delete("/api/flights/{flight_id}", dependencies=admin)
def delete_flight(flight_id: str, driver=Depends(db)):
    return deleted(crud.delete_flight(driver, flight_id), f"Flight {flight_id}")


# ---------------------------------------------------------------- Routes and hubs

class RouteIn(BaseModel):
    src: str
    dst: str
    distance_km: float = Field(gt=0)
    avg_duration_min: int = Field(gt=0)
    co2_per_seat_kg: float = Field(gt=0)
    weight_cost: Optional[float] = None


class RouteUpdate(BaseModel):
    avg_duration_min: Optional[int] = Field(None, gt=0)
    co2_per_seat_kg: Optional[float] = Field(None, gt=0)
    weight_cost: Optional[float] = None


class CorridorIn(BaseModel):
    status: CorridorStatus


@app.get("/api/routes/{src}/{dst}")
def get_route(src: str, dst: str, driver=Depends(db)):
    return found(crud.get_route(driver, src.upper(), dst.upper()), f"Route {src}->{dst}")


@app.post("/api/routes", dependencies=admin, status_code=201)
def create_route(body: RouteIn, driver=Depends(db)):
    src, dst = body.src.upper(), body.dst.upper()
    if crud.get_route(driver, src, dst):
        raise HTTPException(409, f"Route {src}->{dst} already exists")
    weight_cost = body.weight_cost if body.weight_cost is not None else round(50 + 0.10 * body.distance_km, 2)
    result = found(crud.create_route(driver, src, dst, body.distance_km, body.avg_duration_min,
                                     body.co2_per_seat_kg, weight_cost, data_source="MANUAL"),
                   f"Airport {src} or {dst}")
    network_changed(driver)
    return result


@app.put("/api/routes/{src}/{dst}", dependencies=admin)
def update_route(src: str, dst: str, body: RouteUpdate, driver=Depends(db)):
    props = body.model_dump(exclude_unset=True)
    # Keep the pathfinding weights in step with the values they mirror.
    if "avg_duration_min" in props:
        props["weight_time"] = float(props["avg_duration_min"])
    if "co2_per_seat_kg" in props:
        props["weight_carbon"] = float(props["co2_per_seat_kg"])
    result = found(crud.update_route(driver, src.upper(), dst.upper(), **props), f"Route {src}->{dst}")
    network_changed(driver)
    return result


@app.put("/api/routes/{src}/{dst}/status", dependencies=admin)
def set_corridor_status(src: str, dst: str, body: CorridorIn, driver=Depends(db)):
    result = found(crud.set_corridor_status(driver, src.upper(), dst.upper(), body.status), f"Route {src}->{dst}")
    network_changed(driver)
    return result


@app.delete("/api/routes/{src}/{dst}", dependencies=admin)
def delete_route(src: str, dst: str, driver=Depends(db)):
    result = deleted(crud.delete_route(driver, src.upper(), dst.upper()), f"Route {src}->{dst}")
    network_changed(driver)
    return result


class HubIn(BaseModel):
    airport_id: str
    airline_id: str
    since: Optional[str] = None


@app.post("/api/hubs", dependencies=admin, status_code=201)
def add_hub(body: HubIn, driver=Depends(db)):
    return found(crud.add_hub(driver, body.airport_id.upper(), body.airline_id.upper(), body.since or None),
                 "Airport or airline")


@app.delete("/api/hubs/{airport_id}/{airline_id}", dependencies=admin)
def remove_hub(airport_id: str, airline_id: str, driver=Depends(db)):
    return deleted(crud.remove_hub(driver, airport_id.upper(), airline_id.upper()), "Hub link")


# ---------------------------------------------------------------- Events and disruptions

class EventIn(BaseModel):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,60}$")
    event_type: EventType
    severity: Severity
    start_time: str
    end_time: Optional[str] = None
    airports: list[str]
    description: str


class ResolveIn(BaseModel):
    end_time: str


class DisruptionIn(BaseModel):
    airports: list[str] = []
    region: Optional[str] = None
    severity: Severity = "CRITICAL"
    event_type: EventType = "OTHER"
    description: Optional[str] = None


@app.get("/api/events")
def list_events(driver=Depends(db)):
    return crud.list_events(driver)


@app.get("/api/events/{event_id}")
def get_event(event_id: str, driver=Depends(db)):
    return found(crud.get_event(driver, event_id), f"Event {event_id}")


@app.post("/api/events", dependencies=admin, status_code=201)
def create_event(body: EventIn, driver=Depends(db)):
    """Logs an event for the audit trail only; it does not close anything."""
    return found(crud.create_event(driver, **body.model_dump(), data_source="MANUAL"), "Affected airports")


@app.put("/api/events/{event_id}/resolve", dependencies=admin)
def resolve_event(event_id: str, body: ResolveIn, driver=Depends(db)):
    return found(crud.resolve_event(driver, event_id, body.end_time), f"Event {event_id}")


@app.delete("/api/events/{event_id}", dependencies=admin)
def delete_event(event_id: str, driver=Depends(db)):
    event = found(crud.get_event(driver, event_id), f"Event {event_id}")
    if event["event"].get("data_source") != "MANUAL":
        raise HTTPException(409, "Only manually created events can be deleted")
    queries.restore_disruption(driver, event_id)  # reopen anything it still holds closed
    traffic.invalidate()
    return deleted(crud.delete_event(driver, event_id), f"Event {event_id}")


@app.post("/api/disruptions", dependencies=admin, status_code=201)
def simulate_disruption(body: DisruptionIn, driver=Depends(db)):
    if not body.airports and not body.region:
        raise HTTPException(422, "Choose at least one airport or a region")
    result = queries.simulate_disruption(driver, [a.upper() for a in body.airports], body.region or None,
                                         body.severity, body.event_type, body.description or None)
    traffic.invalidate()
    return result


@app.post("/api/disruptions/{event_id}/restore", dependencies=admin)
def restore_disruption(event_id: str, driver=Depends(db)):
    found(crud.get_event(driver, event_id), f"Event {event_id}")
    result = queries.restore_disruption(driver, event_id)
    traffic.invalidate()
    return result


# ---------------------------------------------------------------- Live traffic

@app.get("/api/live")
def live_traffic(driver=Depends(db)):
    """Simulated flights in the air right now (see traffic.py)."""
    return traffic.live(driver)


@app.get("/api/replay")
def replay(day: str = Query("2015-01-26", pattern=r"^2015-01-(0[1-9]|[12]\d|3[01])$"), driver=Depends(db)):
    """The real January 2015 Flight nodes departing on one day."""
    return traffic.replay(driver, day)


# Static frontend last, so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
