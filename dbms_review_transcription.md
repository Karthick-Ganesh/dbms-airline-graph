# Graph-Based Airline Route Optimization for Disruption Analysis and Sustainable Travel

## Abstract

This project presents a Neo4j graph-database framework to model and optimize global airline networks. By representing aviation infrastructure as a dynamic graph, the system utilizes network algorithms to evaluate systemic resilience, simulating localized airport disruptions to identify critical vulnerabilities and perform dynamic rerouting. Additionally, the model incorporates edge-weight analysis to explore sustainable eco-routing, optimizing flight paths based on environmental impact rather than traditional distance or time constraints. Ultimately, this project demonstrates the capability of NoSQL graph databases in solving complex logistics, disaster recovery, and sustainable routing challenges within interconnected transportation networks.

## Problem Statement

Global commercial aviation networks are inherently vulnerable to cascading disruptions caused by extreme weather, infrastructure failures, geopolitical events, and operational bottlenecks. Traditional relational and tabular databases struggle to compute multi-hop network traversals and shortest paths efficiently under real-time disruption constraints. Furthermore, standard flight-routing systems prioritize speed or financial cost while neglecting carbon emissions and ecological footprints. There is a need for a dynamic, graph-native model capable of simulating systemic vulnerabilities, executing real-time rerouting during localized hub outages, and prioritizing sustainable eco-routing.

## Objective

### Model Aviation Topologies:

Construct a high-performance graph representation of global airports, flight routes, airlines, and operational metrics. Utilize graph centrality metrics (Betweenness, Degree Centrality) to locate single points of failure and vulnerable transit hubs.

### Simulate Disruption & Dynamic Rerouting:

Implement pathfinding algorithms (such as Dijkstra's and $A^*$) to recalculate optimal alternative flight paths when specific nodes (airports) or edges (routes) fail. Define multi-attribute edge weights incorporating fuel burn, aircraft type, and carbon emissions ($CO_2$) to enable eco-friendly route recommendations alongside cost/time metrics.

## Scope

The scope of this project encompasses the design and implementation of a directed graph topology using Neo4j to model regional and international aviation networks. Within this NoSQL architecture, airports will be instantiated as distinct nodes containing embedded geospatial properties (latitude, longitude) and operational metadata, while flight paths will be modelled as directed edges (relationships). These relationships will carry multi-attribute weights, specifically flight duration, physical distance, and calculated carbon emissions based on aircraft fuel burn constants. A core technical focus is leveraging Neo4j's Graph Data Science (GDS) library to execute native pathfinding algorithms such as weighted Dijkstra's and $A^*$ (A-Star) search directly in the database engine. This setup will facilitate disruption simulations by allowing the system to dynamically filter out "closed" nodes via Cypher query constraints, thereby forcing the engine to compute real-time, multi-hop alternative routes. Ultimately, the system will output a quantitative comparison between traditionally optimized paths (shortest time) and sustainably optimized paths (lowest carbon footprint).

*Note: The Project will not include live commercial passenger logistics or any live Air Traffic Control (ATC) / Airborne Collision Avoidance System (ACAS) (Out of Scope).*

## NoSQL Database Selection & Justification (Neo4j)

| Requirement | Why a Property Graph (Neo4j) Fits | Why Relational/Other NoSQL Falls Short | 
 | ----- | ----- | ----- | 
| **Multi-hop path traversal (2-10 hops) / Centrality/bottleneck detection** | Index-free adjacency: traversal cost is $O(1)$ per hop, independent of total graph size. Neo4j GDS ships production-grade Betweenness, Degree, PageRank algorithms operating directly on the stored graph. | RDBMS: each hop a JOIN; cost grows with table size. Document DBs (MongoDB): no native multi-hop traversal, requires `$graphLookup` which is far slower than native graph engines. Cassandra/wide-column stores have no graph algorithm layer; would require exporting data to an external graph engine. | 
| **Weighted shortest path (Dijkstra/**$A^*$**)** | GDS shortest Path, dijkstra/astar procedures run natively over relationship weight properties. | RDBMS requires recursive CTEs with manual priority-queue emulation—poor performance beyond small graphs. | 
| **Dynamic disruption simulation** | Node/relationship properties (operational status, corridor status) can be toggled in-place; graph projections filter on these flags at algorithm run-time, with no schema migration. | RDBMS disruption modelling requires soft-delete flags across multiple tables; joined consistency is harder to enforce. | 
| **Multi-criteria weights on same edge** | A single relationship naturally holds multiple weight properties (weight time, weight carbon, weight cost) simultaneously. | Document stores would need to duplicate edge-like documents per criterion, and pathfinding logic must be built entirely in application code. | 

**Conclusion:** Neo4j's Labelled Property Graph (LPG) model is the architecturally superior choice because the problem itself is fundamentally graph-shaped—the questions the system must answer ("what's connected to what, how, and at what cost") map directly onto nodes and relationships rather than rows and joins.

## Database Model Design:

### 1. Relational (ER) → Graph NoSQL Mapping

#### 1.1 Mapping Table:

| Relational Concept | Example | Graph (Neo4j) Equivalent | 
 | ----- | ----- | ----- | 
| **Entity table** | `Airports(airport_id PK, name, city,...)` | Node label: `Airport` with properties | 
| **Entity table** | `Airlines(airline_id PK, name, ...)` | Node label: `Airline` | 
| **Entity table** | `Aircraft_Types(type_id PK, model, ...)` | Node label: `Aircraft` | 
| **Entity table with FKs** | `Flights(flight_id PK, dep_airport_id FK, arr_airport_id FK, airline_id FK, aircraft_type_id FK)` | Node label: `Flight`, with the four foreign keys replaced by directed relationships: `DEPARTS_FROM`, `ARRIVES_AT`, `OPERATES` (from Airline), `USES_AIRCRAFT` | 
| **Many-to-many junction/associative table** | `Routes(dep_airport_id FK, arr_airport_id FK, distance_km, avg_duration_min)` | Collapsed directly into a `(:Airport)-[:ROUTE {distance_km, avg_duration_min, ...}]->(:Airport)` relationship - no junction node required, since a relationship in a property graph can natively hold attributes. | 
| **Many-to-many junction table** | `Airline_Hubs(airline_id FK, airport_id FK)` | `(:Airport)-[:HUB_OF]->(:Airline)` relationship | 
| **Many-to-many junction table (new)** | `Aircraft_Engine_Options(aircraft_type_id FK, engine_uid FK)` since one aircraft type is certified for several engine choices, and one engine can power several aircraft types. | `(Aircraft)-[:POWERED_BY]->(:Engine)` relationship | 
| **Reference/lookup table (new)** | `Engines(engine_uid PK, manufacturer, bypass_ratio, ...)` from the ICAO Emissions Databank, \~100 raw columns | `Engine` node, trimmed to the 15 columns actually needed for fuel-efficiency and local-emissions modeling. | 
| **Foreign key constraint (referential integrity)** | `FOREIGN KEY (dep_airport_id) REFERENCES Airports` | Relationship pattern itself enforces the connection; no orphaned-key risk because a relationship cannot exist without both endpoint nodes. | 
| **Composite lookup query (JOIN across 3-4 tables)** | Flights JOIN Airports JOIN Airlines JOIN Aircraft_Types | Single Cypher pattern: `(Airline)-[:OPERATES]->(Flight)-[:DEPARTS_FROM]->(Airport)` | 

#### 1.2 Architectural Advantages for Routing & Disruption Handling:

* **Index-free adjacency:** Every node stores direct pointers to its relationships. Traversing from an airport to its neighbors is a pointer lookup, not an index scan—critical for real-time rerouting during an outage.

* **No junction-table overhead:** The many-to-many `Routes` association becomes a first-class relationship carrying weights directly, eliminating an entire table and its joins from every path query.

* **Schema flexibility for disruption states:** Operational properties (`corridor_status`, `disruption_reason`) can be added to existing relationships without `ALTER TABLE` locks or migrations—essential when disruption modeling requirements evolve.

* **Natural variable-length path expression:** Cypher's `[:ROUTE*1..6]` syntax expresses "up to 6 connecting flights" in one line; the relational equivalent needs a recursive CTE with explicit depth bookkeeping.

* **Algorithm-ready structure:** GDS algorithms (Dijkstra, $A^*$, Betweenness) consume the graph directly—no ETL step to reshape relational rows into an adjacency list is needed.

### 2. Graph Schema Design

#### 2.1 Node Labels & Properties:

**:Airport** (unique constraint on `airport_id`)

| Property | Type | Description | 
 | ----- | ----- | ----- | 
| `airport_id` | String | IATA code (e.g., "JFK") - primary identifier | 
| `icao_code` | String | ICAO code (e.g., "KJFK") | 
| `name` | String | Full airport name | 
| `city` | String | City served | 
| `country` | String | Country | 
| `region` (new) | String | Continent-level geographic grouping (e.g., "North America", "Europe", "Asia") enables regional cascading-disruption queries (e.g., "close all airports in a storm-affected region") | 
| `location` | Point (spatial) | `point({latitude, longitude})` enables spatial/distance queries | 
| `elevation_m` | Float | Elevation above sea level | 
| `timezone` | String | IANA timezone id | 
| `is_hub` | Boolean | Flags major-hub airports for centrality baselining | 
| `operational_status` | String (enum) | ACTIVE | RESTRICTED | CLOSED - toggled for disruption simulation | 
| `daily_capacity` | Integer | Max scheduled movements/day | 
| `runway_count` | Integer | Number of active runways | 

**:Airline** (unique constraint on `airline_id`)

| Property | Type | Description | 
 | ----- | ----- | ----- | 
| `airline_id` | String | IATA airline code (e.g., "BA") | 
| `name` | String | Airline name | 
| `country` | String | Country of registration | 
| `alliance` | String | e.g., "Oneworld", "Star Alliance" | 
| `fleet_size` | Integer | Number of aircraft operated | 

**:Aircraft** (aircraft type/model reference; unique constraint on `aircraft_type_id`)

| Property | Type | Description | 
 | ----- | ----- | ----- | 
| `aircraft_type_id` | String | Internal type code (e.g., "B77W") | 
| `model` | String | e.g., "Boeing 777-300ER" | 
| `manufacturer` | String | e.g., "Boeing" | 
| `category` | String (enum) | Widebody | Narrowbody | Regional | Turboprop | Private Jet | Freighter | Supersonic | 
| `seating_capacity` | Integer | Max seats | 
| `avg_fuel_burn_kg_per_km` | Float | Cruise-phase fuel burn rate; used to derive route-level emissions (see modeling note below) | 
| `max_range_km` | Integer | Operational range | 
| `engine_count` | Integer | Number of engines, inferred from the model name | 

*$CO_2$ is not stored on `:Aircraft`. It is derived in Cypher as `avg_fuel_burn_kg_per_km` × 3.16, where 3.16 kg of $CO_2$ per kg of jet fuel burned is a global constant.*

**:Engine** (new - sourced from the ICAO Aircraft Engine Emissions Databank ges.csv; unique constraint on `engine_uid`)

| Property | Type | Description | 
 | ----- | ----- | ----- | 
| `engine_uid` | String | Databank UID (e.g., "7GE099") primary identifier | 
| `manufacturer` | String | Engine manufacturer (e.g., "General Electric Company") | 
| `engine_model` | String | Engine Identification (e.g., "GE90-115B") | 
| `bypass_ratio` | Float | Bypass ratio - the single strongest indicator of fuel efficiency across engine families | 
| `rated_thrust_kn` | Float | Certified rated thrust in kN | 
| `fuel_lto_cycle_kg` | Float | Total certified fuel burned over one standard LTO cycle | 

*The `(Engine)` node links aircraft to empirical hardware specifications from the ICAO Emissions Databank rather than relying on generic estimates. By filtering out non-essential testing parameters, the model retains core indicators such as bypass ratio and rated thrust to establish physical fuel efficiency baselines. These metrics serve to substantiate and refine the route-level carbon weights (*$CO_2$*) used in sustainable pathfinding algorithms.*

**:Flight** (a specific scheduled flight leg - the reified "connection" entity; unique constraint on `flight_id`)

| Property | Type | Description | 
 | ----- | ----- | ----- | 
| `flight_id` | String | Unique instance id (e.g., "BA178-20260822") | 
| `flight_number` | String | e.g., "BA178" | 
| `scheduled_departure` | DateTime | ISO 8601 datetime | 
| `scheduled_arrival` | DateTime | ISO 8601 datetime | 
| `actual_departure` (new) | DateTime | Recorded actual departure - nullable until the flight departs | 
| `actual_arrival` (new) | DateTime | Recorded actual arrival - nullable until the flight lands | 
| `delay_minutes` (new) | Integer | `duration(actual_departure - scheduled_departure)`, in minutes - the actual quantitative measure of disruption impact per flight | 
| `status` | String (enum) | SCHEDULED | DELAYED | CANCELLED | DIVERTED | COMPLETED - DELAYED means departure ≥ 15 min late (the BTS on-time definition) | 
| `distance_km` | Float | Great-circle distance flown | 
| `duration_min` | Integer | Scheduled flight duration | 
| `base_fare_usd` | Float | Reference fare for cost-weighted routing (same synthetic distance-based fare as `ROUTE.weight_cost`) | 
| `data_source` (new) | String (enum) | BTS_2015 | SYNTHETIC - BTS_2015 are real US domestic flights from January 2015 (Kaggle "2015 Flight Delays and Cancellations"). SYNTHETIC flights (India and the rest of the world) use real routes, carriers and aircraft, with schedules and delay/cancellation outcomes sampled from the real data | 

*The `(:Flight)` entity is modeled as a distinct node rather than a simple relationship using the reified entity pattern, allowing each scheduled service to maintain its own identity, schedule, and operational status. While historical flight instances track quantitative disruption metrics like actual delay times, direct `[:ROUTE]` relationships connect airport pairs with precomputed time and carbon weights. This dual-layer architecture separates transactional schedule data from pathfinding edges, enabling Neo4j Graph Data Science (GDS) algorithms to traverse the network efficiently without traversing through individual flight schedules.*

**:Disruption Event** (new - an auditable, time-bound record of a disruption, separate from the live `operational_status`/`corridor_status` flags)

| Property | Type | Description | 
 | ----- | ----- | ----- | 
| `event_id` | String | Unique identifier | 
| `event_type` | String (enum) | WEATHER | ATC_FAILURE | TECHNICAL | GEOPOLITICAL | LABOUR_ACTION | OTHER | 
| `severity` | String (enum) | LOW | MODERATE | SEVERE | CRITICAL | 
| `start_time` | DateTime | When the disruption began | 
| `end_time` | DateTime | When it was resolved (nullable while ongoing) | 
| `description` | String | Free-text summary | 
| `data_source` (new) | String (enum) | BTS_2015 | SYNTHETIC - BTS_2015 events are detected from real cancellation spikes (an airport-day where ≥ 30 departures and ≥ 25% of departures were cancelled for weather or air-traffic-system reasons, merged across consecutive days). Example: Winter Storm Juno, 26–28 Jan 2015 | 

#### 2.2 Directed Relationships & Properties:

| Relationship | Direction | Cardinality | Key Properties | Purpose | 
 | ----- | ----- | ----- | ----- | ----- | 
| `(:Airline)-[:OPERATES]->(:Flight)` | Airline → Flight | 1:N |  | Ownership of scheduled flight | 
| `(:Flight)-[:DEPARTS_FROM]->(:Airport)` | Flight → Airport | N:1 |  | Origin linkage | 
| `(:Flight)-[:ARRIVES_AT]->(:Airport)` | Flight → Airport | N:1 |  | Destination linkage | 
| `(:Flight)-[:USES_AIRCRAFT]->(:Aircraft)` | Flight → Aircraft | N:1 | `tail_number` (String) | Specific tail assigned to type | 
| `(:Airport)-[:ROUTE]->(:Airport)` | Origin → Destination | M:N | *see below* | Primary edge for pathfinding & GDS algorithms | 
| `(:Airport)-[:HUB_OF]->(:Airline)` | Airport → Airline | M:N | `since` (Date, unset: no source data) | Identifies hub airports per carrier for resilience analysis. Derived from route data: an airline's top 5 origin airports that each have ≥ 5 routes and ≥ 30% of the busiest origin's route count | 
| `(:Aircraft)-[:POWERED_BY]->(:Engine)` (new) | Aircraft → Engine | M:N | `is_default_engine` (Boolean) | Links an aircraft type to its certified engine option(s). One type can be offered with several engine choices, and one engine variant can power several aircraft types. | 
| `(:DisruptionEvent)-[:AFFECTS]->(:Airport)` (new) | Event → Airport | M:N |  | Historical/audit link from a logged disruption event to every airport it impacted | 

**ROUTE relationship properties (the multi-criteria pathfinding edge):**

| Property | Type | Purpose | 
 | ----- | ----- | ----- | 
| `distance_km` | Float | Great-circle distance | 
| `avg_duration_min` | Integer | Average scheduled flight time on this corridor | 
| `avg_speed_kmh` | Float | Derived operational metric | 
| `fuel_burn_estimate_kg` | Float | `distance_km` × `avg_fuel_burn_kg_per_km` + one landing/take-off (LTO) cycle, averaged over the route's aircraft mix. LTO fuel = the default engine's ICAO `fuel_lto_cycle_kg` × `engine_count` (via `POWERED_BY`). Charging it once per leg means every extra stop costs carbon. | 
| `co2_emissions_kg` | Float | `fuel_burn_estimate_kg` × 3.16 (kg $CO_2$ per kg jet fuel) - total per flight | 
| `co2_per_seat_kg` (new) | Float | $CO_2$ ÷ `seating_capacity`, averaged over the aircraft mix - what eco-routing minimises | 
| `weight_time` | Float | Normalized weight for fastest-path queries (= `avg_duration_min`) | 
| `weight_carbon` | Float | Normalized weight for greenest-path queries (= `co2_per_seat_kg`) | 
| `weight_cost` | Float | Normalized weight for cost-optimized queries (synthetic fare: 50 + 0.10 × `distance_km` USD, since no fare data is available) | 
| `aircraft_types` (new) | List of String | `aircraft_type_id`s believed to fly this route (passenger types only) | 
| `carriers` (new) | List of String | IATA codes of airlines serving the route | 
| `fuel_burn_source` (new) | String (enum) | EQUIPMENT_AIRLINE | EQUIPMENT_ROUTE | CARRIER_FLEET | DISTANCE_BAND - how `aircraft_types` was determined, from most to least direct evidence | 
| `duration_estimated` (new) | Boolean | True when the source duration was missing or physically impossible and a fitted block-time model (46.2 + 0.0725 × km minutes) was used | 
| `corridor_status` | String (enum) | ACTIVE | DISRUPTED | CLOSED - toggled for outage simulation without deleting the edge | 
| `congestion_index` | Float (0-1) | Real-time load factor, usable as a tie-breaker weight | 
| `last_updated` | DateTime | Freshness marker for operational data | 

*Why per seat: total $CO_2$ per flight makes small aircraft look greenest. A 70-seat turboprop "beats" a 300-seat widebody on the same route even though it emits more per passenger. Minimising $CO_2$ per seat gives the greenest path for one traveller, the same basis as ICAO's carbon calculator.*

*This soft-state design (`operational_status` on `:Airport`, `corridor_status` on `ROUTE`) is what makes dynamic disruption simulation possible: an outage is simulated by flipping a property, and every downstream Cypher/GDS query that filters on 'ACTIVE' immediately reflects the new topology—no nodes or edges are destroyed, so the disruption is fully reversible and auditable.*

## Sample Dataset:

*Sample dataset represented as JSON file for visual representation.*

```
[
  {
    "label": "Airport",
    "properties": {
      "airport_id": "JFK",
      "icao_code": "KJFK",
      "name": "John F. Kennedy International Airport",
      "city": "New York",
      "country": "USA",
      "location": { "latitude": 40.6413, "longitude": -73.7781 },
      "elevation_m": 4,
      "timezone": "America/New_York",
      "is_hub": true,
      "operational_status": "ACTIVE",
      "daily_capacity": 1200,
      "runway_count": 4
    }
  },
  {
    "label": "Airport",
    "properties": {
      "airport_id": "LHR",
      "icao_code": "EGLL",
      "name": "London Heathrow Airport",
      "city": "London",
      "country": "UK",
      "location": { "latitude": 51.4700, "longitude": -0.4543 },
      "elevation_m": 25,
      "timezone": "Europe/London",
      "is_hub": true,
      "operational_status": "ACTIVE",
      "daily_capacity": 1300,
      "runway_count": 2
    }
  },
  {
    "label": "Airline",
    "properties": {
      "airline_id": "BA",
      "name": "British Airways",
      "country": "UK",
      "alliance": "Oneworld",
      "fleet_size": 280
    }
  },
  {
    "label": "Aircraft",
    "properties": {
      "aircraft_type_id": "B77W",
      "model": "Boeing 777-300ER",
      "manufacturer": "Boeing",
      "category": "Widebody",
      "seating_capacity": 296,
      "avg_fuel_burn_kg_per_km": 7.8,
      "max_range_km": 13650,
      "engine_count": 2
    }
  },
  {
    "label": "Engine",
    "properties": {
      "engine_uid": "7GE099",
      "manufacturer": "General Electric Company",
      "engine_model": "GE90-115B",
      "bypass_ratio": 7.08,
      "rated_thrust_kn": 513.9,
      "fuel_lto_cycle_kg": 1546.0
    }
  },
  {
    "label": "Flight",
    "properties": {
      "flight_id": "BA178-20260822",
      "flight_number": "BA178",
      "scheduled_departure": "2026-08-22T18:20:00-04:00",
      "scheduled_arrival": "2026-08-23T06:15:00+01:00",
      "actual_departure": "2026-08-22T18:47:00-04:00",
      "actual_arrival": "2026-08-23T06:52:00+01:00",
      "delay_minutes": 27,
      "status": "DELAYED",
      "distance_km": 5555,
      "duration_min": 415,
      "base_fare_usd": 620.0
    }
  }
]

```

## Sample Relationship Table:

| From | Relationship | To | Key Properties | 
 | ----- | ----- | ----- | ----- | 
| `BA` (Airline) | `OPERATES` | `BA178-20260822` (Flight) |  | 
| `BA178-20260822` (Flight) | `DEPARTS_FROM` | `JFK` (Airport) |  | 
| `BA178-20260822` (Flight) | `ARRIVES_AT` | `LHR` (Airport) |  | 
| `BA178-20260822` (Flight) | `USES_AIRCRAFT` | `B77W` (Aircraft) | `tail_number`: "G-STBC" | 
| `JFK` (Airport) | `ROUTE` | `LHR` (Airport) | `distance_km`: 5555, `weight_time`: 415, `weight_carbon`: 296.68, `weight_cost`: 605.5, `corridor_status`: "ACTIVE" | 
| `LHR` (Airport) | `HUB_OF` | `BA` (Airline) | `since`: 1974-01-01 | 
| `B77W` (Aircraft) | `POWERED_BY` | `7GE099` (Engine) | `is_default_engine`: true | 
| `EVT-2026-0822-01` (DisruptionEvent) | `AFFECTS` | `LHR` (Airport) |  | 
