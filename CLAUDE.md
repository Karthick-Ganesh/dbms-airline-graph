# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This is a course project for BCSE302P (Database Systems Lab): **Graph-Based Airline Route Optimization for Disruption Analysis and Sustainable Travel**, built on **Neo4j** and its Graph Data Science (GDS) library. The code is a set of seed scripts, a CRUD module (`crud.py`), the analytical queries (`queries.py`) and a Review 2 walkthrough (`review2_demo.py`). There is no web UI (planned for Review 3: a FastAPI backend reusing `crud.py` and `queries.py`) and no test suite. The data is loaded in the local Neo4j Desktop DBMS "DBMS Airlines", in its default `neo4j` database, with the GDS plugin installed.

## Commands

Use the project venv. The system-wide Python's pandas is broken by a numpy binary mismatch.

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python seed.py --dry-run   # parse the source data only, no DB needed
.venv/Scripts/python seed.py             # needs NEO4J_PASSWORD; optional NEO4J_URI, NEO4J_USER
.venv/Scripts/python seed2.py            # :Aircraft nodes (also accepts --dry-run)
.venv/Scripts/python seed3.py            # ROUTE + HUB_OF; run after seed.py and seed2.py (--dry-run prints a match report)
.venv/Scripts/python seed4.py            # Flight + DisruptionEvent (--dry-run prints status mix and checks)
.venv/Scripts/python seed5.py            # POWERED_BY + indexes + existence/type constraints
.venv/Scripts/python review2_demo.py     # overview, CRUD cycle, 12 queries, transaction demo
.venv/Scripts/python review2_demo.py queries 4   # one query; parts: overview | crud | queries | tx
```

`NEO4J_PASSWORD` comes from the environment or from a git-ignored `.env` file (`seed._load_dotenv`).

- `crud.py`: one managed transaction per function. Airports are soft-deleted with `close_airport` (sets `operational_status`). `delete_airport` is for test data only. Datetimes are passed in as ISO strings and converted with `datetime()` in Cypher. `plain()` turns Neo4j values into printable or JSON values.
- `queries.py`: GDS queries run on the in-memory graph `active_network`, which `project_active_network` builds from ACTIVE airports and ACTIVE ROUTE edges only (Cypher aggregation projection). Re-project after any status change. `disruption_demo` tags the corridors it disrupts with `disrupted_by`, so the restore step touches only those. Its demo event `EVT-DEMO-DXB-CLOSURE` is kept, with an `end_time`, as an audit record.
- `seed5.py`: `AIRCRAFT_ENGINES` is a hand-written mapping from aircraft type to engine-model prefixes. The emissions databank has no turboprops, and a few engines (GE9X, D-18T, Trent 7000, Olympus...) are missing from it, so about 25 types have no POWERED_BY link.

The seed scripts are idempotent: they use `MERGE` on each label's unique key, and they create the constraints before loading. Run them in order. `seed.py` loads Engine, Airport and Airline. `seed2.py` loads Aircraft. `seed3.py` loads ROUTE and HUB_OF. `seed4.py` loads Flight (with OPERATES, DEPARTS_FROM, ARRIVES_AT and USES_AIRCRAFT) and DisruptionEvent (with AFFECTS). `seed4.py` rebuilds the route mixes in memory with `seed3`'s functions rather than reading them back from Neo4j, so `--dry-run` needs no database. The later scripts import the CSV, batching, connection and haversine helpers from `seed.py`. Any randomly generated property (`fleet_size`, `avg_fuel_burn_kg_per_km`, `congestion_index`) uses a fixed seed so re-runs give the same values. Re-seeding never resets `Airport.operational_status` or `ROUTE.corridor_status` (both are set with `coalesce`), so a simulated disruption survives a re-run.

Source data files:
- `edb-emissions-databank_v32__web_.xlsx`: the ICAO Engine Emissions Databank, the source for `:Engine` nodes. Only 6 of its roughly 105 columns in sheet `Gaseous Emissions and Smoke` are used (see `ENGINE_COLUMNS` in `seed.py`). Some headers have trailing spaces, so headers are stripped before columns are selected.
- `airports_new.csv` and `runways.csv` (OurAirports): the source for `:Airport` nodes. `runway_count` is the number of runway rows per `airport_ident`, matched to `airports.ident`.
- `airlines.csv` (OpenFlights `airlines.dat`, no header row, `\N` means null): the source for `:Airline` nodes.
- `commercial_aircraft_dataset.csv`: the source for `:Aircraft` nodes. The `aircraft_id` column is the ICAO type code and becomes `aircraft_type_id`. Variants share codes (A321, A321LR and A321XLR are all `A321`), and the first row wins.
- `airline_routes.json` (keyed by airport IATA code, recent): the source of which routes exist, with `km`, `min` and `carriers` per route. It also supplies `Airport.timezone`.
- `routes.csv` (OpenFlights `routes.dat`, about 2014): used only for its `equipment` column (IATA aircraft codes). Its header names have leading spaces, and `destination apirport` is misspelled in the file.
- `planes.csv` (OpenFlights, no header row): maps IATA equipment codes to ICAO codes. Family codes it lacks (`73H`, `32S`, `CRJ`...) and variants we don't model are handled by `IATA_EQUIPMENT_ALIASES` and `ICAO_ALIASES` in `seed3.py`.

How `seed3.py` builds a ROUTE: one edge per directed airport pair from the JSON. `aircraft_types` comes from the first tier that has data, and the tier is recorded in `fuel_burn_source`:
1. `EQUIPMENT_AIRLINE`: the same carrier on the same route in `routes.csv`.
2. `EQUIPMENT_ROUTE`: the same airport pair, either direction, any carrier.
3. `CARRIER_FLEET`: aircraft the carrier flew on routes within 1.5× of the distance, limited to aircraft with enough range.
4. `DISTANCE_BAND`: a generic mix chosen by distance.

Tiers 1 and 2 cover about 46% of routes, tier 3 about 43%, and tier 4 about 12%. `weight_carbon` is `co2_per_seat_kg`, not total CO2.

- `flights.csv` (Kaggle "2015 Flight Delays and Cancellations", about 5.8M rows, 590 MB): only January 2015 is used, read in chunks. The Flight layer has 1,000 flights:
  - 600 real US flights with `data_source='BTS_2015'`. 200 of them are deliberately from Winter Storm Juno (26–28 Jan, at JFK, LGA, BOS, EWR and PHL).
  - 400 synthetic flights with `data_source='SYNTHETIC'`: 150 in India, including a synthetic DEL/LKO/ATQ fog scenario on 13–14 Jan, and 250 across Europe, the Middle East, Asia and elsewhere.
  - Synthetic outcomes (cancelled, diverted, delays) are drawn from real January rows, and fog mornings are drawn from real disrupted airport-days. Real `DisruptionEvent`s are detected from cancellation spikes rather than written by hand.

Flight-data traps `seed4.py` handles:
- A flight number plus a day is not unique, because one flight number flies several legs a day. Only one leg is kept, so `flight_id` = `{carrier}{number}-{yyyymmdd}` stays unique.
- Times are local `HHMM` with no date rollover. Arrival is computed as departure + `SCHEDULED_TIME` in UTC, then converted to the destination timezone. This matches the dataset's own `SCHEDULED_ARRIVAL` for 99.99% of rows. Actual times are scheduled + delay, so late departures roll past midnight correctly.
- `zoneinfo` needs the `tzdata` package on Windows.

Data traps the seed scripts already handle, so keep handling them:
- Read CSVs with `keep_default_na=False`. Otherwise pandas turns Namibia's country code `"NA"` into NaN. The same goes for continent `NA` (North America).
- IATA codes are not unique in either source. Airports have placeholder codes like `0` and `-`, and old or closed airports reuse current codes (MUC, HKG). Airline codes get reassigned. Filter on the code format and pick one row per code deliberately; never let a plain MERGE keep whichever row came last.
- About 33 airport codes mean a different airport in the route JSON than in OurAirports (IAL is Salinas in Brazil there, but Ialibu in PNG for us), or the JSON coordinates are corrupt. `seed.mismatched_airports` (a location more than 50 km off) excludes them: their routes are skipped and their JSON timezone isn't used.
- The route JSON has self-loops (ACC→ACC) and impossible durations (680 km in 1 minute). JSON `km` is a great-circle distance, within 0.7% of haversine for 95% of routes. When it's more than 5% off, haversine from our coordinates is used.

- `dbms_review_transcription.md`: the Review 1 design (problem statement, why Neo4j, ER-to-graph mapping, full graph schema, sample data). **This is the source of truth for the data model.** When you add code, keep it consistent with this document, or update the document in the same change.
- `bcse302p_rubrics.md`: the grading rubric for the three reviews. Use it to decide what to build next and what "done" means.

## Rubric requirements for Neo4j projects (from `bcse302p_rubrics.md`)

- At least 5 node labels, 6 relationship types, and 100+ nodes (Review 2 also asks for at least 50 sample records)
- 10 meaningful Cypher queries
- At least 2 graph algorithms or analytics features (the design plans Dijkstra/A* pathfinding plus Betweenness/Degree/PageRank centrality)
- Node and relationship CRUD, graph indexes and constraints
- Interactive graph visualization and a responsive UI
- Final deliverables: working app, final report, source code, user manual and slides

## Data model (see the design doc for the full property lists)

Node labels: `Airport` (key `airport_id` = IATA code), `Airline` (`airline_id`), `Aircraft` (aircraft *type*, `aircraft_type_id` such as "B77W"), `Engine` (`engine_uid`, from the ICAO Emissions Databank), `Flight` (`flight_id` such as "BA178-20260822"), `DisruptionEvent` (`event_id`). Each key has a unique constraint.

Relationships and their directions (keep the directions exactly as written):
- `(:Airline)-[:OPERATES]->(:Flight)`
- `(:Flight)-[:DEPARTS_FROM]->(:Airport)`, `(:Flight)-[:ARRIVES_AT]->(:Airport)`
- `(:Flight)-[:USES_AIRCRAFT {tail_number}]->(:Aircraft)`
- `(:Airport)-[:ROUTE]->(:Airport)`: the pathfinding edge
- `(:Airport)-[:HUB_OF {since}]->(:Airline)`: note it points from Airport to Airline
- `(:Aircraft)-[:POWERED_BY {is_default_engine}]->(:Engine)`
- `(:DisruptionEvent)-[:AFFECTS]->(:Airport)`

Key design decisions that span the model:
- **Two layers.** `Flight` nodes (reified pattern) hold schedule and delay data. Direct `ROUTE` edges between airports hold precomputed aggregate weights. GDS algorithms run over `Airport`/`ROUTE` only, never through `Flight` nodes.
- **Multiple weights on each edge.** A `ROUTE` edge carries `weight_time` (= `avg_duration_min`), `weight_carbon` (= `co2_per_seat_kg`) and `weight_cost` at the same time. The fastest and the greenest routes come from the same graph with a different `relationshipWeightProperty`.
- **Carbon calculation:** `fuel_burn_estimate_kg = distance_km × avg_fuel_burn_kg_per_km + LTO fuel`, where LTO fuel is one landing/take-off cycle: the default engine's `fuel_lto_cycle_kg` × `engine_count` (`seed3.lto_fuel_by_aircraft`). Aircraft without a databank engine use cruise burn × about 270 km. Without the LTO term, the greenest path chains many short hops (BOM→JFK came out as 7 legs). Then `co2_emissions_kg = fuel_burn_estimate_kg × 3.16` (kg CO2 per kg of jet fuel). The 3.16 is a global constant applied in Cypher; nothing stores a CO2 factor on `:Aircraft`. `avg_fuel_burn_kg_per_km` is synthetic: a seeded random draw within a range set by aircraft `category`. `Engine` data (bypass ratio, thrust, LTO fuel) is there to support these weights.
- **Disruptions are soft state.** An outage is simulated by setting `Airport.operational_status` (ACTIVE/RESTRICTED/CLOSED) or `ROUTE.corridor_status` (ACTIVE/DISRUPTED/CLOSED). Nodes and edges are never deleted. GDS projections and Cypher queries filter on `'ACTIVE'`, so a disruption can be undone. `DisruptionEvent` nodes are a separate audit trail and do not drive routing.
- `Airport.location` is a Neo4j `point({latitude, longitude})`, which A* and spatial queries need. `Airport.region` supports queries that close a whole region at once.
- Datetimes are ISO 8601 with a UTC offset. `Flight.delay_minutes` is derived from actual departure minus scheduled departure.

The design doc writes one heading as "Disruption Event" (with a space), but the relationship table uses the label `DisruptionEvent`. Use `DisruptionEvent` in code.

Out of scope: live passenger logistics, and live ATC/ACAS integration.
