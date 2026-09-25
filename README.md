# Graph-Based Airline Route Optimization for Disruption Analysis and Sustainable Travel

BCSE302P Database Systems Lab project. A Neo4j property graph of the world's airline network (airports, airlines, aircraft, engines, flights, disruptions). Graph Data Science algorithms on this graph find critical hubs, simulate airport and corridor closures, and compare the **fastest** route with the **lowest-CO2-per-seat** route.

## Graph model

| Node | Key | Count (approx.) | Source |
|---|---|---|---|
| `Airport` | `airport_id` (IATA) | 9,100 | OurAirports + runways |
| `Airline` | `airline_id` (IATA) | 1,100 | OpenFlights |
| `Aircraft` | `aircraft_type_id` (ICAO) | 106 | commercial aircraft dataset |
| `Engine` | `engine_uid` | 888 | ICAO Aircraft Engine Emissions Databank v32 |
| `Flight` | `flight_id` | 1,000 | Kaggle 2015 Flight Delays (US) + synthetic |
| `DisruptionEvent` | `event_id` | 3+ | detected from cancellation spikes + scenario |

| Relationship | Meaning |
|---|---|
| `(:Airport)-[:ROUTE]->(:Airport)` | ~56,600 directed routes, each with `weight_time`, `weight_carbon` (CO2 per seat) and `weight_cost` |
| `(:Airport)-[:HUB_OF]->(:Airline)` | airline hubs derived from route data |
| `(:Airline)-[:OPERATES]->(:Flight)` | who flies the flight |
| `(:Flight)-[:DEPARTS_FROM / :ARRIVES_AT]->(:Airport)` | the flight's airports |
| `(:Flight)-[:USES_AIRCRAFT {tail_number}]->(:Aircraft)` | the aircraft type used |
| `(:Aircraft)-[:POWERED_BY {is_default_engine}]->(:Engine)` | certified engine options |
| `(:DisruptionEvent)-[:AFFECTS]->(:Airport)` | disruption audit trail |

The full schema and design rationale are in [`dbms_review_transcription.md`](dbms_review_transcription.md).

## Setup

Requirements: Python 3.10+, and Neo4j 5.x/2025+ with the **Graph Data Science** plugin (Neo4j Desktop: open the DBMS, go to Plugins, and install Graph Data Science).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows (use .venv/bin/python on macOS/Linux)
```

Create a `.env` file (git-ignored) in the project root:

```
NEO4J_PASSWORD=your-password
# optional: NEO4J_URI=bolt://localhost:7687, NEO4J_USER=neo4j
```

**Data files:** everything is in the repo except `flights.csv` (592 MB, over GitHub's file limit). Download it from Kaggle's [2015 Flight Delays and Cancellations](https://www.kaggle.com/datasets/usdot/flight-delays) dataset and put `flights.csv` in the project root.

## Loading the graph

Run the seed scripts in order. Each is idempotent (MERGE on unique keys), and each supports `--dry-run`, which parses the data and prints a report without touching Neo4j.

```bash
.venv/Scripts/python seed.py     # Engine, Airport, Airline + unique constraints
.venv/Scripts/python seed2.py    # Aircraft
.venv/Scripts/python seed3.py    # ROUTE, HUB_OF
.venv/Scripts/python seed4.py    # Flight (+4 relationship types), DisruptionEvent (+AFFECTS)
.venv/Scripts/python seed5.py    # POWERED_BY, indexes, existence/type constraints
```

## Using it

- `crud.py`: node and relationship CRUD functions (Airport, Airline, Flight, DisruptionEvent, ROUTE, HUB_OF, USES_AIRCRAFT).
- `queries.py`: 12 analytical queries, including graph traversals, GDS Dijkstra (fastest vs greenest), Betweenness, PageRank, aggregations and a spatial search, plus a disruption-simulation transaction demo.
- `review2_demo.py`: runs the whole walkthrough.

```bash
.venv/Scripts/python review2_demo.py             # overview, CRUD cycle, 12 queries, transactions
.venv/Scripts/python review2_demo.py queries 4   # a single query
```

## Data notes

- **Real vs synthetic:** each `Flight` and `DisruptionEvent` has `data_source` = `BTS_2015` (real US flights, January 2015, including Winter Storm Juno) or `SYNTHETIC` (India and the rest of the world: real routes, carriers and aircraft, with schedules and delays sampled from the real data).
- **Synthetic values:** `avg_fuel_burn_kg_per_km` (aircraft), `weight_cost` (a distance-based fare) and `fleet_size` are generated with fixed random seeds, so every run gives the same values.
- **Route aircraft:** a ROUTE's `aircraft_types` comes from the most direct evidence available, recorded in `fuel_burn_source`. It is either the airline's recorded equipment on that route, any airline's on that airport pair, the carrier's fleet on similar distances, or a distance band.
