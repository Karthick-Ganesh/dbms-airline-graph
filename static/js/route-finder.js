// Route finder: GET /api/routes/find and draw every option on the map.
(() => {
  const EXAMPLES = [["DEL", "JFK"], ["BOM", "SFO"], ["BLR", "LHR"], ["MAA", "SYD"], ["IXL", "LIS"]];
  const COLOR_TOKEN = { fastest: "--route-fastest", greenest: "--route-greenest", shortest: "--route-shortest",
                        unconstrained: "--route-before" };

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
  const SHORT_ALGO = { fastest: "Least time", greenest: "Least CO₂ in limit", shortest: "Least km in limit",
                       unconstrained: "GDS Dijkstra" };

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
        <div class="ticket-strip" title="${App.esc(option.algorithm)}"><span>${App.esc(option.label)}</span><span class="algo">${SHORT_ALGO[option.key] || "Next fastest"}</span></div>
        <div class="ticket-body">
          <div class="route-codes">${option.path.map(App.esc).join(HOP)}</div>
          <div class="route-cities">${App.esc(city(option.path[0]))} to ${App.esc(city(option.path[option.path.length - 1]))}
            · ${stops ? `${stops} stop${stops > 1 ? "s" : ""} via ${App.esc(option.path.slice(1, -1).map(city).join(", "))}` : "nonstop"}</div>
          ${twin ? `<div class="note">Same itinerary as ${App.esc(twin.label)}</div>` : ""}
          ${typicalBadge(option)}
          ${delta(option, all[0])}
          ${option.key === "unconstrained" ? unbookable(option) : ""}
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

  /** "−13% CO₂ vs typical", like booking sites' emissions labels. */
  function typicalBadge(option) {
    const p = option.vs_typical_pct;
    if (p == null) return "";
    const cls = p <= -5 ? "better" : p >= 5 ? "worse" : "";
    return `<span class="eco-badge ${cls}">${p === 0 ? "Typical" : `${p > 0 ? "+" : "−"}${Math.abs(p)}%`} CO₂ vs typical</span>`;
  }

  /** Where each option sits on the CO₂ range of all bookable itineraries. */
  function co2Range() {
    const L = result.limits;
    if (L.co2_min == null) return "";
    const span = Math.max(1, L.co2_max - L.co2_min);
    const at = v => `${Math.min(100, Math.max(0, 100 * (v - L.co2_min) / span))}%`;
    const marks = result.options.map(o =>
      `<span class="range-mark" style="left:${at(o.co2_per_seat_kg)}; --option-color:${colorOf(o)}" title="${App.esc(o.label)}: ${App.number(o.co2_per_seat_kg, 1)} kg"></span>`).join("");
    const saved = L.co2_max - L.co2_min;
    return `<div class="co2-range">
      <div class="field-label muted">Your choice matters: CO₂ per seat of the ${App.number(L.within_limit)} bookable itineraries</div>
      <div class="range-bar"><span class="range-typical" style="left:${at(L.co2_typical)}" title="Typical"></span>${marks}</div>
      <div class="range-labels"><span>${App.number(L.co2_min, 1)} kg</span><span>typical ${App.number(L.co2_typical, 1)} kg</span><span>${App.number(L.co2_max, 1)} kg</span></div>
      <p class="small" style="margin: var(--space-2) 0 0">Picking the cleanest instead of the dirtiest bookable itinerary saves
        <strong>${App.number(saved, 1)} kg CO₂</strong> per seat (${App.number(100 * saved / L.co2_max)}%).</p>
    </div>`;
  }

  /** Why the unconstrained optimum isn't offered as a bookable itinerary. */
  function unbookable(option) {
    const L = result.limits, why = [];
    const stops = option.flights - 1;
    if (stops > L.max_stops) why.push(`${stops} stops (limit ${L.max_stops})`);
    if (L.fastest_min != null && option.journey_minutes > L.fastest_min + L.max_extra_min)
      why.push(`${App.minutes(option.journey_minutes - L.fastest_min)} longer than the fastest (limit +${App.minutes(L.max_extra_min)})`);
    return `<div class="note">${why.length ? "Not bookable under your limits: " + why.join(", ")
      : "Outside the 2× detour search area"}. Shown for comparison.</div>`;
  }

  function limitsLine() {
    const L = result.limits;
    if (!L.candidates) return `<p class="delta worse">No itinerary with at most ${L.max_stops} stop${L.max_stops === 1 ? "" : "s"}
      between these airports. Allow more stops, or see the unconstrained optimum below.</p>`;
    return `<p class="small muted" style="margin:0">Bookable itineraries only: at most ${L.max_stops} stop${L.max_stops === 1 ? "" : "s"};
      greenest and shortest within <strong>+${App.minutes(L.max_extra_min)}</strong> of the fastest
      (${App.number(L.candidates)} itineraries checked, ${App.number(L.within_limit)} within the limit).</p>`;
  }

  function headline(options) {
    const [fast, green] = options;
    if (!fast?.path.length) return "";
    if (samePath(fast, green)) return `<p>The fastest itinerary is also the greenest.</p>`;
    const saved = fast.co2_per_seat_kg - green.co2_per_seat_kg;
    const pct = 100 * saved / fast.co2_per_seat_kg;
    const extra = green.journey_minutes - fast.journey_minutes;
    return `<p>The greenest itinerary saves <strong>${App.number(saved, 1)} kg CO₂ per seat</strong>
      (${App.number(pct)}%) for <strong>${extra >= 0 ? App.minutes(extra) + " more" : App.minutes(-extra) + " less"}</strong> journey time.</p>`;
  }

  function render() {
    const { options, alternatives, airports, unconstrained } = result;
    const all = [...options, ...alternatives, ...(unconstrained ? [unconstrained] : [])];
    $("#results").innerHTML = `
      <div class="card"><h2>${App.esc(result.src)} → ${App.esc(result.dst)} · Itinerary summary</h2>${headline(options)}
        ${limitsLine()}
        ${co2Range()}
        <p class="small muted" style="margin: var(--space-2) 0 0">Journey time is the scheduled flight time plus 90 minutes for each connection.
        CO₂ per seat includes one take-off/landing cycle per leg.</p></div>
      ${options.length ? `<div class="section-label">Optimised routes</div>${options.map(o => card(o, all, airports)).join("")}` : ""}
      ${options.length ? `<div class="section-label">Alternatives (next fastest)</div>
        ${alternatives.length ? alternatives.map(o => card(o, all, airports)).join("") : '<div class="card empty small">No other itineraries within the limits.</div>'}` : ""}
      ${unconstrained ? `<div class="section-label">For comparison · unconstrained optimum</div>${card(unconstrained, all, airports)}` : ""}`;

    layers.forEach(l => l.remove());
    labels.forEach(l => l.remove());
    layers = [];
    labels = [];
    // Draw back to front: alternatives first, fastest on top.
    [...all].reverse().forEach(o => {
      const twin = all.find(x => x !== o && samePath(x, o) && all.indexOf(x) < all.indexOf(o));
      const minor = o.key.startsWith("alt") || o.key === "unconstrained";
      const group = RouteMap.drawPath(stopsOf(o, airports), {
        color: colorOf(o), weight: minor ? 3 : 4, dashArray: twin || minor ? "6 8" : null,
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
      const stops = $("#stops").value, extra = $("#extra").value;
      result = await App.api(`/api/routes/find?src=${src}&dst=${dst}&max_stops=${stops}${extra ? `&max_extra=${extra}` : ""}`);
      from.set(src, result.airports[src]?.city);
      to.set(dst, result.airports[dst]?.city);
      history.replaceState(null, "", `?from=${src}&to=${dst}&stops=${stops}${extra ? `&extra=${extra}` : ""}`);
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

    // Changing a limit re-runs the current search.
    ["#stops", "#extra"].forEach(sel => $(sel).addEventListener("change", () => {
      if (result) search(result.src, result.dst);
    }));

    const params = new URLSearchParams(location.search);
    if (params.get("stops")) $("#stops").value = params.get("stops");
    if (params.get("extra")) $("#extra").value = params.get("extra");
    if (params.get("from") && params.get("to")) search(params.get("from").toUpperCase(), params.get("to").toUpperCase());
  });
})();
