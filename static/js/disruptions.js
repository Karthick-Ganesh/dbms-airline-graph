// Disruption simulator: POST /api/disruptions, compare a watched route before
// and after, list events and restore them.
(() => {
  const $ = s => document.querySelector(s);
  let map, drawn = [], closing = [], before = null;

  const watchFrom = App.airportPicker($("#watch-from"), () => refreshWatch());
  const watchTo = App.airportPicker($("#watch-to"), () => refreshWatch());
  const addAirport = App.airportPicker($("#close-airport"), code => {
    if (!closing.includes(code)) closing.push(code);
    $("#close-airport").value = "";
    renderClosing();
  });

  function renderClosing() {
    $("#close-list").innerHTML = closing.map(c =>
      `<button type="button" class="chip" data-code="${App.esc(c)}" title="Remove">${App.esc(c)} ✕</button>`).join("");
  }

  function stops(option, airports) {
    return option.path.map(code => ({ code, lat: airports[code].location.latitude,
      lon: airports[code].location.longitude, label: `${code} · ${airports[code].city}` }));
  }

  /** Current fastest + greenest for the watched route, or {error}. */
  async function routes() {
    const src = watchFrom.get(), dst = watchTo.get();
    if (!src || !dst) return null;
    try {
      const r = await App.api(`/api/routes/find?src=${src}&dst=${dst}&k=1`);
      return { fastest: r.options[0], greenest: r.options[1], airports: r.airports };
    } catch (e) {
      return { error: e.message };
    }
  }

  function draw(now, closed = []) {
    drawn.forEach(l => l.remove());
    drawn = [];
    const add = (opt, airports, color, dash) => {
      if (!opt?.path.length) return;
      const g = RouteMap.drawPath(stops(opt, airports), { color, dashArray: dash });
      g.addTo(map);
      drawn.push(g);
    };
    if (before && now && !now.error) add(before.fastest, before.airports, App.token("--route-before"), "6 8");
    if (now && !now.error) {
      add(now.greenest, now.airports, App.token("--route-greenest"));
      add(now.fastest, now.airports, App.token("--route-fastest"));
    } else if (before) {
      add(before.fastest, before.airports, App.token("--route-before"), "6 8");
    }
    // Closed airports that the old itinerary used.
    const ref = before || now;
    if (ref && !ref.error) {
      ref.fastest.path.filter(c => closed.includes(c)).forEach(c => {
        const a = ref.airports[c];
        RouteMap.wrapped(off => drawn.push(L.circleMarker([a.location.latitude, a.location.longitude + off],
          { radius: 9, color: App.token("--danger"), fillColor: App.token("--danger"), fillOpacity: 0.85 })
          .bindTooltip(`${c} closed`, { permanent: true, direction: "top", className: "map-label" }).addTo(map)));
      });
    }
    const bounds = drawn.filter(l => l.bounds).map(l => l.bounds);
    if (bounds.length) map.fitBounds(L.latLngBounds(bounds).pad(0.15));
  }

  function row(name, b, a) {
    if (!b?.path.length) return "";
    if (!a?.path.length) return `<tr><td>${name}</td><td>${b.path.join(" → ")}</td><td colspan="3" class="muted">no route</td></tr>`;
    const dm = a.journey_minutes - b.journey_minutes, dc = a.co2_per_seat_kg - b.co2_per_seat_kg;
    const same = a.path.join() === b.path.join();
    return `<tr><td>${name}</td>
      <td>${b.path.join(" → ")}<br><span class="muted">${App.minutes(b.journey_minutes)} · ${App.number(b.co2_per_seat_kg, 1)} kg</span></td>
      <td>${same ? '<span class="muted">unchanged</span>' : a.path.join(" → ")}<br><span class="muted">${App.minutes(a.journey_minutes)} · ${App.number(a.co2_per_seat_kg, 1)} kg</span></td>
      <td class="num"><span class="delta ${dm > 0 ? "worse" : "better"}">${dm ? (dm > 0 ? "+" : "−") + App.minutes(Math.abs(dm)) : "–"}</span></td>
      <td class="num"><span class="delta ${dc > 0 ? "worse" : "better"}">${Math.abs(dc) >= 0.1 ? (dc > 0 ? "+" : "−") + App.number(Math.abs(dc), 1) : "–"}</span></td></tr>`;
  }

  function impact(now) {
    const ref = before || now;
    if (!ref) { $("#impact").innerHTML = ""; return; }
    if (ref.error) { $("#impact").innerHTML = `<p class="muted small">${App.esc(ref.error)}</p>`; return; }
    if (!before || before === now) {
      $("#impact").innerHTML = `<table><thead><tr><th></th><th>Current itinerary</th><th class="num">Time</th><th class="num">kg CO₂</th></tr></thead><tbody>
        ${[["Fastest", now.fastest], ["Greenest", now.greenest]].map(([n, o]) => o.path.length
          ? `<tr><td>${n}</td><td>${o.path.join(" → ")}</td><td class="num">${App.minutes(o.journey_minutes)}</td><td class="num">${App.number(o.co2_per_seat_kg, 1)}</td></tr>`
          : `<tr><td>${n}</td><td colspan="3" class="muted">no route</td></tr>`).join("")}</tbody></table>`;
      return;
    }
    $("#impact").innerHTML = now.error
      ? `<p class="delta worse">${App.esc(now.error)}</p>`
      : `<table><thead><tr><th></th><th>Before</th><th>After</th><th class="num">Δ time</th><th class="num">Δ kg CO₂</th></tr></thead>
         <tbody>${row("Fastest", before.fastest, now.fastest)}${row("Greenest", before.greenest, now.greenest)}</tbody></table>`;
  }

  async function refreshWatch() {
    before = null;
    const now = await routes();
    impact(now);
    draw(now);
  }

  function status(e) {
    if (e.still_closed) return '<span class="badge badge-closed">Active closure</span>';
    if (!e.end_time) return '<span class="badge badge-closed">Ongoing</span>';
    if (e.data_source === "MANUAL") return '<span class="badge badge-active">Restored</span>';
    return '<span class="badge">Historical</span>';
  }

  async function loadEvents() {
    const events = await App.api("/api/events").catch(e => { App.toast(e.message, "error"); return []; });
    $("#events").innerHTML = events.length ? `<div class="table-wrap"><table>
      <thead><tr><th>Event</th><th>Status</th><th class="num">Airports</th><th></th></tr></thead><tbody>
      ${events.map(e => `<tr>
        <td><strong>${App.esc(e.event_id)}</strong><br><span class="small muted">${App.esc(e.event_type)} · ${App.esc(e.severity)} · ${App.esc(e.data_source)}<br>${App.esc(e.description || "")}</span></td>
        <td>${status(e)}<br><span class="small muted">${App.esc((e.start_time || "").slice(0, 16).replace("T", " "))}</span></td>
        <td class="num">${e.airports}</td>
        <td><div class="chips">
          <a class="chip" href="/network.html?event=${encodeURIComponent(e.event_id)}">Graph</a>
          ${e.still_closed ? `<button class="chip admin-only" data-restore="${App.esc(e.event_id)}">Restore</button>` : ""}
          ${e.data_source === "MANUAL" ? `<button class="chip admin-only" data-delete="${App.esc(e.event_id)}">Delete</button>` : ""}
        </div></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">No events.</div>';
  }

  async function simulate(e) {
    e.preventDefault();
    const region = $("#region").value;
    if (!closing.length && !region) { App.toast("Add at least one airport or pick a region.", "error"); return; }
    if (region && !confirm(`Close every active airport in ${region}? You can restore it afterwards.`)) return;
    const btn = $("#run");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Closing…';
    try {
      before = await routes();
      const res = await App.api("/api/disruptions", { method: "POST", body: {
        airports: closing, region: region || null, severity: $("#severity").value,
        event_type: $("#event-type").value, description: $("#description").value.trim() || null } });
      App.toast(`${res.event_id}: ${res.closed_airports.length} airport(s) closed, ${res.disrupted_routes} routes disrupted`, "success", 7000);
      const now = await routes();
      impact(now);
      draw(now, res.closed_airports);
      closing = [];
      renderClosing();
      $("#region").value = "";
      loadEvents();
    } catch (err) {
      App.toast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Simulate closure";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    map = RouteMap.create("map");
    watchFrom.set("BOM", "Mumbai");
    watchTo.set("JFK", "New York");
    closing = ["DXB"];
    renderClosing();
    refreshWatch();
    loadEvents();

    $("#simulate").addEventListener("submit", simulate);
    $("#close-list").addEventListener("click", e => {
      const chip = e.target.closest("[data-code]");
      if (chip) { closing = closing.filter(c => c !== chip.dataset.code); renderClosing(); }
    });
    $("#events").addEventListener("click", async e => {
      const restore = e.target.closest("[data-restore]")?.dataset.restore;
      const del = e.target.closest("[data-delete]")?.dataset.delete;
      if (restore) {
        await App.attempt(async () => {
          const r = await App.api(`/api/disruptions/${encodeURIComponent(restore)}/restore`, { method: "POST" });
          App.toast(`Restored ${r.reopened_airports} airport(s) and ${r.restored_routes} routes`, "success");
          const now = await routes();
          impact(now);
          draw(now);
          loadEvents();
        });
      }
      if (del && confirm(`Delete event ${del}? Anything it still closes is restored first.`)) {
        await App.attempt(async () => {
          await App.api(`/api/events/${encodeURIComponent(del)}`, { method: "DELETE" });
          App.toast(`Deleted ${del}`, "success");
          loadEvents();
          refreshWatch();
        });
      }
    });
  });
})();
