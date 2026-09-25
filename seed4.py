"""Seed the Flight layer and DisruptionEvent nodes.

Real flights: a sample of US domestic flights from the Kaggle "2015 Flight
Delays and Cancellations" dataset (BTS on-time data), January 2015, which
includes Winter Storm Juno (26-28 Jan).

Synthetic flights: made-up flights for India and the rest of the world, on
real ROUTE edges with real carriers and aircraft, whose schedules follow the
real departure-hour distribution and whose delays/cancellations are sampled
from the real data. Every Flight and DisruptionEvent carries data_source
'BTS_2015' or 'SYNTHETIC'.

Run after seed.py, seed2.py and seed3.py; uses the same NEO4J_* settings.
"""

import argparse
import random
import string
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from seed import ROOT, load, neo4j_driver, read_airlines, read_airports
from seed2 import read_aircraft
from seed3 import (FARE_BASE_USD, FARE_PER_KM_USD, build_routes, read_equipment,
                   read_iata_to_icao, read_json_routes)

FLIGHTS_FILE = ROOT / "flights.csv"
FLIGHT_COLUMNS = [
    "MONTH", "DAY", "AIRLINE", "FLIGHT_NUMBER", "TAIL_NUMBER", "ORIGIN_AIRPORT",
    "DESTINATION_AIRPORT", "SCHEDULED_DEPARTURE", "DEPARTURE_DELAY", "SCHEDULED_TIME",
    "DISTANCE", "SCHEDULED_ARRIVAL", "ARRIVAL_DELAY", "DIVERTED", "CANCELLED",
    "CANCELLATION_REASON",
]
YEAR, MONTH = 2015, 1
MILES_TO_KM = 1.609344
DELAYED_AT_MIN = 15
SEED = 302

# Real sample: part of it deliberately from Winter Storm Juno.
REAL_SAMPLE_SIZE = 600
JUNO_SAMPLE_SIZE = 200
JUNO_DAYS = {26, 27, 28}
JUNO_AIRPORTS = {"JFK", "LGA", "BOS", "EWR", "PHL"}

# Synthetic flights per bucket, keyed on the origin airport's country
# (India also takes flights *to* India).
SYNTHETIC_QUOTAS = {"India": 150, "Europe": 80, "Middle East": 50,
                    "East & Southeast Asia": 70, "Rest of world": 50}
MIDDLE_EAST = {"AE", "QA", "SA", "OM", "BH", "KW", "JO", "IL", "LB", "IQ", "IR", "EG"}
EAST_SE_ASIA = {"CN", "JP", "KR", "HK", "MO", "TW", "SG", "TH", "MY", "ID", "PH", "VN",
                "KH", "LA", "MM", "BN"}

# Synthetic dense-fog scenario over North India: morning departures on these
# days draw their outcome from the real disrupted-day pool.
FOG_AIRPORTS = {"DEL", "LKO", "ATQ"}
FOG_DAYS = (13, 14)
FOG_HOURS = range(5, 11)
FOG_TZ = "Asia/Kolkata"
FOG_FLIGHTS = 30
MAX_MAKEUP_SHARE = 0.15

# An airport-day becomes (part of) a real DisruptionEvent when cancellations
# for one reason pass both thresholds.
EVENT_MIN_CANCELLATIONS = 30
EVENT_MIN_RATE = 0.25
EVENT_TYPES = {"B": "WEATHER", "C": "ATC_FAILURE"}  # BTS: B weather, C National Air System
EVENT_LABELS = {"WEATHER": "weather", "ATC_FAILURE": "air-traffic-system"}

REGISTRATION_PREFIX = {
    "India": "VT-", "United Kingdom": "G-", "United Arab Emirates": "A6-", "Qatar": "A7-",
    "Saudi Arabia": "HZ-", "Oman": "A4O-", "Bahrain": "A9C-", "Kuwait": "9K-", "Israel": "4X-",
    "Jordan": "JY-", "Egypt": "SU-", "Turkey": "TC-", "Germany": "D-", "France": "F-",
    "Spain": "EC-", "Italy": "I-", "Netherlands": "PH-", "Ireland": "EI-", "Switzerland": "HB-",
    "Austria": "OE-", "Belgium": "OO-", "Portugal": "CS-", "Sweden": "SE-", "Norway": "LN-",
    "Denmark": "OY-", "Finland": "OH-", "Poland": "SP-", "Greece": "SX-", "Russia": "RA-",
    "China": "B-", "Hong Kong SAR of China": "B-", "Taiwan": "B-", "Japan": "JA",
    "South Korea": "HL", "Singapore": "9V-", "Thailand": "HS-", "Malaysia": "9M-",
    "Indonesia": "PK-", "Philippines": "RP-C", "Vietnam": "VN-", "Sri Lanka": "4R-",
    "Nepal": "9N-", "Bangladesh": "S2-", "Pakistan": "AP-", "Australia": "VH-",
    "New Zealand": "ZK-", "Canada": "C-", "Mexico": "XA-", "Brazil": "PR-",
    "Argentina": "LV-", "Chile": "CC-", "Colombia": "HK-", "South Africa": "ZS-",
    "Kenya": "5Y-", "Ethiopia": "ET-", "Nigeria": "5N-", "Morocco": "CN-",
    "United States": "N",
}
DEFAULT_REGISTRATION_PREFIX = "XX-"
TAILS_PER_FLEET = 3

CONSTRAINTS = [
    "CREATE CONSTRAINT flight_id IF NOT EXISTS "
    "FOR (f:Flight) REQUIRE f.flight_id IS UNIQUE",
    "CREATE CONSTRAINT event_id IF NOT EXISTS "
    "FOR (e:DisruptionEvent) REQUIRE e.event_id IS UNIQUE",
]

MERGE_FLIGHTS = """
UNWIND $rows AS row
MATCH (al:Airline {airline_id: row.airline_id})
MATCH (o:Airport {airport_id: row.origin})
MATCH (d:Airport {airport_id: row.destination})
MATCH (ac:Aircraft {aircraft_type_id: row.aircraft_type_id})
MERGE (f:Flight {flight_id: row.flight_id})
SET f += row.props,
    f.scheduled_departure = datetime(row.scheduled_departure),
    f.scheduled_arrival = datetime(row.scheduled_arrival),
    f.actual_departure = datetime(row.actual_departure),
    f.actual_arrival = datetime(row.actual_arrival)
MERGE (al)-[:OPERATES]->(f)
MERGE (f)-[:DEPARTS_FROM]->(o)
MERGE (f)-[:ARRIVES_AT]->(d)
MERGE (f)-[u:USES_AIRCRAFT]->(ac)
SET u.tail_number = row.tail_number
"""

MERGE_EVENTS = """
UNWIND $rows AS row
MERGE (e:DisruptionEvent {event_id: row.event_id})
SET e += row.props,
    e.start_time = datetime(row.start_time),
    e.end_time = datetime(row.end_time)
WITH e, row
UNWIND row.airports AS code
MATCH (a:Airport {airport_id: code})
MERGE (e)-[:AFFECTS]->(a)
"""


def iso(dt):
    return dt.isoformat() if dt is not None else None


def local_time(day, hhmm, tz):
    """Local wall-clock HHMM on a January 2015 day, as an aware datetime."""
    hhmm = str(hhmm).zfill(4)
    midnight = datetime(YEAR, MONTH, day, tzinfo=ZoneInfo(tz))
    # timedelta also turns "2400" into next-day midnight.
    return midnight + timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[2:]))


def arrive(departure, minutes, tz):
    """Departure + elapsed minutes, in the destination's local time."""
    return (departure.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(ZoneInfo(tz))


def flight_status(cancelled, diverted, delay):
    if cancelled:
        return "CANCELLED"
    if diverted:
        return "DIVERTED"
    return "DELAYED" if delay is not None and delay >= DELAYED_AT_MIN else "COMPLETED"


def read_real_flights(path=FLIGHTS_FILE):
    """All January 2015 rows (the file is ~5.8M rows; read in chunks)."""
    parts = [
        chunk[chunk["MONTH"] == str(MONTH)]
        for chunk in pd.read_csv(path, usecols=FLIGHT_COLUMNS, dtype=str, keep_default_na=False,
                                 na_values=[""], chunksize=500_000)
    ]
    df = pd.concat(parts, ignore_index=True)
    # October 2015 uses 5-digit numeric airport ids; guard anyway.
    df = df[df["ORIGIN_AIRPORT"].str.fullmatch(r"[A-Z]{3}") & df["DESTINATION_AIRPORT"].str.fullmatch(r"[A-Z]{3}")]
    df["DAY"] = df["DAY"].astype(int)
    for col in ("DEPARTURE_DELAY", "ARRIVAL_DELAY", "SCHEDULED_TIME", "DISTANCE"):
        df[col] = pd.to_numeric(df[col])
    df["CANCELLED"] = df["CANCELLED"].eq("1")
    df["DIVERTED"] = df["DIVERTED"].eq("1")
    return df.reset_index(drop=True)


def disrupted_airport_days(df):
    """{(reason, day, airport): (cancellations, rate)} for real cancellation spikes."""
    flights = df.groupby(["DAY", "ORIGIN_AIRPORT"]).size()
    cancelled = df[df["CANCELLED"] & df["CANCELLATION_REASON"].isin(EVENT_TYPES)]
    spikes = {}
    for (reason, day, airport), n in cancelled.groupby(["CANCELLATION_REASON", "DAY", "ORIGIN_AIRPORT"]).size().items():
        rate = n / flights[(day, airport)]
        if n >= EVENT_MIN_CANCELLATIONS and rate >= EVENT_MIN_RATE:
            spikes[(reason, day, airport)] = (int(n), rate)
    return spikes


def real_events(df, spikes, airports):
    """Merge spikes of one reason on consecutive days into DisruptionEvents."""
    events = []
    for reason, event_type in EVENT_TYPES.items():
        days = sorted({day for r, day, _ in spikes if r == reason})
        episodes = []
        for day in days:
            if episodes and day - episodes[-1][-1] <= 1:
                episodes[-1].append(day)
            else:
                episodes.append([day])
        for ep in episodes:
            keys = [k for k in spikes if k[0] == reason and k[1] in ep]
            codes = sorted({a for _, _, a in keys if a in airports})
            n = sum(spikes[k][0] for k in keys)
            worst = max(spikes[k][1] for k in keys)
            rows = df[df["CANCELLED"] & (df["CANCELLATION_REASON"] == reason)
                      & df["DAY"].isin(ep) & df["ORIGIN_AIRPORT"].isin(codes)]
            times = [local_time(d, t, airports[a]["timezone"]).astimezone(timezone.utc)
                     for d, t, a in zip(rows["DAY"], rows["SCHEDULED_DEPARTURE"], rows["ORIGIN_AIRPORT"])]
            span = f"{ep[0]}" + (f"-{ep[-1]}" if len(ep) > 1 else "")
            events.append({
                "event_id": f"EVT-{YEAR}-{MONTH:02d}{ep[0]:02d}-{event_type}",
                "airports": codes,
                "start_time": iso(min(times)),
                "end_time": iso(max(times)),
                "props": {
                    "event_type": event_type,
                    "severity": "CRITICAL" if worst >= 0.6 else "SEVERE" if worst >= 0.4 else "MODERATE",
                    "description": (f"{n} {EVENT_LABELS[event_type]} cancellations at "
                                    f"{', '.join(codes)} on {span} Jan {YEAR} (worst airport-day: "
                                    f"{worst:.0%} of departures cancelled)"),
                    "data_source": "BTS_2015",
                },
            })
    return events


class AircraftPicker:
    """Picks an :Aircraft type for a flight, consistent per tail number."""

    def __init__(self, eq, route_types, aircraft, rng):
        self.eq, self.route_types, self.rng = eq, route_types, rng
        self.passenger = {k for k, a in aircraft.items() if a["seating_capacity"] > 0}
        self.by_tail = {}
        self.fleets = {}

    def pick(self, carrier, origin, destination, tail=None):
        if tail and tail in self.by_tail:
            return self.by_tail[tail]
        types = (self.eq.by_airline.get((carrier, origin, destination), set()) & self.passenger
                 or set(self.route_types[(origin, destination)]))
        chosen = self.rng.choice(sorted(types))
        if tail:
            self.by_tail[tail] = chosen
        return chosen

    def tail_for(self, carrier, country, aircraft_type):
        """A synthetic registration from a small per-(carrier, type) pool."""
        pool = self.fleets.setdefault((carrier, aircraft_type), [])
        if len(pool) < TAILS_PER_FLEET:
            prefix = REGISTRATION_PREFIX.get(country, DEFAULT_REGISTRATION_PREFIX)
            pool.append(prefix + "".join(self.rng.choices(string.ascii_uppercase, k=3)))
        return self.rng.choice(pool)


def flight_row(flight_id, carrier, number, origin, destination, aircraft_type, tail,
               sched_dep, sched_arr, cancelled, diverted, dep_delay, arr_delay,
               distance_km, duration_min, source):
    dep_delay = None if cancelled or pd.isna(dep_delay) else int(dep_delay)
    arr_delay = None if cancelled or diverted or pd.isna(arr_delay) else int(arr_delay)
    return {
        "flight_id": flight_id, "airline_id": carrier, "origin": origin,
        "destination": destination, "aircraft_type_id": aircraft_type, "tail_number": tail,
        "scheduled_departure": iso(sched_dep),
        "scheduled_arrival": iso(sched_arr),
        "actual_departure": iso(None if dep_delay is None else sched_dep + timedelta(minutes=dep_delay)),
        "actual_arrival": iso(None if arr_delay is None else sched_arr + timedelta(minutes=arr_delay)),
        "props": {
            "flight_number": f"{carrier}{number}",
            "delay_minutes": dep_delay,
            "status": flight_status(cancelled, diverted, dep_delay),
            "distance_km": round(distance_km, 1),
            "duration_min": int(duration_min),
            "base_fare_usd": round(FARE_BASE_USD + FARE_PER_KM_USD * distance_km, 2),
            "data_source": source,
        },
    }


def real_flights(df, airports, route_types, airline_ids, picker):
    tz = {k for k, a in airports.items() if a["timezone"]}
    eligible = df[
        df["AIRLINE"].isin(airline_ids)
        & df["ORIGIN_AIRPORT"].isin(tz) & df["DESTINATION_AIRPORT"].isin(tz)
        & pd.Series([(o, d) in route_types for o, d in zip(df["ORIGIN_AIRPORT"], df["DESTINATION_AIRPORT"])],
                    index=df.index)
    ]
    # A flight number can fly several legs a day; keep one so flight_id
    # (carrier + number + date) stays unique.
    eligible = eligible.drop_duplicates(["AIRLINE", "FLIGHT_NUMBER", "DAY"])
    juno = eligible[eligible["DAY"].isin(JUNO_DAYS)
                    & (eligible["ORIGIN_AIRPORT"].isin(JUNO_AIRPORTS) | eligible["DESTINATION_AIRPORT"].isin(JUNO_AIRPORTS))]
    juno = juno.sample(JUNO_SAMPLE_SIZE, random_state=SEED)
    rest = eligible.drop(juno.index).sample(REAL_SAMPLE_SIZE - JUNO_SAMPLE_SIZE, random_state=SEED)
    sample = pd.concat([juno, rest]).sort_values(["DAY", "SCHEDULED_DEPARTURE", "AIRLINE", "FLIGHT_NUMBER"])

    rows = []
    for r in sample.itertuples(index=False):
        o, d = r.ORIGIN_AIRPORT, r.DESTINATION_AIRPORT
        sched_dep = local_time(r.DAY, r.SCHEDULED_DEPARTURE, airports[o]["timezone"])
        sched_arr = arrive(sched_dep, r.SCHEDULED_TIME, airports[d]["timezone"])
        tail = r.TAIL_NUMBER if pd.notna(r.TAIL_NUMBER) else None
        rows.append(flight_row(
            f"{r.AIRLINE}{r.FLIGHT_NUMBER}-{YEAR}{MONTH:02d}{r.DAY:02d}", r.AIRLINE, r.FLIGHT_NUMBER,
            o, d, picker.pick(r.AIRLINE, o, d, tail), tail, sched_dep, sched_arr,
            r.CANCELLED, r.DIVERTED, r.DEPARTURE_DELAY, r.ARRIVAL_DELAY,
            r.DISTANCE * MILES_TO_KM, r.SCHEDULED_TIME, "BTS_2015",
        ))
    return rows


def bucket_of(route, airports):
    src, dst = airports[route["src"]]["iso_country"], airports[route["dst"]]["iso_country"]
    if "IN" in (src, dst):
        return "India"
    if src == "US":
        return None  # the US is covered by real data
    if airports[route["src"]]["region"] == "Europe":
        return "Europe"
    if src in MIDDLE_EAST:
        return "Middle East"
    if src in EAST_SE_ASIA:
        return "East & Southeast Asia"
    return "Rest of world"


def synthetic_flights(routes, airports, airlines, df, spikes, picker, taken_ids, rng):
    """Synthetic flights per SYNTHETIC_QUOTAS, calibrated on the real rows."""
    disrupted_days = {(day, a) for _, day, a in spikes}
    outcome = list(zip(df["CANCELLED"], df["DIVERTED"], df["DEPARTURE_DELAY"], df["ARRIVAL_DELAY"]))
    is_disrupted = [(d, a) in disrupted_days for d, a in zip(df["DAY"], df["ORIGIN_AIRPORT"])]
    normal_pool = [x for x, bad in zip(outcome, is_disrupted) if not bad]
    disrupted_pool = [x for x, bad in zip(outcome, is_disrupted) if bad]
    hours = (pd.to_numeric(df["SCHEDULED_DEPARTURE"]) // 100).value_counts(normalize=True).sort_index()

    candidates = {b: [] for b in SYNTHETIC_QUOTAS}
    for r in routes:
        p = r["props"]
        carriers = [c for c in p["carriers"] if c in airlines]
        if (carriers and airports[r["src"]]["timezone"] and airports[r["dst"]]["timezone"]
                and (b := bucket_of(r, airports))):
            candidates[b].append((r, carriers))

    def make(route, carriers, day, hour):
        p = route["props"]
        carrier = rng.choice(carriers)
        while True:
            number = str(rng.randint(100, 9999))
            flight_id = f"{carrier}{number}-{YEAR}{MONTH:02d}{day:02d}"
            if flight_id not in taken_ids:
                taken_ids.add(flight_id)
                break
        o, d = route["src"], route["dst"]
        sched_dep = local_time(day, f"{hour:02d}{rng.randrange(0, 60, 5):02d}", airports[o]["timezone"])
        sched_arr = arrive(sched_dep, p["avg_duration_min"], airports[d]["timezone"])
        fog = o in FOG_AIRPORTS and day in FOG_DAYS and hour in FOG_HOURS
        cancelled, diverted, dep_delay, arr_delay = rng.choice(disrupted_pool if fog else normal_pool)
        # A long real flight can make up time in the air that a short one
        # can't; cap the make-up at 15% of this flight's duration so it
        # never lands before it took off.
        if pd.notna(dep_delay) and pd.notna(arr_delay):
            arr_delay = max(arr_delay, dep_delay - MAX_MAKEUP_SHARE * p["avg_duration_min"])
        aircraft_type = picker.pick(carrier, o, d)
        tail = picker.tail_for(carrier, airlines[carrier]["country"], aircraft_type)
        return flight_row(flight_id, carrier, number, o, d, aircraft_type, tail, sched_dep, sched_arr,
                          cancelled, diverted, dep_delay, arr_delay,
                          p["distance_km"], p["avg_duration_min"], "SYNTHETIC")

    rows = []
    # Fog scenario first, so the India demo is guaranteed to show it.
    fog_routes = [(r, c) for r, c in candidates["India"] if r["src"] in FOG_AIRPORTS]
    for _ in range(FOG_FLIGHTS):
        r, c = rng.choice(fog_routes)
        rows.append(make(r, c, rng.choice(FOG_DAYS), rng.choice(list(FOG_HOURS))))

    for bucket, quota in SYNTHETIC_QUOTAS.items():
        n = quota - (FOG_FLIGHTS if bucket == "India" else 0)
        pool = candidates[bucket]
        # Busier routes (more carriers) get more flights.
        picks = rng.choices(pool, weights=[len(c) for _, c in pool], k=n)
        for r, c in picks:
            rows.append(make(r, c, rng.randint(1, 31), int(rng.choices(hours.index, weights=hours.values)[0])))
    return rows


def fog_event(airports):
    start = datetime(YEAR, MONTH, FOG_DAYS[0], FOG_HOURS.start, tzinfo=ZoneInfo(FOG_TZ))
    end = datetime(YEAR, MONTH, FOG_DAYS[-1], FOG_HOURS.stop, tzinfo=ZoneInfo(FOG_TZ))
    codes = sorted(a for a in FOG_AIRPORTS if a in airports)
    return {
        "event_id": f"EVT-{YEAR}-{MONTH:02d}{FOG_DAYS[0]:02d}-FOG",
        "airports": codes,
        "start_time": iso(start),
        "end_time": iso(end),
        "props": {
            "event_type": "WEATHER",
            "severity": "SEVERE",
            "description": (f"Synthetic scenario: dense morning fog across North India ({', '.join(codes)}), "
                            f"{FOG_DAYS[0]}-{FOG_DAYS[-1]} Jan {YEAR}"),
            "data_source": "SYNTHETIC",
        },
    }


def report(flights, events, df, airports, route_types):
    print(f"Flight: {len(flights)} rows")
    src = Counter(f["props"]["data_source"] for f in flights)
    print("  by data_source:", dict(src))

    def bucket(f):
        if f["props"]["data_source"] == "BTS_2015":
            return "US (real)"
        return bucket_of({"src": f["origin"], "dst": f["destination"]}, airports)
    print("  by bucket:", dict(Counter(bucket(f) for f in flights)))

    def mix(rows):
        c = Counter(f["props"]["status"] for f in rows)
        return {k: f"{v / len(rows):.1%}" for k, v in sorted(c.items())}
    real = [f for f in flights if f["props"]["data_source"] == "BTS_2015"]
    synth = [f for f in flights if f["props"]["data_source"] == "SYNTHETIC"]
    fog = [f for f in synth if f["origin"] in FOG_AIRPORTS and f["scheduled_departure"][8:10] in
           {f"{d:02d}" for d in FOG_DAYS} and int(f["scheduled_departure"][11:13]) in FOG_HOURS]
    calm = [f for f in synth if f not in fog]
    all_rows = df.assign(status=[flight_status(c, dv, dl) for c, dv, dl in
                                 zip(df["CANCELLED"], df["DIVERTED"], df["DEPARTURE_DELAY"])])
    print("  status, all real Jan 2015 :", {k: f"{v:.1%}" for k, v in all_rows["status"].value_counts(normalize=True).sort_index().items()})
    print("  status, real sample       :", mix(real))
    print("  status, synthetic (no fog):", mix(calm))
    print(f"  status, synthetic fog ({len(fog)}) :", mix(fog))

    problems = Counter()
    for f in flights:
        if (f["origin"], f["destination"]) not in route_types:
            problems["no ROUTE edge"] += 1
        for k in ("scheduled_departure", "scheduled_arrival", "actual_departure", "actual_arrival"):
            v = f[k]
            if v is not None and not (v.endswith("Z") or v[-6] in "+-"):
                problems[f"{k} without UTC offset"] += 1
        if datetime.fromisoformat(f["scheduled_arrival"]) <= datetime.fromisoformat(f["scheduled_departure"]):
            problems["scheduled arrival not after departure"] += 1
        if f["actual_arrival"] and datetime.fromisoformat(f["actual_arrival"]) <= datetime.fromisoformat(f["actual_departure"]):
            problems["actual arrival not after departure"] += 1
    print("  problems:", dict(problems) or "none")
    print(f"  unique flight_id: {len({f['flight_id'] for f in flights}) == len(flights)}")

    print(f"DisruptionEvent: {len(events)}")
    for e in events:
        print(f"  {e['event_id']} [{e['props']['data_source']}] {e['props']['severity']}: {e['props']['description']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="parse source data and report, without touching Neo4j")
    args = parser.parse_args()
    rng = random.Random(SEED)

    airports = {r["iata_code"]: r for r in read_airports()}
    aircraft = {r["aircraft_type_id"]: r for r in read_aircraft()}
    airlines = {r["IATA"]: r for r in read_airlines()}
    json_routes, _, _ = read_json_routes(airports)
    eq = read_equipment(read_iata_to_icao(aircraft.keys()), airports)
    routes, _ = build_routes(json_routes, airports, aircraft, eq)
    route_types = {(r["src"], r["dst"]): r["props"]["aircraft_types"] for r in routes}

    df = read_real_flights()
    spikes = disrupted_airport_days(df)
    picker = AircraftPicker(eq, route_types, aircraft, rng)

    flights = real_flights(df, airports, route_types, airlines.keys(), picker)
    flights += synthetic_flights(routes, airports, airlines, df, spikes, picker,
                                 {f["flight_id"] for f in flights}, rng)
    events = real_events(df, spikes, airports) + [fog_event(airports)]
    report(flights, events, df, airports, route_types)

    if args.dry_run:
        for label, pick in (("real Juno", lambda f: f["props"]["data_source"] == "BTS_2015" and f["origin"] in JUNO_AIRPORTS
                             and f["scheduled_departure"].startswith(f"{YEAR}-{MONTH:02d}-27")),
                            ("synthetic fog", lambda f: f["origin"] in FOG_AIRPORTS and f["props"]["status"] != "COMPLETED")):
            print(f"Flight sample ({label}):", next((f for f in flights if pick(f)), None))
        return

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt).consume()
            # The synthetic sample depends on the input data (e.g. which
            # airlines exist), so replace it rather than MERGE a new set
            # next to a stale one. Real BTS flights are stable and merged.
            session.run("MATCH (f:Flight {data_source: 'SYNTHETIC'}) DETACH DELETE f").consume()
            load(session, MERGE_FLIGHTS, flights)
            load(session, MERGE_EVENTS, events)
            n_flights = session.run("MATCH (f:Flight) RETURN count(f) AS n").single()["n"]
            n_events = session.run("MATCH (e:DisruptionEvent) RETURN count(e) AS n").single()["n"]
    print(f"Flight: {n_flights} nodes in Neo4j")
    print(f"DisruptionEvent: {n_events} nodes in Neo4j")


if __name__ == "__main__":
    main()
