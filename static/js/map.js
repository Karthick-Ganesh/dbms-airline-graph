// Leaflet helpers shared by the route finder and the disruption simulator.
// Paths are drawn as great-circle arcs, the way flights actually fly.
const RouteMap = (() => {
  const rad = d => d * Math.PI / 180, deg = r => r * 180 / Math.PI;
  const SEGMENTS = 40;  // points per leg

  function create(el) {
    const map = L.map(el, { worldCopyJump: true, minZoom: 1, zoomSnap: 0.5 }).setView([25, 40], 2);
    // The basemap URL is a design token (--map-tiles).
    const tiles = App.token("--map-tiles").replace(/^["']|["']$/g, "") || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
    L.tileLayer(tiles, {
      maxZoom: 9,
      attribution: tiles.includes("arcgisonline") ? "Tiles &copy; Esri" : "&copy; OpenStreetMap contributors",
    }).addTo(map);
    return map;
  }

  /** Points along the great circle from a to b ([lat, lon] pairs). */
  function arc(a, b, n = 40) {
    const [la1, lo1, la2, lo2] = [a[0], a[1], b[0], b[1]].map(rad);
    const d = 2 * Math.asin(Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 +
      Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2));
    if (d < 1e-9) return [a, b];
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const f = i / n, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
      const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
      const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
      const z = A * Math.sin(la1) + B * Math.sin(la2);
      pts.push([deg(Math.atan2(z, Math.hypot(x, y))), deg(Math.atan2(y, x))]);
    }
    return pts;
  }

  /** Shift longitudes by ±360 so a line crossing the date line stays continuous. */
  function unwrap(pts) {
    for (let i = 1; i < pts.length; i++) {
      while (pts[i][1] - pts[i - 1][1] > 180) pts[i][1] -= 360;
      while (pts[i][1] - pts[i - 1][1] < -180) pts[i][1] += 360;
    }
    return pts;
  }

  /**
   * Draws one itinerary into a new layer group and returns it.
   * stops: [{code, lat, lon, label}], style: {color, dashArray, weight}.
   */
  function drawPath(stops, style = {}) {
    const group = L.layerGroup();
    if (stops.length < 2) return group;
    const line = [];
    stops.forEach((s, i) => {
      if (i === 0) return;
      const seg = arc([stops[i - 1].lat, stops[i - 1].lon], [s.lat, s.lon], SEGMENTS);
      if (line.length) seg.shift();
      line.push(...seg);
    });
    unwrap(line);
    // Each leg adds SEGMENTS points, so stop i sits at line[i * SEGMENTS]
    // (using the unwrapped longitude keeps markers on the line).
    const marks = stops.map((s, i) => line[i * SEGMENTS]);
    L.polyline(line, { color: style.color, weight: style.weight || 4, opacity: 0.9,
                       dashArray: style.dashArray, lineCap: "round" }).addTo(group);
    stops.forEach((s, i) => {
      L.circleMarker(marks[i], { radius: i === 0 || i === stops.length - 1 ? 6 : 5, color: style.color,
                                 weight: 2, fillColor: App.token("--surface"), fillOpacity: 1 })
        .bindTooltip(s.label || s.code, { direction: "top" })
        .addTo(group);
    });
    group.bounds = L.latLngBounds(line);
    group.endpoints = [marks[0], marks[marks.length - 1]];
    return group;
  }

  /** A permanent code label (e.g. "DEL") at a position. */
  function label(latlng, text) {
    return L.tooltip({ permanent: true, direction: "right", className: "map-label", offset: [6, 0] })
      .setLatLng(latlng).setContent(text);
  }

  /** Great-circle points from a to b with longitudes unwrapped (one leg). */
  function leg(a, b) {
    return unwrap(arc(a, b, SEGMENTS));
  }

  return { create, arc, leg, drawPath, label };
})();
