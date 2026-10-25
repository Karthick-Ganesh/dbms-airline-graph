"""Air traffic for the Live Traffic page: a simulated live view and a 2015 replay.

Live (simulated): our data has no live positions, so a repeatable daily
timetable is generated from the ACTIVE ROUTE edges. Every (route, carrier)
pair gets one departure a day at a fixed local time between 06:00 and 23:30
at the origin (crc32 of the pair, so it never changes between runs); the
flight takes the route's avg_duration_min. At any moment that puts roughly
ten thousand flights in the air, similar to real traffic. Because only ACTIVE
airports and corridors are used, a simulated closure empties the sky there.

Replay: the real Flight nodes (January 2015, BTS + synthetic) for one day,
with scheduled and actual times and their status, for the browser to animate.
"""

import threading
import zlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY = 86400
FIRST_LOCAL_DEP = 6 * 60          # 06:00 local
LAST_LOCAL_DEP = 23 * 60 + 30     # 23:30 local
LOOKAHEAD_S = 120                 # also send flights departing in the next 2 minutes

ACTIVE_ROUTES = """
MATCH (a:Airport {operational_status: 'ACTIVE'})-[r:ROUTE {corridor_status: 'ACTIVE'}]->
      (b:Airport {operational_status: 'ACTIVE'})
RETURN a.airport_id AS src, b.airport_id AS dst, r.carriers AS carriers, r.avg_duration_min AS minutes
"""

# Origin timezones, fetched once per airport rather than once per route.
ORIGIN_ZONES = """
MATCH (a:Airport {operational_status: 'ACTIVE'}) WHERE EXISTS { (a)-[:ROUTE]->() }
RETURN a.airport_id AS code, a.timezone AS tz, a.location.longitude AS lon
"""

AIRPORT_COORDS = """
UNWIND $codes AS code
MATCH (a:Airport {airport_id: code})
RETURN a.airport_id AS code, a.location.latitude AS lat, a.location.longitude AS lon, a.city AS city
"""

REPLAY_DAY = """
MATCH (al:Airline)-[:OPERATES]->(f:Flight)-[:DEPARTS_FROM]->(o:Airport), (f)-[:ARRIVES_AT]->(d:Airport)
WHERE f.scheduled_departure >= datetime($start) AND f.scheduled_departure < datetime($end)
RETURN f.flight_id AS id, f.flight_number AS flight, al.airline_id AS carrier, al.name AS airline,
       o.airport_id AS src, d.airport_id AS dst, f.status AS status, f.delay_minutes AS delay,
       f.data_source AS source,
       f.scheduled_departure.epochSeconds AS sched_dep, f.scheduled_arrival.epochSeconds AS sched_arr,
       coalesce(f.actual_departure, f.scheduled_departure).epochSeconds AS dep,
       coalesce(f.actual_arrival, f.scheduled_arrival).epochSeconds AS arr
ORDER BY dep
"""

_lock = threading.Lock()
_timetable = None   # list of (code, carrier, src, dst, dep_utc_minute, duration_s)


def _utc_offset_min(tz_name, lon, today):
    """Origin's UTC offset today; from the longitude if the timezone is unknown."""
    if tz_name:
        try:
            off = datetime.combine(today, datetime.min.time(), ZoneInfo(tz_name)).utcoffset()
            return int(off.total_seconds() // 60)
        except ZoneInfoNotFoundError:
            pass
    return round((lon or 0) / 15) * 60


def _build(driver):
    today = datetime.now(timezone.utc).date()
    span = LAST_LOCAL_DEP - FIRST_LOCAL_DEP
    rows, by_zone = [], {}
    with driver.session() as session:
        offset = {}
        for a in session.run(ORIGIN_ZONES):
            zone = a["tz"] or f"lon:{a['lon']}"
            if zone not in by_zone:
                by_zone[zone] = _utc_offset_min(a["tz"], a["lon"], today)
            offset[a["code"]] = by_zone[zone]
        for r in session.run(ACTIVE_ROUTES):
            for carrier in r["carriers"] or []:
                h = zlib.crc32(f"{r['src']}{r['dst']}{carrier}".encode())
                local = FIRST_LOCAL_DEP + h % span
                code = f"{carrier}{100 + (h >> 12) % 8900}"
                rows.append((code, carrier, r["src"], r["dst"], (local - offset.get(r["src"], 0)) % 1440,
                             int(r["minutes"] * 60)))
    return rows


def timetable(driver):
    """The cached timetable, built on first use (a few seconds: 56k routes)."""
    global _timetable
    with _lock:
        if _timetable is None:
            _timetable = _build(driver)
        return _timetable


def warm(driver):
    """Build the timetable in the background so the first page load doesn't wait."""
    threading.Thread(target=timetable, args=(driver,), daemon=True).start()


def invalidate(driver=None):
    """Call after anything changes the ACTIVE network (closures, route edits);
    with a driver, the new timetable is rebuilt in the background straight away."""
    global _timetable
    with _lock:
        _timetable = None
    if driver is not None:
        warm(driver)


def live(driver, at=None):
    """Flights in the air at `at` (UTC datetime, default now), plus those about to depart.

    Returns {"now", "flights": [[code, carrier, src, dst, dep, arr], ...],
    "airports": {code: [lat, lon, city]}, "scheduled_per_day"} with epoch seconds.
    """
    now = int((at or datetime.now(timezone.utc)).timestamp())
    midnight = now - now % DAY
    flights = []
    for code, carrier, src, dst, dep_min, dur in timetable(driver):
        for day_start in (midnight - DAY, midnight):   # yesterday's departures may still be flying
            dep = day_start + dep_min * 60
            arr = dep + dur
            if dep <= now + LOOKAHEAD_S and arr > now:
                flights.append([code, carrier, src, dst, dep, arr])
    codes = sorted({f[2] for f in flights} | {f[3] for f in flights})
    with driver.session() as session:
        airports = {r["code"]: [round(r["lat"], 4), round(r["lon"], 4), r["city"]]
                    for r in session.run(AIRPORT_COORDS, codes=codes)}
    return {"now": now, "flights": flights, "airports": airports,
            "scheduled_per_day": len(timetable(driver))}


def replay(driver, day):
    """The real Flight nodes departing on `day` (UTC), for animation in the browser."""
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), timezone.utc)
    with driver.session() as session:
        flights = session.run(REPLAY_DAY, start=start.isoformat(),
                              end=(start + timedelta(days=1)).isoformat()).data()
        codes = sorted({f["src"] for f in flights} | {f["dst"] for f in flights})
        airports = {r["code"]: [round(r["lat"], 4), round(r["lon"], 4), r["city"]]
                    for r in session.run(AIRPORT_COORDS, codes=codes)}
    return {"day": day, "start": int(start.timestamp()), "flights": flights, "airports": airports}
