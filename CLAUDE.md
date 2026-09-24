# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This is a course project for BCSE302P (Database Systems Lab): **Graph-Based Airline Route Optimization for Disruption Analysis and Sustainable Travel**, built on **Neo4j** and its Graph Data Science (GDS) library. The code so far is a single seed script. There is no application or test suite yet.

## Commands

Use the project venv. The system-wide Python's pandas is broken by a numpy binary mismatch.

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python seed.py --dry-run   # parse the source data only, no DB needed
.venv/Scripts/python seed.py             # needs NEO4J_PASSWORD; optional NEO4J_URI, NEO4J_USER
```

`seed.py` is idempotent: it uses `MERGE` on each label's unique key, and it creates the constraints before loading. Add each new label as a `read_*`/`load_*` pair, following the Engine pattern.

- `edb-emissions-databank_v32__web_.xlsx`: the ICAO Engine Emissions Databank, the source for `:Engine` nodes. Only 6 of its roughly 105 columns in sheet `Gaseous Emissions and Smoke` are used (see `ENGINE_COLUMNS` in `seed.py`). Some headers have trailing spaces, so headers are stripped before columns are selected.

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
- **Multiple weights on each edge.** A `ROUTE` edge carries `weight_time` (= `avg_duration_min`), `weight_carbon` (= `co2_emissions_kg`) and `weight_cost` at the same time. The fastest and the greenest routes come from the same graph with a different `relationshipWeightProperty`.
- **Carbon calculation:** `fuel_burn_estimate_kg = distance_km × avg_fuel_burn_kg_per_km`, then `co2_emissions_kg = fuel_burn_estimate_kg × emission_factor`. `Engine` data (bypass ratio, thrust, LTO fuel) is there to support these weights.
- **Disruptions are soft state.** An outage is simulated by setting `Airport.operational_status` (ACTIVE/RESTRICTED/CLOSED) or `ROUTE.corridor_status` (ACTIVE/DISRUPTED/CLOSED). Nodes and edges are never deleted. GDS projections and Cypher queries filter on `'ACTIVE'`, so a disruption can be undone. `DisruptionEvent` nodes are a separate audit trail and do not drive routing.
- `Airport.location` is a Neo4j `point({latitude, longitude})`, which A* and spatial queries need. `Airport.region` supports queries that close a whole region at once.
- Datetimes are ISO 8601 with a UTC offset. `Flight.delay_minutes` is derived from actual departure minus scheduled departure.

The design doc writes one heading as "Disruption Event" (with a space), but the relationship table uses the label `DisruptionEvent`. Use `DisruptionEvent` in code.

Out of scope: live passenger logistics, and live ATC/ACAS integration.
