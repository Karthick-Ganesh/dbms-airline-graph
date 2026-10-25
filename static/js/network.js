// Graph explorer: renders {nodes, edges} from /api/graph/* with vis-network
// (Graph view) or plots the same airports and routes on Leaflet (Map view).
(() => {
  const STYLE = {
    Airport: { token: "--node-airport", shape: "dot", size: 16 },
    Airline: { token: "--node-airline", shape: "diamond", size: 14 },
    Aircraft: { token: "--node-aircraft", shape: "triangle", size: 14 },
    Engine: { token: "--node-engine", shape: "square", size: 11 },
    Flight: { token: "--node-flight", shape: "box" },
    DisruptionEvent: { token: "--node-event", shape: "star", size: 20 },
  };
  const $ = s => document.querySelector(s);
  const nodes = new vis.DataSet(), edges = new vis.DataSet();
  const raw = new Map();  // vis id -> original node/edge from the API
  const centers = new Set();  // airports the user explored (drawn larger on the map)
  let network, mode = "airport", view = "graph", geo, geoLayer;

  const airport = App.airportPicker($("#airport"));

  function visNode(n) {
    const s = STYLE[n.label] || { token: "--edge", shape: "dot" };
    const color = App.token(s.token);
    const closed = n.label === "Airport" && n.props.operational_status !== "ACTIVE";
    return {
      id: n.id, label: n.caption, shape: s.shape, size: s.size,
      color: { background: s.shape === "box" ? App.token("--surface") : color, border: closed ? App.token("--danger") : color,
               highlight: { background: color, border: App.token("--text") } },
      borderWidth: closed ? 4 : 2,
      font: { color: App.token("--text"), size: 13, face: App.token("--font-body") },
      title: `${n.label}: ${n.caption}${closed ? " (" + n.props.operational_status + ")" : ""}`,
    };
  }

  function visEdge(e) {
    const quiet = e.type === "ROUTE";
    return {
      id: e.id, from: e.from, to: e.to, arrows: "to",
      label: quiet ? undefined : e.type,
      title: e.type === "ROUTE" ? `ROUTE · ${App.number(e.props.distance_km)} km · ${App.minutes(e.props.avg_duration_min)}` : e.type,
      color: { color: e.props.corridor_status && e.props.corridor_status !== "ACTIVE" ? App.token("--danger") : App.token("--edge"),
               highlight: App.token("--primary") },
      dashes: e.props.corridor_status && e.props.corridor_status !== "ACTIVE",
      font: { size: 10, color: App.token("--text-muted"), strokeWidth: 3, strokeColor: App.token("--surface"), align: "middle" },
      smooth: { type: "continuous" },
    };
  }

  /** Add a graph to the canvas (replace = start over). */
  function show(graph, replace = true) {
    if (replace) { nodes.clear(); edges.clear(); raw.clear(); }
    graph.nodes.forEach(n => raw.set(n.id, n));
    graph.edges.forEach(e => raw.set(e.id, e));
    nodes.update(graph.nodes.map(visNode));
    edges.update(graph.edges.map(visEdge));
    legend();
    if (replace) network.once("stabilizationIterationsDone", () => network.fit({ animation: true }));
    if (view === "map") renderMap(replace);
  }

  function legend() {
    const items = [...raw.values()];
    const present = new Set(items.filter(x => x.label).map(n => n.label));
    if (view === "map") {
      const offMap = items.filter(n => n.label && n.label !== "Airport").length;
      $("#legend").innerHTML = `
        <span class="legend-item"><span class="legend-dot" style="background:${App.token("--node-airport")}"></span>Airport</span>
        <span class="legend-item"><span class="legend-line" style="border-color:${App.token("--node-airport")}"></span>ROUTE (width = carriers)</span>
        ${present.has("Flight") ? `<span class="legend-item"><span class="legend-line" style="border-color:${App.token("--node-flight")}"></span>Flight</span>` : ""}
        <span class="legend-item"><span class="legend-dot" style="background:${App.token("--danger")}"></span>Closed / affected</span>
        ${offMap ? `<span class="legend-item small muted">${offMap} non-airport nodes are in Graph view</span>` : ""}`;
      return;
    }
    $("#legend").innerHTML = Object.entries(STYLE).filter(([label]) => present.has(label)).map(([label, s]) =>
      `<span class="legend-item"><span class="legend-dot" style="background:${App.token(s.token)}"></span>${label}</span>`).join("")
      + `<span class="legend-item small muted">${nodes.length} nodes · ${edges.length} relationships</span>`;
  }

  /** Map view: Airport nodes at their location, ROUTE edges as great-circle arcs.
   *  Everything is drawn in each world copy (RouteMap.wrapped) so lines and
   *  markers stay together when the map wraps around. */
  function renderMap(fit = true) {
    geoLayer?.remove();
    geoLayer = L.featureGroup().addTo(geo);
    const items = [...raw.values()];
    const airports = new Map(items.filter(n => n.label === "Airport" && n.props.location).map(n => [n.id, n]));
    const pos = n => [n.props.location.latitude, n.props.location.longitude];
    const affected = new Set(items.filter(e => e.type === "AFFECTS").map(e => e.to));
    const view = L.latLngBounds([]);   // what to fit: the real (offset 0) lines and centre airports

    const line = (pts, style, tip, onClick) => {
      view.extend(L.latLngBounds(pts));
      RouteMap.wrapped(off => L.polyline(RouteMap.shift(pts, off), style)
        .bindTooltip(tip, { sticky: true }).on("click", onClick).addTo(geoLayer));
    };

    items.filter(e => e.type === "ROUTE" && airports.has(e.from) && airports.has(e.to)).forEach(e => {
      const a = airports.get(e.from), b = airports.get(e.to);
      const active = !e.props.corridor_status || e.props.corridor_status === "ACTIVE";
      line(RouteMap.leg(pos(a), pos(b)), {
        color: App.token(active ? "--node-airport" : "--danger"), opacity: 0.55,
        weight: 1 + Math.min(4, (e.props.carriers || []).length / 2), dashArray: active ? null : "4 6",
      }, `${a.caption} → ${b.caption} · ${App.number(e.props.distance_km)} km · ${App.minutes(e.props.avg_duration_min)}`,
      () => details(e));
    });

    items.filter(n => n.label === "Flight").forEach(f => {
      const dep = items.find(e => e.type === "DEPARTS_FROM" && e.from === f.id);
      const arr = items.find(e => e.type === "ARRIVES_AT" && e.from === f.id);
      if (!dep || !arr || !airports.has(dep.to) || !airports.has(arr.to)) return;
      line(RouteMap.leg(pos(airports.get(dep.to)), pos(airports.get(arr.to))),
           { color: App.token("--node-flight"), weight: 4, opacity: 0.9 },
           `Flight ${f.caption} · ${f.props.status}`, () => details(f));
    });

    airports.forEach(n => {
      const bad = n.props.operational_status !== "ACTIVE" || affected.has(n.id);
      const center = centers.has(n.props.airport_id);
      const color = App.token(bad ? "--danger" : "--node-airport");
      const [lat, lon] = pos(n);
      if (center || !view.isValid()) view.extend([lat, lon]);
      RouteMap.wrapped(off => {
        const marker = L.circleMarker([lat, lon + off], { radius: center ? 8 : 5, weight: 2, color,
                                                          fillColor: center || bad ? color : App.token("--surface"), fillOpacity: 1 })
          .bindTooltip(`${n.caption} · ${App.esc(n.props.city)}${bad && n.props.operational_status !== "ACTIVE" ? " (" + n.props.operational_status + ")" : ""}`,
                       { direction: "top", permanent: center, className: center ? "map-label" : "" })
          .on("click", () => details(n))
          .on("dblclick", () => load("airport", n.props.airport_id, false))
          .addTo(geoLayer);
        if (center) marker.bringToFront();
      });
    });

    if (fit && view.isValid()) geo.fitBounds(view.pad(0.1));
  }

  function setView(v) {
    view = v;
    document.querySelectorAll("[data-view]").forEach(b => b.setAttribute("aria-pressed", b.dataset.view === v));
    $("#graph").classList.toggle("hidden", v !== "graph");
    $("#geo").classList.toggle("hidden", v !== "map");
    if (v === "map") {
      geo ??= RouteMap.create("geo");
      geo.doubleClickZoom.disable();  // double-click expands an airport instead
      // Wait a frame so the now-visible container has its real size.
      requestAnimationFrame(() => { geo.invalidateSize(); renderMap(); });
    } else {
      network.fit();
    }
    legend();
  }

  function value(v) {
    if (v == null) return '<span class="muted">null</span>';
    if (typeof v === "object" && "latitude" in v) return `point(${App.number(v.latitude, 4)}, ${App.number(v.longitude, 4)})`;
    if (Array.isArray(v)) return App.esc(v.join(", "));
    if (typeof v === "number") return App.number(v, 2);
    return App.esc(v);
  }

  function details(item) {
    const isNode = !!item.label;
    const head = isNode
      ? `<span class="badge" style="color:${App.token(STYLE[item.label]?.token || "--text")}">:${App.esc(item.label)}</span>
         <h2 style="margin-top:var(--space-2)">${App.esc(item.caption)}</h2>`
      : `<span class="badge">[:${App.esc(item.type)}]</span>
         <h2 style="margin-top:var(--space-2)">${App.esc(raw.get(item.from)?.caption)} → ${App.esc(raw.get(item.to)?.caption)}</h2>`;
    const rows = Object.entries(item.props).sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `<tr><th>${App.esc(k)}</th><td>${value(v)}</td></tr>`).join("");
    let actions = "";
    if (item.label === "Airport") {
      const code = item.props.airport_id;
      actions = `<button class="btn btn-secondary" data-expand="${App.esc(code)}">Expand routes</button>
                 <a class="btn btn-secondary" href="/?from=${App.esc(code)}">Routes from ${App.esc(code)}</a>`;
    } else if (item.label === "Flight") {
      actions = `<button class="btn btn-secondary" data-flight="${App.esc(item.props.flight_id)}">Show flight graph</button>`;
    } else if (item.label === "DisruptionEvent") {
      actions = `<button class="btn btn-secondary" data-event="${App.esc(item.props.event_id)}">Show event graph</button>`;
    }
    $("#details").innerHTML = `${head}
      ${actions ? `<div class="chips" style="margin-bottom:var(--space-3)">${actions}</div>` : ""}
      <div class="table-wrap"><table class="props">${rows}</table></div>`;
  }

  async function load(kind, id, replace = true) {
    if (!id) { App.toast("Choose something to show first.", "error"); return; }
    const limit = $("#limit").value;
    const url = kind === "airport" ? `/api/graph/airport/${encodeURIComponent(id)}?limit=${limit}`
      : `/api/graph/${kind}/${encodeURIComponent(id)}`;
    await App.attempt(async () => {
      const graph = await App.api(url);
      if (replace) centers.clear();
      if (kind === "airport") centers.add(id);
      show(graph, replace);
      if (replace) history.replaceState(null, "", `?${kind}=${encodeURIComponent(id)}`);
    });
  }

  function setMode(m) {
    mode = m;
    document.querySelectorAll(".tab").forEach(t => t.setAttribute("aria-selected", t.dataset.mode === m));
    document.querySelectorAll(".mode-field").forEach(f => f.classList.toggle("hidden", f.dataset.mode !== m));
  }

  async function fillPickers() {
    const [flights, events] = await Promise.all([
      App.api("/api/flights?limit=500").catch(() => []),
      App.api("/api/events").catch(() => []),
    ]);
    $("#flight-list").innerHTML = flights.map(f =>
      `<option value="${App.esc(f.flight_id)}">${App.esc(f.origin)} → ${App.esc(f.destination)} · ${App.esc(f.status)}</option>`).join("");
    $("#event").innerHTML = events.map(e =>
      `<option value="${App.esc(e.event_id)}">${App.esc(e.event_id)} · ${App.esc(e.description || "")}</option>`).join("");
  }

  document.addEventListener("DOMContentLoaded", () => {
    network = new vis.Network($("#graph"), { nodes, edges }, {
      physics: { solver: "forceAtlas2Based", stabilization: { iterations: 150 },
                 forceAtlas2Based: { gravitationalConstant: -60, springLength: 110 } },
      interaction: { hover: true, tooltipDelay: 120, navigationButtons: false, keyboard: false },
      layout: { improvedLayout: false },
    });
    network.on("click", p => {
      const id = p.nodes[0] || p.edges[0];
      if (id) details(raw.get(id));
    });
    network.on("doubleClick", p => {
      const n = raw.get(p.nodes[0]);
      if (n?.label === "Airport") load("airport", n.props.airport_id, false);
    });

    document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => setMode(t.dataset.mode)));
    $("#limit").addEventListener("input", () => { $("#limit-value").textContent = $("#limit").value; });
    $("#fit").addEventListener("click", () => view === "map" ? renderMap() : network.fit({ animation: true }));
    document.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
    $("#explore").addEventListener("submit", e => {
      e.preventDefault();
      const id = mode === "airport" ? airport.get() : mode === "flight" ? $("#flight").value.trim() : $("#event").value;
      load(mode, id);
    });
    $("#details").addEventListener("click", e => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.expand) load("airport", b.dataset.expand, false);
      if (b.dataset.flight) { setMode("flight"); $("#flight").value = b.dataset.flight; load("flight", b.dataset.flight); }
      if (b.dataset.event) { setMode("event"); $("#event").value = b.dataset.event; load("event", b.dataset.event); }
    });

    fillPickers().then(() => {
      const params = new URLSearchParams(location.search);
      const [kind, id] = ["airport", "flight", "event"].map(k => [k, params.get(k)]).find(([, v]) => v) || ["airport", "DEL"];
      setMode(kind);
      if (kind === "airport") airport.set(id.toUpperCase());
      if (kind === "flight") $("#flight").value = id;
      if (kind === "event") $("#event").value = id;
      load(kind, kind === "airport" ? id.toUpperCase() : id);
    });
  });
})();
