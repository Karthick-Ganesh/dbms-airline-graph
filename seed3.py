"""Seed ROUTE edges and HUB_OF relationships.

Joins airline_routes.json (which routes exist today, with km, minutes and
carriers) with the OpenFlights routes.csv (aircraft equipment per route),
using planes.csv to map IATA equipment codes to our ICAO :Aircraft ids.

Run after seed.py and seed2.py; uses the same NEO4J_* settings.
"""

import argparse
import bisect
import math
import random
from collections import Counter, defaultdict

import pandas as pd

import statistics

from seed import (AIRPORT_MISMATCH_KM, OPENFLIGHTS_NULL, ROOT, ROUTES_JSON_FILE,
                  _read_csv, haversine_km, load, mismatched_airports, neo4j_driver,
                  read_airlines, read_airports, read_engines, read_json)
from seed2 import read_aircraft
from seed5 import build_powered_by, read_engine_records

ROUTES_CSV_FILE = ROOT / "routes.csv"
PLANES_FILE = ROOT / "planes.csv"

CO2_KG_PER_KG_FUEL = 3.16

# routes.csv equipment codes that planes.csv lacks (family/generic codes) or
# maps to a type we don't model -> the representative :Aircraft id.
IATA_EQUIPMENT_ALIASES = {
    "737": "B738", "73H": "B738", "73W": "B737", "73C": "B733", "73J": "B739", "73M": "B732",
    "757": "B752", "75W": "B752", "767": "B763", "76W": "B763",
    "777": "B77W", "787": "B788", "747": "B744", "74E": "B744", "74M": "B744",
    "32S": "A320", "32A": "A320", "32B": "A321",
    "330": "A333", "340": "A343", "380": "A388", "313": "A310", "AB4": "A306",
    "CRJ": "CRJ2", "CRA": "CRJ7", "ERJ": "E145", "EMJ": "E190", "E75": "E75L",
    "DH8": "DH8D", "M80": "MD82", "146": "B462", "BE1": "B190", "CNC": "C208",
}
# planes.csv ICAO codes for variants we don't model -> nearest :Aircraft id.
ICAO_ALIASES = {
    "AT72": "AT75", "AT43": "AT45", "B773": "B77W", "B762": "B763", "A342": "A343",
    "A345": "A346", "B461": "B462", "B463": "RJ1H", "B74S": "B744",
}

# Last-resort aircraft mix when no equipment is known: (max km, types).
DISTANCE_BAND_MIX = [
    (600, ["AT76", "DH8D", "CRJ9", "E75L"]),
    (4500, ["A320", "B738"]),
    (math.inf, ["B77W", "B789", "A359"]),
]

# JSON km is a great-circle figure (median 0.16% off haversine); beyond this
# it's a data error and haversine from our own coordinates is used instead.
DISTANCE_TOLERANCE = 0.05
# Block-time model fitted on the JSON's clean routes (median error ~6%);
# replaces durations that are physically impossible.
BLOCK_TIME_FIXED_MIN = 46.2
BLOCK_TIME_MIN_PER_KM = 0.0725
MAX_SPEED_KMH = 1000
MAX_DURATION_RATIO = 2.5
# Neither source has fares: synthetic distance-based fare, USD.
FARE_BASE_USD = 50.0
FARE_PER_KM_USD = 0.10
CONGESTION_SEED = 302

# CARRIER_FLEET fallback: a carrier's type counts for a route if the carrier
# flew it (in routes.csv) on some route within this factor of the distance.
FLEET_DISTANCE_FACTOR = 1.5

# An airport is a hub of an airline if it's one of the airline's top origins
# by route count, with at least this fraction of the busiest origin's routes.
# (A share of *all* routes doesn't work: AA's DFW is only 9% of AA's routes.)
HUB_TOP_N = 5
HUB_MIN_RATIO_TO_TOP = 0.30
HUB_MIN_ROUTES = 5

MERGE_ROUTES = """
UNWIND $rows AS row
MATCH (a:Airport {airport_id: row.src})
MATCH (b:Airport {airport_id: row.dst})
MERGE (a)-[r:ROUTE]->(b)
SET r += row.props,
    // Keep any simulated disruption across re-seeds.
    r.corridor_status = coalesce(r.corridor_status, 'ACTIVE'),
    r.last_updated = datetime()
"""

MERGE_HUBS = """
UNWIND $rows AS row
MATCH (a:Airport {airport_id: row.airport_id})
MATCH (al:Airline {airline_id: row.airline_id})
MERGE (a)-[:HUB_OF]->(al)
"""


def block_time_min(km):
    return BLOCK_TIME_FIXED_MIN + BLOCK_TIME_MIN_PER_KM * km


def lto_fuel_by_aircraft(aircraft):
    """aircraft_type_id -> fuel (kg) burned in one landing/take-off cycle.

    Default engine's ICAO LTO fuel x engine_count. Types without a databank
    engine (turboprops, a few jets) get their cruise burn times the median
    ratio of the known ones (~270 km of cruise per LTO cycle). Charging this
    once per leg is what makes an extra stop cost carbon.
    """
    engines = {e["engine_uid"]: e for e in read_engines()}
    rows, _, _ = build_powered_by(aircraft.keys(), read_engine_records())
    lto = {}
    for r in rows:
        fuel = engines.get(r["engine_uid"], {}).get("fuel_lto_cycle_kg")
        if r["is_default_engine"] and fuel:
            lto[r["aircraft_type_id"]] = fuel * aircraft[r["aircraft_type_id"]]["engine_count"]
    km_equivalent = statistics.median(v / aircraft[k]["avg_fuel_burn_kg_per_km"] for k, v in lto.items())
    return {k: lto.get(k, a["avg_fuel_burn_kg_per_km"] * km_equivalent) for k, a in aircraft.items()}


def read_json_routes(airports, path=ROUTES_JSON_FILE):
    """One row per directed airport pair: km, min, carriers.

    Routes touching an airport whose JSON location disagrees with our node
    (see seed.AIRPORT_MISMATCH_KM) are skipped rather than attached to the
    wrong airport. Returns (DataFrame, Counter of skipped route entries by
    reason, {code: details} of mismatched airports).
    """
    js = read_json(path)
    mismatched = mismatched_airports(js, airports)
    rows = [
        (src, r["iata"], r.get("km"), r.get("min"),
         [c["iata"] for c in r.get("carriers") or [] if c.get("iata")])
        for src, a in js.items()
        for r in a.get("routes") or []
    ]
    df = pd.DataFrame(rows, columns=["src", "dst", "km", "min", "carriers"])
    unknown = ~(df["src"].isin(airports.keys()) & df["dst"].isin(airports.keys()))
    clash = ~unknown & (df["src"].isin(mismatched.keys()) | df["dst"].isin(mismatched.keys()))
    self_loop = df["src"] == df["dst"]  # e.g. ACC->ACC
    skipped = Counter(unknown_airport=int(unknown.sum()), mismatched_airport=int(clash.sum()),
                      self_loop=int((self_loop & ~unknown & ~clash).sum()))
    known = ~unknown & ~clash & ~self_loop

    # A few pairs are listed twice; merge them.
    df = df[known].groupby(["src", "dst"], as_index=False).agg(
        km=("km", "median"),
        min=("min", "median"),
        carriers=("carriers", lambda cs: sorted({c for lst in cs for c in lst})),
    )
    return df, skipped, mismatched


def read_iata_to_icao(aircraft_ids, path=PLANES_FILE):
    """IATA equipment code -> :Aircraft id, for codes we can resolve."""
    planes = _read_csv(path, header=None, names=["name", "iata", "icao"])
    planes = planes[(planes["iata"] != OPENFLIGHTS_NULL) & (planes["icao"] != OPENFLIGHTS_NULL)]
    planes = planes.assign(icao=planes["icao"].replace(ICAO_ALIASES))

    # Some IATA codes cover several types (CN1 = Cessna 172/182/208/210);
    # prefer the one we model.
    mapping = {}
    for iata, icao in zip(planes["iata"], planes["icao"]):
        if icao in aircraft_ids and mapping.get(iata) not in aircraft_ids:
            mapping[iata] = icao
    mapping.update(IATA_EQUIPMENT_ALIASES)
    return {k: v for k, v in mapping.items() if v in aircraft_ids}


class Equipment:
    """Aircraft seen in routes.csv, indexed for the ROUTE fallback chain."""

    def __init__(self):
        self.by_airline = defaultdict(set)        # (airline, src, dst) -> types
        self.by_pair = defaultdict(set)           # {src, dst} -> types
        self.fleet_km = defaultdict(list)         # (airline, type) -> sorted route km
        self.fleet = defaultdict(set)             # airline -> types
        self.unresolved = Counter()               # equipment code -> mentions

    def carrier_fleet(self, carriers, km, aircraft):
        """Types these carriers flew on routes of similar length, with the range for this one."""
        lo, hi = km / FLEET_DISTANCE_FACTOR, km * FLEET_DISTANCE_FACTOR
        types = set()
        for c in carriers:
            for t in self.fleet.get(c, ()):
                if aircraft[t]["max_range_km"] < km:
                    continue
                flown = self.fleet_km[(c, t)]
                i = bisect.bisect_left(flown, lo)
                if i < len(flown) and flown[i] <= hi:
                    types.add(t)
        return types


def read_equipment(iata_to_icao, airports, path=ROUTES_CSV_FILE):
    df = _read_csv(path)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={"source airport": "src", "destination apirport": "dst"})
    # Codeshare rows duplicate the operating carrier's row.
    df = df[df["codeshare"].ne("Y") & df["equipment"].notna()]

    eq = Equipment()
    for airline, src, dst, equipment in df[["airline", "src", "dst", "equipment"]].itertuples(index=False):
        types = set()
        for code in str(equipment).split():
            if code in iata_to_icao:
                types.add(iata_to_icao[code])
            else:
                eq.unresolved[code] += 1
        if not types:
            continue
        eq.by_airline[(airline, src, dst)] |= types
        # The return leg is almost always flown by the same aircraft.
        eq.by_pair[frozenset((src, dst))] |= types
        if src in airports and dst in airports:
            a, b = airports[src], airports[dst]
            km = haversine_km(a["latitude_deg"], a["longitude_deg"], b["latitude_deg"], b["longitude_deg"])
            eq.fleet[airline] |= types
            for t in types:
                eq.fleet_km[(airline, t)].append(km)
    for flown in eq.fleet_km.values():
        flown.sort()
    return eq


def build_routes(json_routes, airports, aircraft, eq, lto=None):
    """Return (ROUTE rows, stats Counter)."""
    lto = lto or lto_fuel_by_aircraft(aircraft)
    passenger = {k: a for k, a in aircraft.items() if a["seating_capacity"] > 0}
    rng = random.Random(CONGESTION_SEED)
    stats = Counter()
    rows = []

    for src, dst, json_km, json_min, carriers in json_routes.sort_values(["src", "dst"]).itertuples(index=False):
        a, b = airports[src], airports[dst]
        gc_km = haversine_km(a["latitude_deg"], a["longitude_deg"], b["latitude_deg"], b["longitude_deg"])
        # NaN is truthy, so test missing values explicitly.
        if pd.notna(json_km) and json_km > 0 and abs(json_km - gc_km) <= DISTANCE_TOLERANCE * max(gc_km, 1):
            km = float(json_km)
        else:
            km = gc_km
            stats["distance_from_haversine"] += 1

        duration = json_min
        duration_estimated = (
            pd.isna(duration)
            or duration <= 0
            or km / (duration / 60) > MAX_SPEED_KMH
            or duration / block_time_min(km) > MAX_DURATION_RATIO
        )
        if duration_estimated:
            duration = block_time_min(km)
            stats["duration_estimated"] += 1
        duration = int(round(duration))

        # Most to least specific evidence of which aircraft fly this route.
        mix = set().union(*(eq.by_airline.get((c, src, dst), set()) for c in carriers)) & passenger.keys()
        source = "EQUIPMENT_AIRLINE"
        if not mix:
            mix = eq.by_pair.get(frozenset((src, dst)), set()) & passenger.keys()
            source = "EQUIPMENT_ROUTE"
        if not mix:
            mix = eq.carrier_fleet(carriers, km, aircraft) & passenger.keys()
            source = "CARRIER_FLEET"
        if not mix:
            band = next(types for max_km, types in DISTANCE_BAND_MIX if km < max_km)
            mix = ({t for t in band if aircraft[t]["max_range_km"] >= km}
                   or {max(band, key=lambda t: aircraft[t]["max_range_km"])})
            source = "DISTANCE_BAND"
        stats[source] += 1

        mix = sorted(mix)
        # Cruise fuel for the distance plus one landing/take-off cycle.
        leg_fuel = {t: km * passenger[t]["avg_fuel_burn_kg_per_km"] + lto[t] for t in mix}
        fuel_kg = sum(leg_fuel.values()) / len(mix)
        co2_per_seat = sum(
            leg_fuel[t] * CO2_KG_PER_KG_FUEL / passenger[t]["seating_capacity"] for t in mix
        ) / len(mix)
        fare = FARE_BASE_USD + FARE_PER_KM_USD * km

        rows.append({"src": src, "dst": dst, "props": {
            "distance_km": round(km, 1),
            "avg_duration_min": duration,
            "avg_speed_kmh": round(km / (duration / 60), 1),
            "fuel_burn_estimate_kg": round(fuel_kg, 1),
            "co2_emissions_kg": round(fuel_kg * CO2_KG_PER_KG_FUEL, 1),
            "co2_per_seat_kg": round(co2_per_seat, 2),
            "weight_time": float(duration),
            "weight_carbon": round(co2_per_seat, 2),
            "weight_cost": round(fare, 2),
            "aircraft_types": mix,
            "carriers": list(carriers),
            "fuel_burn_source": source,
            "duration_estimated": bool(duration_estimated),
            "congestion_index": round(rng.random(), 3),
        }})
    return rows, stats


def derive_hubs(json_routes, airline_ids):
    """HUB_OF rows: each airline's top origin airports by route count."""
    counts = Counter(
        (carrier, src)
        for src, carriers in zip(json_routes["src"], json_routes["carriers"])
        for carrier in carriers
        if carrier in airline_ids
    )
    per_airline = defaultdict(list)
    for (carrier, src), n in counts.items():
        per_airline[carrier].append((n, src))

    hubs = []
    for carrier, origins in per_airline.items():
        top = sorted(origins, key=lambda x: (-x[0], x[1]))[:HUB_TOP_N]
        busiest = top[0][0]
        hubs += [{"airport_id": src, "airline_id": carrier}
                 for n, src in top
                 if n >= HUB_MIN_ROUTES and n >= HUB_MIN_RATIO_TO_TOP * busiest]
    return hubs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="parse source data and report, without touching Neo4j")
    args = parser.parse_args()

    airports = {r["iata_code"]: r for r in read_airports()}
    aircraft = {r["aircraft_type_id"]: r for r in read_aircraft()}
    airline_ids = {r["IATA"] for r in read_airlines()}

    json_routes, skipped, mismatched = read_json_routes(airports)
    iata_to_icao = read_iata_to_icao(aircraft.keys())
    eq = read_equipment(iata_to_icao, airports)
    routes, stats = build_routes(json_routes, airports, aircraft, eq)
    hubs = derive_hubs(json_routes, airline_ids)

    n = len(routes)
    print(f"ROUTE: {n} airport pairs")
    print(f"  JSON entries skipped: {skipped['unknown_airport']} (airport not an :Airport), "
          f"{skipped['mismatched_airport']} (JSON airport >{AIRPORT_MISMATCH_KM} km from our node "
          f"for {len(mismatched)} codes: {', '.join(sorted(mismatched))}), "
          f"{skipped['self_loop']} (same origin and destination)")
    for key in ("EQUIPMENT_AIRLINE", "EQUIPMENT_ROUTE", "CARRIER_FLEET", "DISTANCE_BAND"):
        print(f"  aircraft from {key}: {stats[key]} ({100 * stats[key] / n:.1f}%)")
    print(f"  distance from haversine (JSON km missing or >{DISTANCE_TOLERANCE:.0%} off): "
          f"{stats['distance_from_haversine']}")
    print(f"  duration estimated (missing or impossible in JSON): {stats['duration_estimated']}")
    print(f"  unresolved equipment codes (top 10): {eq.unresolved.most_common(10)}")
    print(f"HUB_OF: {len(hubs)} relationships")

    if args.dry_run:
        by_key = {(r["src"], r["dst"]): r["props"] for r in routes}
        for pair in (("JFK", "LHR"), ("BOM", "DEL")):
            print(f"ROUTE sample {pair[0]}->{pair[1]}:", by_key.get(pair))
        for carrier in ("BA", "AA", "6E"):
            print(f"HUB_OF sample {carrier}:", sorted(h["airport_id"] for h in hubs if h["airline_id"] == carrier))
        return

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            load(session, MERGE_ROUTES, routes)
            load(session, MERGE_HUBS, hubs)
            n_routes = session.run("MATCH ()-[r:ROUTE]->() RETURN count(r) AS n").single()["n"]
            n_hubs = session.run("MATCH ()-[h:HUB_OF]->() RETURN count(h) AS n").single()["n"]
    print(f"ROUTE: {n_routes} edges in Neo4j")
    print(f"HUB_OF: {n_hubs} relationships in Neo4j")


if __name__ == "__main__":
    main()
