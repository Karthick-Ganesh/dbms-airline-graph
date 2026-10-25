// Live Traffic: thousands of planes drawn on one canvas over the Leaflet map.
// Live mode moves simulated flights (/api/live) with the system clock; Replay
// mode animates the real January 2015 Flight nodes (/api/replay) at speed.
(() => {
  const $ = s => document.querySelector(s);
  const rad = d => d * Math.PI / 180, deg = r => r * 180 / Math.PI;
  const REFRESH_MS = 60_000;          // live list refresh
  const HIT_PX = 10;                  // click tolerance around a plane
  const STATUS_TOKEN = { COMPLETED: "--success", DELAYED: "--warning", DIVERTED: "--route-alt",
                         CANCELLED: "--danger", SCHEDULED: "--pass" };
  const DAYS = { 13: "Delhi fog (synthetic)", 14: "Delhi fog (synthetic)", 26: "Winter Storm Juno", 27: "Winter Storm Juno",
                 28: "Juno aftermath" };

  let map, canvas, ctx, mode = "live";
  let flights = [], airports = {}, drawn = [], selected = null, selectedLayer = null;
  let highlight = "", liveTimer = null, frame = null;
  let replay = { start: 0, end: 0, t: 0, speed: 900, playing: true, last: 0 };
  const colors = {};

  // ---------------------------------------------------------------- geometry

  const unit = ([lat, lon]) => [Math.cos(rad(lat)) * Math.cos(rad(lon)), Math.cos(rad(lat)) * Math.sin(rad(lon)), Math.sin(rad(lat))];

  /** Precompute the great-circle vectors so each frame is a cheap slerp. */
  function prepare(f) {
    const a = airports[f.src], b = airports[f.dst];
    if (!a || !b) return null;
    const v1 = unit(a), v2 = unit(b);
    const w = Math.acos(Math.min(1, Math.max(-1, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2])));
    return { ...f, v1, v2, w, a, b };
  }

  function position(f, t) {
    const frac = Math.min(1, Math.max(0, (t - f.dep) / (f.arr - f.dep)));
    let p;
    if (f.w < 1e-6) p = f.v1;
    else {
      const s1 = Math.sin((1 - frac) * f.w) / Math.sin(f.w), s2 = Math.sin(frac * f.w) / Math.sin(f.w);
      p = [s1 * f.v1[0] + s2 * f.v2[0], s1 * f.v1[1] + s2 * f.v2[1], s1 * f.v1[2] + s2 * f.v2[2]];
    }
    const lat = deg(Math.atan2(p[2], Math.hypot(p[0], p[1]))), lon = deg(Math.atan2(p[1], p[0]));
    // Heading: initial bearing from here to the destination.
    const [la1, lo1, la2, lo2] = [rad(lat), rad(lon), rad(f.b[0]), rad(f.b[1])];
    const hdg = Math.atan2(Math.sin(lo2 - lo1) * Math.cos(la2),
                           Math.cos(la1) * Math.sin(la2) - Math.sin(la1) * Math.cos(la2) * Math.cos(lo2 - lo1));
    return { lat, lon, hdg, frac };
  }

  // ---------------------------------------------------------------- drawing

  function sizeCanvas() {
    const { x, y } = map.getSize(), dpr = window.devicePixelRatio || 1;
    canvas.width = x * dpr;
    canvas.height = y * dpr;
    canvas.style.width = `${x}px`;
    canvas.style.height = `${y}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function plane(x, y, hdg, size, fill) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(hdg);
    ctx.scale(size / 10, size / 10);
    ctx.beginPath();
    ctx.moveTo(0, -10); ctx.lineTo(1.6, -4); ctx.lineTo(9, 1.5); ctx.lineTo(9, 3.5); ctx.lineTo(1.6, 1.4);
    ctx.lineTo(1.2, 6.5); ctx.lineTo(3.8, 8.6); ctx.lineTo(3.8, 10); ctx.lineTo(0, 9); ctx.lineTo(-3.8, 10);
    ctx.lineTo(-3.8, 8.6); ctx.lineTo(-1.2, 6.5); ctx.lineTo(-1.6, 1.4); ctx.lineTo(-9, 3.5); ctx.lineTo(-9, 1.5);
    ctx.lineTo(-1.6, -4); ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.restore();
  }

  function cross(x, y, size, stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size);
    ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size);
    ctx.stroke();
  }

  function now() {
    return mode === "live" ? Date.now() / 1000 : replay.t;
  }

  /** Draws every visible plane; records screen positions for click hit-testing. */
  function draw() {
    frame = null;
    const { x: W, y: H } = map.getSize();
    ctx.clearRect(0, 0, W, H);
    const t = now(), zoom = map.getZoom(), bounds = map.getBounds();
    const west = bounds.getWest(), east = bounds.getEast();
    // Dots when zoomed out; plane icons that grow as you zoom in.
    const glyph = mode === "replay" || zoom >= 4;
    const size = mode === "replay" ? 15 : zoom >= 7 ? 14 : zoom >= 6 ? 11 : zoom >= 5 ? 9 : 7;
    drawn = [];
    let airborne = 0;

    for (const f of flights) {
      let p, cancelled = false;
      if (f.status === "CANCELLED") {
        if (t < f.sched_dep - 3600 || t > f.sched_dep + 2 * 3600) continue;
        p = { lat: f.a[0], lon: f.a[1], hdg: 0 };
        cancelled = true;
      } else {
        if (t < f.dep || t >= f.arr) continue;
        p = position(f, t);
        airborne++;
      }
      const mine = highlight && f.carrier === highlight;
      const isSel = selected && selected.key === f.key;
      const fill = isSel ? colors.accent
        : mode === "replay" ? App.token(STATUS_TOKEN[f.status] || "--pass")
        : highlight ? (mine ? colors.accent : colors.faded) : colors.pass;
      // Draw in every world copy that is on screen.
      for (const off of [-360, 0, 360]) {
        const lon = p.lon + off;
        if (lon < west - 10 || lon > east + 10) continue;
        const pt = map.latLngToContainerPoint([p.lat, lon]);
        if (pt.x < -20 || pt.y < -20 || pt.x > W + 20 || pt.y > H + 20) continue;
        if (cancelled) cross(pt.x, pt.y, 6, colors.danger);
        else if (glyph || isSel || mine) plane(pt.x, pt.y, p.hdg, isSel ? 20 : mine ? size + 3 : size, fill);
        else {   // zoomed out: a small translucent dot reads better than 10,000 glyphs
          ctx.globalAlpha = highlight && !mine ? 1 : 0.7;
          ctx.fillStyle = fill;
          ctx.fillRect(pt.x - 1, pt.y - 1, 2.2, 2.2);
          ctx.globalAlpha = 1;
        }
        if (mode === "replay" && zoom >= 3) {
          ctx.fillStyle = colors.text;
          ctx.font = `600 11px ${colors.mono}`;
          ctx.fillText(f.code, pt.x + 10, pt.y - 8);
        }
        drawn.push([pt.x, pt.y, f]);
      }
    }
    stats(airborne, t);
    if (selected) updateTicket(selected, t);
  }

  function redraw() {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  // ---------------------------------------------------------------- side panel

  const fmt = (epoch, utc) => new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit", minute: "2-digit", ...(utc ? { timeZone: "UTC" } : {}) }).format(epoch * 1000);

  function tick() {
    const t = now();
    const opts = { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" };
    $("#clock-utc").textContent = new Intl.DateTimeFormat("en-GB", opts).format(t * 1000);
    if (mode === "live") {
      $("#clock-local-label").textContent = "Your time";
      $("#clock-local").textContent = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(t * 1000);
    } else {
      $("#clock-local-label").textContent = "Replay date";
      $("#clock-local").textContent = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(t * 1000);
    }
  }

  function stats(airborne, t) {
    if (mode === "live") {
      const counts = {};
      for (const f of flights) if (t >= f.dep && t < f.arr) counts[f.carrier] = (counts[f.carrier] || 0) + 1;
      const mine = highlight ? counts[highlight] || 0 : null;
      $("#stats").innerHTML = `
        <div class="board-stat"><span class="field-label">In the air</span><span class="v">${App.number(airborne)}</span></div>
        <div class="board-stat"><span class="field-label">Scheduled today</span><span class="v">${App.number(scheduledPerDay)}</span></div>
        ${highlight ? `<div class="board-stat"><span class="field-label">${App.esc(highlight)} airborne</span><span class="v">${App.number(mine)}</span></div>` : ""}`;
      topAirlines = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
    } else {
      const departed = flights.filter(f => f.status !== "CANCELLED" && f.dep <= t).length;
      const cancelled = flights.filter(f => f.status === "CANCELLED" && f.sched_dep <= t).length;
      const delayed = flights.filter(f => f.status === "DELAYED" && f.dep <= t).length;
      $("#stats").innerHTML = `
        <div class="board-stat"><span class="field-label">In the air</span><span class="v">${airborne}</span></div>
        <div class="board-stat"><span class="field-label">Departed</span><span class="v">${departed}</span></div>
        <div class="board-stat"><span class="field-label">Delayed</span><span class="v delta worse">${delayed}</span></div>
        <div class="board-stat"><span class="field-label">Cancelled</span><span class="v delta worse">${cancelled}</span></div>`;
      $("#scrub").value = Math.round((t - replay.start) / 60);
    }
  }

  let topAirlines = [], scheduledPerDay = 0;
  function renderTopAirlines() {
    $("#top-airlines").innerHTML = topAirlines.map(([c, n]) =>
      `<button type="button" class="chip" data-carrier="${App.esc(c)}" aria-pressed="${c === highlight}">${App.esc(c)} · ${n}</button>`).join("");
  }

  /** The selected flight as a boarding pass (static parts; progress updates each frame). */
  async function showTicket(f) {
    selected = f;
    selectedLayer?.remove();
    selectedLayer = L.layerGroup().addTo(map);
    const path = RouteMap.leg(f.a, f.b);
    RouteMap.wrapped(off => L.polyline(RouteMap.shift(path, off),
      { color: colors.accent, weight: 2.5, dashArray: "6 6", opacity: 0.9 }).addTo(selectedLayer));
    [[f.a, f.src], [f.b, f.dst]].forEach(([pos, code]) => RouteMap.label([pos[0], pos[1]], code).addTo(selectedLayer));

    const color = mode === "replay" ? App.token(STATUS_TOKEN[f.status] || "--pass") : colors.pass;
    $("#flight").innerHTML = `
      <div class="ticket" style="--option-color:${color}; margin-top: var(--space-4)">
        <div class="ticket-main">
          <div class="ticket-strip"><span>Flight ${App.esc(f.code)}</span>
            <span class="algo">${mode === "live" ? "Live · simulated" : App.esc(f.source || "")}</span></div>
          <div class="ticket-body">
            <div class="route-codes">${App.esc(f.src)}<svg class="hop" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z"/></svg>${App.esc(f.dst)}</div>
            <div class="route-cities">${App.esc(f.a[2])} to ${App.esc(f.b[2])}</div>
            <div class="note" id="t-airline">${App.esc(f.airline || f.carrier)}</div>
            <div class="progress" aria-hidden="true"><span class="progress-fill" id="t-bar"></span><span class="progress-plane" id="t-plane">✈</span></div>
            <div class="note" id="t-status"></div>
            <div class="chips" style="margin-top: var(--space-3)">
              <a class="chip" href="/?from=${encodeURIComponent(f.src)}&to=${encodeURIComponent(f.dst)}">Find routes</a>
              ${f.id ? `<a class="chip" href="/network.html?flight=${encodeURIComponent(f.id)}">Flight graph</a>`
                     : `<a class="chip" href="/network.html?airport=${encodeURIComponent(f.src)}">${App.esc(f.src)} graph</a>`}
              <button type="button" class="chip" id="t-close">Close</button>
            </div>
          </div>
        </div>
        <div class="ticket-stub">
          <div class="ticket-strip">Stub</div>
          <div class="ticket-body">
            <div class="stub-row"><span class="field-label">Departs</span><span class="v">${fmt(f.dep, true)}Z</span></div>
            <div class="stub-row"><span class="field-label">Arrives</span><span class="v">${fmt(f.arr, true)}Z</span></div>
            <div class="stub-row"><span class="field-label">Remaining</span><span class="v" id="t-left">–</span></div>
            <div class="stub-row"><span class="field-label">CO₂ / seat</span><span class="v" id="t-co2">–</span></div>
          </div>
        </div>
      </div>`;
    $("#t-close").addEventListener("click", clearTicket);
    updateTicket(f, now());
    redraw();
    // Extra details from the graph: the ROUTE edge (CO2) and the airline's name.
    const [route, airline] = await Promise.all([
      App.api(`/api/routes/${f.src}/${f.dst}`).catch(() => null),
      f.airline ? null : App.api(`/api/airlines/${f.carrier}`).catch(() => null),
    ]);
    if (selected !== f) return;
    if (route) $("#t-co2").textContent = `${App.number(route.route.co2_per_seat_kg, 1)} kg`;
    if (airline) $("#t-airline").textContent = `${airline.airline.name} (${f.carrier})`;
  }

  function updateTicket(f, t) {
    const bar = $("#t-bar");
    if (!bar) return;
    const frac = Math.min(1, Math.max(0, (t - f.dep) / (f.arr - f.dep)));
    bar.style.width = `${frac * 100}%`;
    $("#t-plane").style.left = `${frac * 100}%`;
    const left = Math.max(0, f.arr - t) / 60;
    $("#t-left").textContent = f.status === "CANCELLED" ? "—" : left > 0 ? App.minutes(left) : "Landed";
    $("#t-status").innerHTML = f.status === "CANCELLED" ? '<span class="badge badge-cancelled">Cancelled</span>'
      : t < f.dep ? "Boarding" : t >= f.arr ? "Landed"
      : `${Math.round(frac * 100)}% of the way${f.delay ? ` · <span class="delta worse">departed ${f.delay} min late</span>` : ""}`;
  }

  function clearTicket() {
    selected = null;
    selectedLayer?.remove();
    selectedLayer = null;
    $("#flight").innerHTML = "";
    redraw();
  }

  // ---------------------------------------------------------------- data

  async function loadLive() {
    const data = await App.api("/api/live").catch(e => { App.toast(e.message, "error"); return null; });
    if (!data || mode !== "live") return;
    airports = data.airports;
    scheduledPerDay = data.scheduled_per_day;
    flights = data.flights.map(([code, carrier, src, dst, dep, arr]) =>
      prepare({ key: `${code}-${src}-${dst}`, code, carrier, src, dst, dep, arr })).filter(Boolean);
    if (selected) selected = flights.find(f => f.key === selected.key) || selected;
    draw();               // synchronous, so the airline counts exist for the chips
    renderTopAirlines();
  }

  async function loadReplay(day) {
    const data = await App.api(`/api/replay?day=${day}`).catch(e => { App.toast(e.message, "error"); return null; });
    if (!data || mode !== "replay") return;
    airports = data.airports;
    flights = data.flights.map(f => prepare({ ...f, key: f.id, code: f.flight })).filter(Boolean);
    replay.start = data.start;
    replay.end = Math.max(data.start + 86400, ...flights.map(f => f.arr));
    const first = Math.min(...flights.map(f => f.status === "CANCELLED" ? f.sched_dep : f.dep));
    replay.t = Number.isFinite(first) ? first - 15 * 60 : data.start;
    $("#scrub").max = Math.round((replay.end - replay.start) / 60);
    clearTicket();
    const b = L.latLngBounds(Object.values(airports).map(a => [a[0], a[1]]));
    if (b.isValid()) map.fitBounds(b.pad(0.2));
  }

  // ---------------------------------------------------------------- modes

  function loop(ts) {
    if (mode !== "replay") return;
    if (replay.playing && replay.last) {
      replay.t = Math.min(replay.end, replay.t + (ts - replay.last) / 1000 * replay.speed);
      if (replay.t >= replay.end) setPlaying(false);
    }
    replay.last = ts;
    tick();
    draw();
    requestAnimationFrame(loop);
  }

  function setPlaying(on) {
    replay.playing = on;
    replay.last = 0;
    $("#play").textContent = on ? "Pause" : "Play";
  }

  function setMode(m) {
    mode = m;
    document.querySelectorAll("[data-mode]").forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === m));
    $("#live-controls").classList.toggle("hidden", m !== "live");
    $("#replay-controls").classList.toggle("hidden", m !== "replay");
    clearTicket();
    flights = [];
    clearInterval(liveTimer);
    if (m === "live") {
      loadLive();
      liveTimer = setInterval(() => { tick(); redraw(); }, 1000);
      map.setView([25, 40], 2);
    } else {
      setPlaying(true);
      loadReplay($("#day").value).then(() => requestAnimationFrame(loop));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    for (const [k, t] of [["pass", "--pass"], ["accent", "--accent"], ["danger", "--danger"], ["text", "--text"]]) colors[k] = App.token(t);
    colors.faded = "rgba(15, 92, 102, 0.22)";
    colors.mono = App.token("--font-mono");

    map = RouteMap.create("map");
    canvas = document.createElement("canvas");
    canvas.className = "plane-canvas";
    map.getContainer().appendChild(canvas);
    ctx = canvas.getContext("2d");
    sizeCanvas();
    map.on("resize", () => { sizeCanvas(); redraw(); });
    map.on("move", redraw);
    map.on("zoomstart", () => { canvas.style.opacity = 0; });
    map.on("zoomend", () => { canvas.style.opacity = 1; redraw(); });
    map.on("click", e => {
      const { x, y } = e.containerPoint;
      let best = null, bestD = HIT_PX * HIT_PX;
      for (const [px, py, f] of drawn) {
        const d = (px - x) ** 2 + (py - y) ** 2;
        if (d < bestD) { bestD = d; best = f; }
      }
      if (best) showTicket(best); else if (selected) clearTicket();
    });

    $("#day").innerHTML = Array.from({ length: 31 }, (_, i) => i + 1).map(d =>
      `<option value="2015-01-${String(d).padStart(2, "0")}" ${d === 26 ? "selected" : ""}>${d} January${DAYS[d] ? " · " + DAYS[d] : ""}</option>`).join("");
    $("#replay-legend").innerHTML = [["On time", "--success"], ["Delayed", "--warning"], ["Diverted", "--route-alt"], ["Cancelled (✕ at origin)", "--danger"]]
      .map(([l, t]) => `<span class="legend-item"><span class="legend-dot" style="background:${App.token(t)}"></span>${l}</span>`).join("");

    document.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));
    $("#airline").addEventListener("input", e => { highlight = e.target.value.trim().toUpperCase(); renderTopAirlines(); redraw(); });
    $("#top-airlines").addEventListener("click", e => {
      const c = e.target.closest("[data-carrier]")?.dataset.carrier;
      if (!c) return;
      highlight = highlight === c ? "" : c;
      $("#airline").value = highlight;
      renderTopAirlines();
      redraw();
    });
    $("#play").addEventListener("click", () => setPlaying(!replay.playing));
    $("#speed").addEventListener("change", e => { replay.speed = Number(e.target.value); });
    $("#day").addEventListener("change", e => loadReplay(e.target.value));
    $("#scrub").addEventListener("input", e => { replay.t = replay.start + Number(e.target.value) * 60; redraw(); });

    setInterval(() => { if (mode === "live") loadLive(); }, REFRESH_MS);
    tick();
    setMode(new URLSearchParams(location.search).get("mode") === "replay" ? "replay" : "live");
  });
})();
