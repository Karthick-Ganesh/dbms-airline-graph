// Route finder: GET /api/routes/find and draw every option on the map.
(() => {
  const EXAMPLES = [["DEL", "JFK"], ["BOM", "SFO"], ["BLR", "LHR"], ["MAA", "SYD"], ["IXL", "LIS"]];
  const COLOR_TOKEN = { fastest: "--route-fastest", greenest: "--route-greenest", shortest: "--route-shortest" };

  const $ = s => document.querySelector(s);
  let map, layers = [], labels = [], selected = null, result = null;

  const from = App.airportPicker($("#from"));
  const to = App.airportPicker($("#to"));

  function colorOf(option) {
    return App.token(COLOR_TOKEN[option.key] || "--route-alt");
  }

  function stopsOf(option, airports) {
    return option.path.map(code => {
      const a = airports[code];
      return { code, lat: a.location.latitude, lon: a.location.longitude,
               label: `${code} · ${a.name}, ${a.city}` };
    });
  }

  function samePath(a, b) {
    return a.path.length && a.path.join() === b.path.join();
  }

  const HOP = '<svg class="hop" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z"/></svg>';

  function delta(option, base) {
    if (option === base || !base.path.length || !option.path.length) return "";
    const parts = [];
    const dm = option.journey_minutes - base.journey_minutes, dc = option.co2_per_seat_kg - base.co2_per_seat_kg;
    if (Math.abs(dm) >= 1) parts.push(`<span class="delta ${dm < 0 ? "better" : "worse"}">${dm > 0 ? "+" : "−"}${App.minutes(Math.abs(dm))}</span>`);
    if (Math.abs(dc) >= 0.1) parts.push(`<span class="delta ${dc < 0 ? "better" : "worse"}">${dc > 0 ? "+" : "−"}${App.number(Math.abs(dc), 1)} kg CO₂</span>`);
    return parts.length ? `<div class="note">vs fastest: ${parts.join(" · ")}</div>` : "";
  }

  // Short algorithm names for the ticket strip (full text is the strip's tooltip).
  const SHORT_ALGO = { fastest: "Dijkstra · time", greenest: "Dijkstra · CO₂", shortest: "A* · distance" };

  const stubRow = (label, value) =>
    `<div class="stub-row"><span class="field-label">${label}</span><span class="v">${value}</span></div>`;

  /** One itinerary printed as a boarding pass: route on the left, tear-off stub on the right. */
  function card(option, all, airports) {
    const color = colorOf(option);
    if (!option.path.length) {
      return `<div class="ticket" style="--option-color:${color}">
        <div class="ticket-main"><div class="ticket-strip"><span>${App.esc(option.label)}</span></div>
          <div class="ticket-body muted small">No route found on the active network.</div></div>
        <div class="ticket-stub"><div class="ticket-strip">—</div></div></div>`;
    }
    const twin = all.find(o => o !== option && samePath(o, option) && all.indexOf(o) < all.indexOf(option));
    const city = code => airports[code]?.city || code;
    const legs = option.legs.map(l => `<tr>
        <td>${App.esc(l.from)} → ${App.esc(l.to)}</td>
        <td class="num">${App.minutes(l.minutes)}</td>
        <td class="num">${App.number(l.km)}</td>
        <td class="num">${App.number(l.co2_per_seat_kg, 1)}</td>
        <td>${App.esc((l.carriers || []).slice(0, 4).join(", "))}</td></tr>`).join("");
    const stops = option.path.length - 2;
    return `<div class="ticket option" data-key="${option.key}" style="--option-color:${color}" tabindex="0">
      <div class="ticket-main">
        <div class="ticket-strip" title="${App.esc(option.algorithm)}"><span>${App.esc(option.label)}</span><span class="algo">${SHORT_ALGO[option.key] || "Yen's k-shortest"}</span></div>
        <div class="ticket-body">
          <div class="route-codes">${option.path.map(App.esc).join(HOP)}</div>
          <div class="route-cities">${App.esc(city(option.path[0]))} to ${App.esc(city(option.path[option.path.length - 1]))}
            · ${stops ? `${stops} stop${stops > 1 ? "s" : ""} via ${App.esc(option.path.slice(1, -1).map(city).join(", "))}` : "nonstop"}</div>
          ${twin ? `<div class="note">Same itinerary as ${App.esc(twin.label)}</div>` : ""}
          ${delta(option, all[0])}
          <details class="legs"><summary>Legs</summary>
            <div class="table-wrap"><table>
              <thead><tr><th>Leg</th><th class="num">Time</th><th class="num">km</th><th class="num">kg CO₂</th><th>Carriers</th></tr></thead>
              <tbody>${legs}</tbody></table></div>
          </details>
        </div>
      </div>
      <div class="ticket-stub">
        <div class="ticket-strip">Stub</div>
        <div class="ticket-body">
          ${stubRow("Journey", App.minutes(option.journey_minutes))}
          ${stubRow("CO₂ / seat", `${App.number(option.co2_per_seat_kg, 1)} kg`)}
          ${stubRow("Distance", `${App.number(option.km)} km`)}
          ${stubRow("Flights", option.flights)}
        </div>
      </div>
    </div>`;
  }

  function headline(options) {
    const [fast, green] = options;
    if (!fast.path.length) return "";
    if (samePath(fast, green)) return `<p>The fastest itinerary is also the greenest.</p>`;
    const saved = fast.co2_per_seat_kg - green.co2_per_seat_kg;
    const pct = 100 * saved / fast.co2_per_seat_kg;
    const extra = green.journey_minutes - fast.journey_minutes;
    return `<p>The greenest itinerary saves <strong>${App.number(saved, 1)} kg CO₂ per seat</strong>
      (${App.number(pct)}%) for <strong>${extra >= 0 ? App.minutes(extra) + " more" : App.minutes(-extra) + " less"}</strong> journey time.</p>`;
  }

  function render() {
    const { options, alternatives, airports } = result;
    const all = [...options, ...alternatives];
    $("#results").innerHTML = `
      <div class="card"><h2>${App.esc(result.src)} → ${App.esc(result.dst)} · Itinerary summary</h2>${headline(options)}
        <p class="small muted" style="margin:0">Journey time is the scheduled flight time plus 90 minutes for each connection.
        CO₂ per seat includes one take-off/landing cycle per leg.</p></div>
      <div class="section-label">Optimised routes</div>
      ${options.map(o => card(o, all, airports)).join("")}
      <div class="section-label">Alternatives (Yen's k-shortest, by time)</div>
      ${alternatives.length ? alternatives.map(o => card(o, all, airports)).join("") : '<div class="card empty small">No alternative itineraries.</div>'}`;

    layers.forEach(l => l.remove());
    labels.forEach(l => l.remove());
    layers = [];
    labels = [];
    // Draw back to front: alternatives first, fastest on top.
    [...all].reverse().forEach(o => {
      const twin = all.find(x => x !== o && samePath(x, o) && all.indexOf(x) < all.indexOf(o));
      const group = RouteMap.drawPath(stopsOf(o, airports), {
        color: colorOf(o), weight: o.key.startsWith("alt") ? 3 : 4,
        dashArray: twin || o.key.startsWith("alt") ? "6 8" : null,
      });
      group.key = o.key;
      group.addTo(map);
      layers.push(group);
    });
    const main = layers.find(l => l.key === "fastest" && l.bounds) || layers.find(l => l.bounds);
    if (main) {
      main.endpoints.forEach((p, i) => labels.push(RouteMap.label(p, i ? result.dst : result.src).addTo(map)));
      map.fitBounds(L.latLngBounds(layers.filter(l => l.bounds).map(l => l.bounds)).pad(0.15));
    }

    document.querySelectorAll(".option[data-key]").forEach(el => {
      const choose = () => select(selected === el.dataset.key ? null : el.dataset.key);
      el.addEventListener("click", e => { if (!e.target.closest("details")) choose(); });
      el.addEventListener("keydown", e => { if (e.key === "Enter") choose(); });
    });
    select(null);
  }

  /** Highlight one option (or none) on the map and in the list. */
  function select(key) {
    selected = key;
    layers.forEach(group => group.eachLayer(l => l.setStyle?.({ opacity: !key || group.key === key ? 0.9 : 0.15,
                                                                  fillOpacity: !key || group.key === key ? 1 : 0.15 })));
    const chosen = layers.find(l => l.key === key);
    if (chosen?.bounds) {
      chosen.eachLayer(l => l.bringToFront?.());
      map.fitBounds(chosen.bounds.pad(0.2));
    }
    document.querySelectorAll(".option[data-key]").forEach(el =>
      el.classList.toggle("dimmed", !!key && el.dataset.key !== key));
  }

  async function search(src, dst) {
    if (!src || !dst) { App.toast("Choose both airports from the list (or type a 3-letter code).", "error"); return; }
    if (src === dst) { App.toast("Origin and destination are the same airport.", "error"); return; }
    const btn = $("#go");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Searching…';
    try {
      result = await App.api(`/api/routes/find?src=${src}&dst=${dst}`);
      from.set(src, result.airports[src]?.city);
      to.set(dst, result.airports[dst]?.city);
      history.replaceState(null, "", `?from=${src}&to=${dst}`);
      render();
    } catch (e) {
      App.toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Find routes";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    map = RouteMap.create("map");
    $("#examples").innerHTML = EXAMPLES.map(([a, b]) =>
      `<button type="button" class="chip" data-from="${a}" data-to="${b}">${a} → ${b}</button>`).join("");
    $("#examples").addEventListener("click", e => {
      const chip = e.target.closest(".chip");
      if (chip) search(chip.dataset.from, chip.dataset.to);
    });
    $("#swap").addEventListener("click", () => {
      const a = from.get(), b = to.get(), av = $("#from").value, bv = $("#to").value;
      $("#from").value = bv; $("#from").dataset.code = b;
      $("#to").value = av; $("#to").dataset.code = a;
    });
    $("#search").addEventListener("submit", e => { e.preventDefault(); search(from.get(), to.get()); });

    const params = new URLSearchParams(location.search);
    if (params.get("from") && params.get("to")) search(params.get("from").toUpperCase(), params.get("to").toUpperCase());
  });
})();
