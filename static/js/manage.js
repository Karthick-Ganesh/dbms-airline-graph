// Data management (admin): CRUD screens over the /api endpoints, one tab per entity.
(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const panel = () => $("#panel");
  const api = App.api;
  const enc = encodeURIComponent;

  // ---------------------------------------------------------------- form helpers
  // Field spec: {name, label, type, options, required, placeholder, step, createOnly, help}

  function input(f, value) {
    const v = value ?? "";
    const common = `name="${f.name}" id="f-${f.name}" ${f.required ? "required" : ""} ${f.readonly ? "readonly" : ""}
                    placeholder="${App.esc(f.placeholder || "")}"`;
    if (f.type === "select") {
      return `<select ${common}>${f.options.map(o => {
        const [val, text] = Array.isArray(o) ? o : [o, o];
        return `<option value="${App.esc(val)}" ${String(v) === String(val) ? "selected" : ""}>${App.esc(text)}</option>`;
      }).join("")}</select>`;
    }
    if (f.type === "checkbox") {
      return `<input type="checkbox" ${common} ${v ? "checked" : ""} style="width:auto">`;
    }
    if (f.type === "datetime") {
      return `<input type="datetime-local" ${common} value="${App.esc(String(v).slice(0, 16))}">`;
    }
    return `<input type="${f.type || "text"}" ${common} value="${App.esc(v)}" ${f.step ? `step="${f.step}"` : ""}>`;
  }

  function formHtml(id, fields, values = {}, { submit = "Save", editing = false } = {}) {
    return `<form id="${id}" class="crud-form">
      <div class="form-grid">
        ${fields.filter(f => !(editing && f.createOnly)).map(f => `<div class="field ${f.wide ? "wide" : ""}">
          <label for="f-${f.name}">${App.esc(f.label)}${f.required ? " *" : ""}</label>
          ${input({ ...f, readonly: editing && f.key }, values[f.name])}
          ${f.help ? `<div class="small muted">${App.esc(f.help)}</div>` : ""}</div>`).join("")}
      </div>
      <div class="chips"><button class="btn">${App.esc(submit)}</button>
        ${editing ? '<button type="button" class="btn btn-secondary" data-cancel>Cancel</button>' : ""}</div>
    </form>`;
  }

  /** Form values typed per field spec; empty optional fields are left out. */
  function readForm(form, fields) {
    const out = {};
    for (const f of fields) {
      const el = form.elements[f.name];
      if (!el) continue;
      if (f.type === "checkbox") { out[f.name] = el.checked; continue; }
      const v = el.value.trim();
      if (v === "") continue;
      if (f.type === "number") out[f.name] = Number(v);
      else if (f.type === "datetime") out[f.name] = v.length === 16 ? `${v}:00Z` : v;
      else if (f.upper) out[f.name] = v.toUpperCase();
      else out[f.name] = v;
    }
    return out;
  }

  function rowsTable(rows, cols, actions) {
    if (!rows.length) return '<div class="empty small">Nothing found.</div>';
    return `<div class="table-wrap"><table>
      <thead><tr>${cols.map(([, label]) => `<th>${App.esc(label)}</th>`).join("")}<th></th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr>${cols.map(([key]) => `<td>${fmt(r[key])}</td>`).join("")}
        <td><div class="chips">${actions(r, i)}</div></td></tr>`).join("")}</tbody></table></div>`;
  }

  function fmt(v) {
    if (v == null) return '<span class="muted">–</span>';
    if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return App.esc(v.slice(0, 16).replace("T", " "));
    if (["ACTIVE", "CLOSED", "RESTRICTED", "DISRUPTED", "CANCELLED"].includes(v))
      return `<span class="badge badge-${v.toLowerCase()}">${v}</span>`;
    if (Array.isArray(v)) return App.esc(v.join(", "));
    return App.esc(v);
  }

  const chip = (label, data) =>
    `<button type="button" class="chip" ${Object.entries(data).map(([k, v]) => `data-${k}="${App.esc(v)}"`).join(" ")}>${label}</button>`;

  /** Two-column tab layout: list on the left, form on the right. */
  function layout(left, right) {
    panel().innerHTML = `<div class="split manage-split"><div class="card" id="left">${left}</div>
                         <div class="card" id="right">${right}</div></div>`;
  }

  async function act(fn, success) {
    try {
      const r = await fn();
      if (success) App.toast(typeof success === "function" ? success(r) : success, "success");
      return r ?? true;
    } catch (e) {
      App.toast(e.message, "error");
      return null;
    }
  }

  // ---------------------------------------------------------------- Airports

  const AIRPORT_FIELDS = [
    { name: "airport_id", label: "IATA code", required: true, key: true, upper: true, placeholder: "ZZZ" },
    { name: "name", label: "Name", required: true, wide: true },
    { name: "city", label: "City", required: true },
    { name: "country", label: "Country code", required: true, upper: true, placeholder: "IN" },
    { name: "latitude", label: "Latitude", type: "number", step: "any", required: true, createOnly: true },
    { name: "longitude", label: "Longitude", type: "number", step: "any", required: true, createOnly: true },
    { name: "region", label: "Region", type: "select",
      options: ["", "Africa", "Asia", "Europe", "North America", "Oceania", "South America"] },
    { name: "timezone", label: "Timezone", placeholder: "Asia/Kolkata" },
    { name: "elevation_m", label: "Elevation (m)", type: "number", step: "any" },
    { name: "runway_count", label: "Runways", type: "number" },
    { name: "daily_capacity", label: "Daily capacity", type: "number" },
    { name: "is_hub", label: "Large hub", type: "checkbox" },
  ];

  async function airports() {
    layout(`<h2>Find airports</h2>
      <form id="find" class="inline-form">
        <input name="q" placeholder="Name, city or code">
        <select name="status"><option value="">Any status</option><option>ACTIVE</option><option>RESTRICTED</option><option>CLOSED</option></select>
        <button class="btn btn-secondary">Search</button>
      </form><div id="list" style="margin-top:var(--space-3)"></div>`, "");
    const newForm = () => {
      $("#right").innerHTML = `<h2>New airport</h2>${formHtml("form", AIRPORT_FIELDS, { daily_capacity: 100 }, { submit: "Create airport" })}`;
      $("#form").addEventListener("submit", async e => {
        e.preventDefault();
        const r = await act(() => api("/api/airports", { method: "POST", body: readForm(e.target, AIRPORT_FIELDS) }),
                            r => `Created ${r.airport.airport_id}`);
        if (r) { $("#find").q.value = r.airport.airport_id; search(); }
      });
    };
    const edit = async code => {
      const a = await act(() => api(`/api/airports/${enc(code)}`));
      if (!a) return;
      $("#right").innerHTML = `<h2>Edit ${App.esc(code)}</h2>
        <p class="small muted">${a.routes_out} outgoing routes · hub of ${App.esc(a.hub_of.join(", ") || "none")}</p>
        ${formHtml("form", AIRPORT_FIELDS, a.airport, { editing: true })}`;
      $("#form").addEventListener("submit", async e => {
        e.preventDefault();
        const body = readForm(e.target, AIRPORT_FIELDS);
        delete body.airport_id;
        if (await act(() => api(`/api/airports/${enc(code)}`, { method: "PUT", body }), `Updated ${code}`)) search();
      });
      $("[data-cancel]").addEventListener("click", newForm);
    };
    const search = async () => {
      const f = $("#find"), q = f.q.value.trim(), status = f.status.value;
      const rows = q
        ? await act(() => api(`/api/airports/search?q=${enc(q)}&limit=25`).then(async hits =>
            Promise.all(hits.map(h => api(`/api/airports/${h.airport_id}`).then(a => ({ ...a.airport, status: a.airport.operational_status }))))))
        : await act(() => api(`/api/airports?limit=50${status ? `&status=${status}` : ""}`));
      const shown = (rows || []).filter(r => !status || r.status === status);
      $("#list").innerHTML = rowsTable(shown, [["airport_id", "Code"], ["name", "Name"], ["city", "City"], ["status", "Status"]],
        r => chip("Edit", { edit: r.airport_id })
          + (r.status === "ACTIVE" ? chip("Close", { close: r.airport_id }) : chip("Reopen", { reopen: r.airport_id }))
          + chip("Graph", { graph: r.airport_id }) + chip("Delete", { del: r.airport_id }));
    };
    $("#find").addEventListener("submit", e => { e.preventDefault(); search(); });
    $("#left").addEventListener("click", async e => {
      const d = e.target.closest("button")?.dataset || {};
      if (d.edit) edit(d.edit);
      if (d.graph) location.href = `/network.html?airport=${enc(d.graph)}`;
      if (d.close && await act(() => api(`/api/airports/${enc(d.close)}/status`, { method: "PUT", body: { status: "CLOSED" } }),
                               `${d.close} closed (soft delete)`)) search();
      if (d.reopen && await act(() => api(`/api/airports/${enc(d.reopen)}/status`, { method: "PUT", body: { status: "ACTIVE" } }),
                                `${d.reopen} reopened`)) search();
      if (d.del && confirm(`Permanently delete airport ${d.del}? Only airports without routes can be deleted.`)
          && await act(() => api(`/api/airports/${enc(d.del)}`, { method: "DELETE" }), `${d.del} deleted`)) { search(); newForm(); }
    });
    newForm();
    search();
  }

  // ---------------------------------------------------------------- Airlines

  const AIRLINE_FIELDS = [
    { name: "airline_id", label: "IATA code", required: true, key: true, upper: true, placeholder: "9Z" },
    { name: "name", label: "Name", required: true, wide: true },
    { name: "country", label: "Country", required: true },
    { name: "fleet_size", label: "Fleet size", type: "number" },
    { name: "alliance", label: "Alliance", type: "select", options: ["", "Star Alliance", "oneworld", "SkyTeam"] },
  ];

  async function airlines() {
    layout(`<h2>Find airlines</h2>
      <form id="find" class="inline-form"><input name="q" placeholder="Code or name"><button class="btn btn-secondary">Search</button></form>
      <div id="list" style="margin-top:var(--space-3)"></div>`, "");
    const newForm = () => {
      $("#right").innerHTML = `<h2>New airline</h2>${formHtml("form", AIRLINE_FIELDS, {}, { submit: "Create airline" })}`;
      $("#form").addEventListener("submit", async e => {
        e.preventDefault();
        const r = await act(() => api("/api/airlines", { method: "POST", body: readForm(e.target, AIRLINE_FIELDS) }),
                            r => `Created ${r.airline.airline_id}`);
        if (r) { $("#find").q.value = r.airline.airline_id; search(); }
      });
    };
    const edit = async id => {
      const a = await act(() => api(`/api/airlines/${enc(id)}`));
      if (!a) return;
      $("#right").innerHTML = `<h2>Edit ${App.esc(id)}</h2>${formHtml("form", AIRLINE_FIELDS, a.airline, { editing: true })}`;
      $("#form").addEventListener("submit", async e => {
        e.preventDefault();
        const body = readForm(e.target, AIRLINE_FIELDS);
        delete body.airline_id;
        if (await act(() => api(`/api/airlines/${enc(id)}`, { method: "PUT", body }), `Updated ${id}`)) search();
      });
      $("[data-cancel]").addEventListener("click", newForm);
    };
    const search = async () => {
      const q = $("#find").q.value.trim();
      const rows = await act(() => api(`/api/airlines?limit=50${q ? `&q=${enc(q)}` : ""}`)) || [];
      $("#list").innerHTML = rowsTable(rows, [["airline_id", "Code"], ["name", "Name"], ["country", "Country"], ["flights", "Flights"]],
        r => chip("Edit", { edit: r.airline_id }) + chip("Delete", { del: r.airline_id }));
    };
    $("#find").addEventListener("submit", e => { e.preventDefault(); search(); });
    $("#left").addEventListener("click", async e => {
      const d = e.target.closest("button")?.dataset || {};
      if (d.edit) edit(d.edit);
      if (d.del && confirm(`Delete airline ${d.del} and its OPERATES / HUB_OF links?`)
          && await act(() => api(`/api/airlines/${enc(d.del)}`, { method: "DELETE" }), `${d.del} deleted`)) { search(); newForm(); }
    });
    newForm();
    search();
  }

  // ---------------------------------------------------------------- Flights

  let aircraftOptions = null;
  async function aircraftList() {
    aircraftOptions ??= (await api("/api/aircraft")).map(a =>
      [a.aircraft_type_id, `${a.manufacturer} ${a.model} (${a.aircraft_type_id})`]);
    return aircraftOptions;
  }

  async function flights() {
    const aircraft = await act(aircraftList) || [];
    const FLIGHT_FIELDS = [
      { name: "airline_id", label: "Airline code", required: true, upper: true, placeholder: "AI" },
      { name: "flight_number", label: "Flight number", required: true, upper: true, placeholder: "AI101" },
      { name: "origin", label: "Origin", required: true, upper: true, placeholder: "DEL" },
      { name: "destination", label: "Destination", required: true, upper: true, placeholder: "BOM" },
      { name: "aircraft_type_id", label: "Aircraft", type: "select", options: aircraft, required: true, wide: true },
      { name: "tail_number", label: "Tail number", upper: true, placeholder: "VT-ABC" },
      { name: "scheduled_departure", label: "Departure (UTC)", type: "datetime", required: true },
      { name: "scheduled_arrival", label: "Arrival (UTC)", type: "datetime", required: true },
    ];
    layout(`<h2>Find flights</h2>
      <form id="find" class="inline-form">
        <input name="airline_id" placeholder="Airline" size="4">
        <input name="origin" placeholder="Origin" size="4">
        <select name="status"><option value="">Any status</option><option>SCHEDULED</option><option>COMPLETED</option>
          <option>DELAYED</option><option>CANCELLED</option><option>DIVERTED</option></select>
        <select name="data_source"><option value="">Any source</option><option>BTS_2015</option><option>SYNTHETIC</option><option>MANUAL</option></select>
        <button class="btn btn-secondary">Search</button>
      </form><div id="list" style="margin-top:var(--space-3)"></div>`, "");

    const newForm = () => {
      $("#right").innerHTML = `<h2>New flight</h2>
        <p class="small muted">Creates the :Flight node and its OPERATES, DEPARTS_FROM, ARRIVES_AT and USES_AIRCRAFT relationships in one transaction.</p>
        ${formHtml("form", FLIGHT_FIELDS, {}, { submit: "Create flight" })}`;
      $("#form").addEventListener("submit", async e => {
        e.preventDefault();
        const body = readForm(e.target, FLIGHT_FIELDS);
        body.flight_id = `${body.flight_number}-${body.scheduled_departure.slice(0, 10).replaceAll("-", "")}`;
        const r = await act(() => api("/api/flights", { method: "POST", body }), r => `Created ${r.flight.flight_id}`);
        if (r) { $("#find").airline_id.value = body.airline_id; search(); }
      });
    };
    const edit = async id => {
      const f = await act(() => api(`/api/flights/${enc(id)}`));
      if (!f) return;
      $("#right").innerHTML = `<h2>${App.esc(id)}</h2>
        <p class="small muted">${App.esc(f.airline)} · ${App.esc(f.origin)} → ${App.esc(f.destination)} · ${App.esc(f.aircraft)}
          ${f.tail_number ? "· " + App.esc(f.tail_number) : ""}<br>Departs ${fmt(f.flight.scheduled_departure)} · ${fmt(f.flight.status)}</p>
        <h3>Record actual departure</h3>
        <form id="dep" class="inline-form"><input type="datetime-local" name="t" required><button class="btn btn-secondary">Save</button></form>
        <p class="small muted">Sets delay_minutes and status (DELAYED at 15+ min).</p>
        <h3>Change status</h3>
        <form id="status" class="inline-form"><select name="s"><option>SCHEDULED</option><option>COMPLETED</option>
          <option>DELAYED</option><option>CANCELLED</option><option>DIVERTED</option></select><button class="btn btn-secondary">Save</button></form>
        <h3 style="margin-top:var(--space-4)">Reassign aircraft</h3>
        <form id="ac" class="inline-form">${input({ name: "a", type: "select", options: aircraft }, null)}
          <input name="tail" placeholder="Tail" size="7"><button class="btn btn-secondary">Save</button></form>
        <div class="chips" style="margin-top:var(--space-4)">
          <a class="btn btn-secondary" href="/network.html?flight=${enc(id)}">View graph</a>
          <button type="button" class="btn btn-secondary" data-cancel>New flight</button></div>`;
      $("#status").s.value = f.flight.status;
      $("#dep").addEventListener("submit", async e => {
        e.preventDefault();
        if (await act(() => api(`/api/flights/${enc(id)}/departure`, { method: "PUT",
            body: { actual_departure: `${e.target.t.value}:00Z` } }), r => `Delay ${r.flight.delay_minutes} min, ${r.flight.status}`)) search();
      });
      $("#status").addEventListener("submit", async e => {
        e.preventDefault();
        if (await act(() => api(`/api/flights/${enc(id)}`, { method: "PUT", body: { status: e.target.s.value } }), "Status updated")) search();
      });
      $("#ac").addEventListener("submit", async e => {
        e.preventDefault();
        await act(() => api(`/api/flights/${enc(id)}/aircraft`, { method: "PUT",
          body: { aircraft_type_id: e.target.a.value, tail_number: e.target.tail.value.trim().toUpperCase() || null } }),
          r => `Now flown by ${r.aircraft}`);
      });
      $("[data-cancel]").addEventListener("click", newForm);
    };
    const search = async () => {
      const params = new URLSearchParams([...new FormData($("#find"))].filter(([, v]) => v.trim()));
      const rows = await act(() => api(`/api/flights?limit=50&${params}`)) || [];
      $("#list").innerHTML = rowsTable(rows, [["flight_id", "Flight"], ["origin", "From"], ["destination", "To"],
                                              ["scheduled_departure", "Departs"], ["status", "Status"], ["delay_minutes", "Delay"]],
        r => chip("Edit", { edit: r.flight_id }) + chip("Delete", { del: r.flight_id }));
    };
    $("#find").addEventListener("submit", e => { e.preventDefault(); search(); });
    $("#left").addEventListener("click", async e => {
      const d = e.target.closest("button")?.dataset || {};
      if (d.edit) edit(d.edit);
      if (d.del && confirm(`Delete flight ${d.del} and its relationships?`)
          && await act(() => api(`/api/flights/${enc(d.del)}`, { method: "DELETE" }), `${d.del} deleted`)) { search(); newForm(); }
    });
    $("#find").data_source.value = "MANUAL";
    newForm();
    search();
  }

  // ---------------------------------------------------------------- Routes

  const ROUTE_FIELDS = [
    { name: "src", label: "From", required: true, upper: true, placeholder: "DEL" },
    { name: "dst", label: "To", required: true, upper: true, placeholder: "BOM" },
    { name: "distance_km", label: "Distance (km)", type: "number", step: "any", required: true },
    { name: "avg_duration_min", label: "Duration (min)", type: "number", required: true },
    { name: "co2_per_seat_kg", label: "CO₂ per seat (kg)", type: "number", step: "any", required: true },
    { name: "weight_cost", label: "Cost weight", type: "number", step: "any", help: "Blank: 50 + 0.10 × km" },
  ];

  function haversine(a, b) {
    const r = d => d * Math.PI / 180;
    const h = Math.sin(r(b.latitude - a.latitude) / 2) ** 2 +
      Math.cos(r(a.latitude)) * Math.cos(r(b.latitude)) * Math.sin(r(b.longitude - a.longitude) / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.sqrt(h));
  }

  async function routes() {
    layout(`<h2>Look up a route</h2>
      <form id="find" class="inline-form"><input name="src" placeholder="From" size="5"><input name="dst" placeholder="To" size="5">
        <button class="btn btn-secondary">Find</button></form>
      <div id="route" style="margin-top:var(--space-3)"></div>`,
      `<h2>New route</h2>
      <p class="small muted">Adds a directed ROUTE edge with its pathfinding weights. The GDS graph is re-projected afterwards.</p>
      ${formHtml("form", ROUTE_FIELDS, {}, { submit: "Create route" })}
      <button type="button" class="btn btn-secondary" id="estimate" style="margin-top:var(--space-2)">Estimate distance and time</button>`);

    const show = async (src, dst) => {
      const r = await act(() => api(`/api/routes/${enc(src)}/${enc(dst)}`));
      if (!r) { $("#route").innerHTML = ""; return; }
      const p = r.route;
      $("#route").innerHTML = `<h3>${App.esc(src)} → ${App.esc(dst)} ${fmt(p.corridor_status)}</h3>
        <div class="table-wrap"><table class="props">${Object.entries(p).sort(([a], [b]) => a.localeCompare(b))
          .map(([k, v]) => `<tr><th>${App.esc(k)}</th><td>${fmt(typeof v === "number" ? Math.round(v * 100) / 100 : v)}</td></tr>`).join("")}</table></div>
        <h3 style="margin-top:var(--space-3)">Corridor status</h3>
        <form id="status" class="inline-form"><select name="s"><option>ACTIVE</option><option>DISRUPTED</option><option>CLOSED</option></select>
          <button class="btn btn-secondary">Save</button></form>
        <h3>Update weights</h3>
        <form id="weights" class="inline-form">
          <input name="m" type="number" placeholder="Minutes" value="${p.avg_duration_min}" size="6">
          <input name="c" type="number" step="any" placeholder="kg CO₂" value="${Math.round(p.co2_per_seat_kg * 10) / 10}" size="6">
          <button class="btn btn-secondary">Save</button></form>
        <button type="button" class="btn btn-danger" id="del" style="margin-top:var(--space-3)">Delete route</button>`;
      $("#status").s.value = p.corridor_status;
      $("#status").addEventListener("submit", async e => {
        e.preventDefault();
        if (await act(() => api(`/api/routes/${enc(src)}/${enc(dst)}/status`, { method: "PUT", body: { status: e.target.s.value } }),
                      "Corridor status saved")) show(src, dst);
      });
      $("#weights").addEventListener("submit", async e => {
        e.preventDefault();
        if (await act(() => api(`/api/routes/${enc(src)}/${enc(dst)}`, { method: "PUT",
            body: { avg_duration_min: Number(e.target.m.value), co2_per_seat_kg: Number(e.target.c.value) } }), "Weights updated")) show(src, dst);
      });
      $("#del").addEventListener("click", async () => {
        if (confirm(`Delete the ROUTE ${src} → ${dst}?`)
            && await act(() => api(`/api/routes/${enc(src)}/${enc(dst)}`, { method: "DELETE" }), "Route deleted")) $("#route").innerHTML = "";
      });
    };
    $("#find").addEventListener("submit", e => {
      e.preventDefault();
      show(e.target.src.value.trim().toUpperCase(), e.target.dst.value.trim().toUpperCase());
    });
    $("#form").addEventListener("submit", async e => {
      e.preventDefault();
      const body = readForm(e.target, ROUTE_FIELDS);
      if (await act(() => api("/api/routes", { method: "POST", body }), `Created ${body.src} → ${body.dst}`)) {
        $("#find").src.value = body.src; $("#find").dst.value = body.dst;
        show(body.src, body.dst);
      }
    });
    $("#estimate").addEventListener("click", async () => {
      const f = $("#form"), src = f.src.value.trim().toUpperCase(), dst = f.dst.value.trim().toUpperCase();
      const pair = await act(() => Promise.all([api(`/api/airports/${enc(src)}`), api(`/api/airports/${enc(dst)}`)]));
      if (!pair) return;
      const km = haversine(pair[0].airport.location, pair[1].airport.location);
      f.distance_km.value = Math.round(km);
      f.avg_duration_min.value = Math.round(46.2 + 0.0725 * km);  // the block-time model seed3.py uses
    });
  }

  // ---------------------------------------------------------------- Hubs

  async function hubs() {
    panel().innerHTML = `<div class="card" id="left"><h2>Hub airlines at an airport</h2>
      <p class="small muted">(:Airport)-[:HUB_OF]->(:Airline) relationships.</p>
      <form id="find" class="inline-form"><input name="airport" placeholder="Airport, e.g. DEL" size="8"><button class="btn btn-secondary">Show</button></form>
      <div id="list" style="margin-top:var(--space-3)"></div></div>`;
    const show = async code => {
      const a = await act(() => api(`/api/airports/${enc(code)}`));
      if (!a) return;
      const rows = a.hub_of.map(id => ({ airline_id: id }));
      $("#list").innerHTML = `<h3>${App.esc(code)} · ${App.esc(a.airport.name)}</h3>
        ${rowsTable(rows, [["airline_id", "Airline"]], r => chip("Remove", { remove: r.airline_id }))}
        <h3 style="margin-top:var(--space-3)">Add a hub airline</h3>
        <form id="add" class="inline-form"><input name="airline" placeholder="Airline, e.g. AI" size="6" required>
          <input name="since" type="date"><button class="btn btn-secondary">Add</button></form>`;
      $("#add").addEventListener("submit", async e => {
        e.preventDefault();
        if (await act(() => api("/api/hubs", { method: "POST", body: {
            airport_id: code, airline_id: e.target.airline.value.trim().toUpperCase(), since: e.target.since.value || null } }),
            "Hub added")) show(code);
      });
    };
    $("#find").addEventListener("submit", e => { e.preventDefault(); show(e.target.airport.value.trim().toUpperCase()); });
    $("#left").addEventListener("click", async e => {
      const id = e.target.closest("[data-remove]")?.dataset.remove;
      const code = $("#find").airport.value.trim().toUpperCase();
      if (id && confirm(`Remove ${code} as a hub of ${id}?`)
          && await act(() => api(`/api/hubs/${enc(code)}/${enc(id)}`, { method: "DELETE" }), "Hub removed")) show(code);
    });
    $("#find").airport.value = "DEL";
    show("DEL");
  }

  // ---------------------------------------------------------------- Events

  const EVENT_FIELDS = [
    { name: "event_id", label: "Event ID", required: true, placeholder: "EVT-2026-1025-FOG", wide: true },
    { name: "event_type", label: "Type", type: "select", options: ["WEATHER", "FOG", "ATC_FAILURE", "STRIKE", "TECHNICAL", "OTHER"] },
    { name: "severity", label: "Severity", type: "select", options: ["MODERATE", "SEVERE", "CRITICAL"] },
    { name: "start_time", label: "Start (UTC)", type: "datetime", required: true },
    { name: "end_time", label: "End (UTC)", type: "datetime" },
    { name: "airports", label: "Affected airports", required: true, upper: true, placeholder: "DEL, LKO", wide: true },
    { name: "description", label: "Description", required: true, wide: true },
  ];

  async function events() {
    layout(`<h2>Events</h2><div id="list"></div>`,
      `<h2>Log an event</h2>
      <p class="small muted">Records a DisruptionEvent with AFFECTS links for the audit trail. To actually close airports, use the
        <a href="/disruptions.html">Disruption Simulator</a>.</p>
      ${formHtml("form", EVENT_FIELDS, {}, { submit: "Create event" })}`);
    const list = async () => {
      const rows = await act(() => api("/api/events")) || [];
      $("#list").innerHTML = rowsTable(rows, [["event_id", "Event"], ["event_type", "Type"], ["data_source", "Source"],
                                              ["start_time", "Start"], ["end_time", "End"]],
        r => chip("Graph", { graph: r.event_id })
          + (r.end_time ? "" : chip("Resolve", { resolve: r.event_id }))
          + (r.data_source === "MANUAL" ? chip("Delete", { del: r.event_id }) : ""));
    };
    $("#form").addEventListener("submit", async e => {
      e.preventDefault();
      const body = readForm(e.target, EVENT_FIELDS);
      body.airports = body.airports.split(/[\s,]+/).filter(Boolean);
      if (await act(() => api("/api/events", { method: "POST", body }), r => `Created ${r.event.event_id} (${r.affects.length} airports)`)) list();
    });
    $("#left").addEventListener("click", async e => {
      const d = e.target.closest("button")?.dataset || {};
      if (d.graph) location.href = `/network.html?event=${enc(d.graph)}`;
      if (d.resolve && await act(() => api(`/api/events/${enc(d.resolve)}/resolve`, { method: "PUT",
          body: { end_time: new Date().toISOString() } }), "Event resolved")) list();
      if (d.del && confirm(`Delete event ${d.del}?`)
          && await act(() => api(`/api/events/${enc(d.del)}`, { method: "DELETE" }), "Event deleted")) list();
    });
    list();
  }

  // ---------------------------------------------------------------- tabs

  const TABS = { airports, airlines, flights, routes, hubs, events };

  function open(name) {
    document.querySelectorAll("#tabs .tab").forEach(t => t.setAttribute("aria-selected", t.dataset.tab === name));
    history.replaceState(null, "", `#${name}`);
    TABS[name]();
  }

  let started = false;
  document.addEventListener("admin-changed", e => {
    if (!e.detail || started) return;
    started = true;
    document.querySelectorAll("#tabs .tab").forEach(t => t.addEventListener("click", () => open(t.dataset.tab)));
    open(TABS[location.hash.slice(1)] ? location.hash.slice(1) : "airports");
  });
})();
