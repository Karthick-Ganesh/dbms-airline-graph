// Shared by every page: API calls, toasts, the boarding-pass header and
// footer, the admin flag and the airport picker. Pages put
// <header id="topbar"></header> first in <body>.
const App = (() => {
  const PAGES = [
    ["/", "Route Finder"],
    ["/live.html", "Live Traffic"],
    ["/network.html", "Graph Explorer"],
    ["/disruptions.html", "Disruptions"],
    ["/analytics.html", "Analytics"],
    ["/manage.html", "Manage Data", "admin"],
  ];

  const PLANE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z"/></svg>';
  const MENU = '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18"/></svg>';

  /** fetch() wrapper: JSON in and out, throws Error(detail) on HTTP errors. */
  async function api(path, { method = "GET", body } = {}) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === "string" ? detail
        : Array.isArray(detail) ? detail.map(d => `${d.loc[d.loc.length - 1]}: ${d.msg}`).join("; ")
        : res.statusText;
      throw new Error(msg);
    }
    return data;
  }

  function toast(message, kind = "info", ms = 4500) {
    let box = document.querySelector(".toasts");
    if (!box) {
      box = document.createElement("div");
      box.className = "toasts";
      box.setAttribute("role", "status");
      document.body.append(box);
    }
    const t = document.createElement("div");
    t.className = `toast toast-${kind}`;
    t.textContent = message;
    box.append(t);
    setTimeout(() => t.remove(), ms);
  }

  /** Runs an async action, showing any error as a toast. */
  async function attempt(fn) {
    try { return await fn(); } catch (e) { toast(e.message, "error"); }
  }

  /** Value of a CSS design token, e.g. token("--route-fastest"). */
  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function minutes(m) {
    m = Math.round(m || 0);
    const h = Math.floor(m / 60);
    return h ? `${h} h ${String(m % 60).padStart(2, "0")} m` : `${m} m`;
  }

  function number(n, digits = 0) {
    return n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: digits });
  }

  let admin = false;
  async function refreshAdmin() {
    try { admin = (await api("/api/me")).admin; } catch { admin = false; }
    document.body.dataset.admin = admin;
    document.querySelectorAll("[data-auth]").forEach(a => { a.textContent = admin ? "Log out" : "Admin login"; });
    document.querySelectorAll("[data-auth-label]").forEach(a => { a.textContent = admin ? "Log out" : "Admin"; });
    const passenger = document.querySelector("#passenger");
    if (passenger) passenger.textContent = admin ? "Admin" : "Guest";
    document.dispatchEvent(new CustomEvent("admin-changed", { detail: admin }));
    return admin;
  }

  const gate = i => `Gate ${String(i + 1).padStart(2, "0")}`;

  /** Header band: brand, pages as boarding-pass "gate" fields, passenger stub. */
  function topbar() {
    const el = document.querySelector("#topbar");
    if (!el) return;
    const here = location.pathname === "/index.html" ? "/" : location.pathname;
    el.className = "topbar";
    el.innerHTML = `
      <div class="pass-top">
        <a class="brand" href="/" aria-label="Neo Airways route optimizer, home">
          <span class="brand-logo">${PLANE}</span>
          <span class="brand-text"><span class="brand-name">Neo Airways</span>
            <span class="brand-sub">Boarding pass · Route optimizer</span></span>
        </a>
        <button class="nav-toggle" aria-label="Menu" aria-expanded="false">${MENU}</button>
        <nav class="nav">
          ${PAGES.map(([href, label, role], i) => `
            <a href="${href}" ${role ? `class="${role}-only"` : ""} ${href === here ? 'aria-current="page"' : ""}>
              <span class="field-label">${href === here ? "Now boarding" : gate(i)}</span>
              <span class="field-value">${label}</span></a>`).join("")}
          <a href="/login.html" class="auth-mobile">
            <span class="field-label">Passenger</span><span class="field-value" data-auth>Admin login</span></a>
        </nav>
        <div class="stub">
          <div class="stub-field"><span class="field-label">Passenger</span><span class="field-value" id="passenger">Guest</span></div>
          <a href="/login.html" class="stub-action" data-auth>Admin login</a>
          <span class="barcode" aria-hidden="true"></span>
        </div>
      </div>`;
    const nav = el.querySelector(".nav"), toggle = el.querySelector(".nav-toggle");
    toggle.addEventListener("click", () => {
      nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", nav.classList.contains("open"));
    });
    el.querySelectorAll("a.stub-action, a.auth-mobile").forEach(a => a.addEventListener("click", async e => {
      if (!admin) return;
      e.preventDefault();
      await api("/api/logout", { method: "POST" });
      await refreshAdmin();
      toast("Logged out");
    }));

    // "Gate 0n · Now boarding" above the page title.
    const i = PAGES.findIndex(([href]) => href === here);
    const header = document.querySelector(".page-header");
    if (header) {
      const kicker = document.createElement("div");
      kicker.className = "kicker";
      kicker.textContent = i >= 0 ? `${gate(i)} · Now boarding` : "Security check";
      header.prepend(kicker);
    }
  }

  /** Footer: the pass's bottom stub. */
  function footer() {
    const f = document.createElement("footer");
    f.className = "pass-footer";
    f.innerHTML = `<div class="pass-footer-inner">
      <div><strong>Neo Airways</strong> · Graph-based airline route optimization for disruption analysis and sustainable travel</div>
      <div>BCSE302P Database Systems Lab · Neo4j + Graph Data Science</div>
      <span class="barcode" aria-hidden="true"></span>
    </div>`;
    document.body.append(f);
  }

  /**
   * Airport autocomplete on an <input>, backed by the full-text index.
   * input.dataset.code holds the chosen IATA code; onPick(code) is optional.
   */
  function airportPicker(input, onPick) {
    const wrap = document.createElement("div");
    wrap.className = "picker";
    input.replaceWith(wrap);
    wrap.append(input);
    const list = document.createElement("ul");
    list.className = "picker-list hidden";
    list.setAttribute("role", "listbox");
    wrap.append(list);
    input.setAttribute("autocomplete", "off");

    let items = [], active = -1, timer;
    const close = () => { list.classList.add("hidden"); active = -1; };
    const pick = item => {
      input.value = `${item.airport_id} – ${item.city || item.name}`;
      input.dataset.code = item.airport_id;
      close();
      onPick?.(item.airport_id);
    };
    const render = () => {
      list.innerHTML = items.map((a, i) =>
        `<li role="option" data-i="${i}" aria-selected="${i === active}"><span class="code">${esc(a.airport_id)}</span>${esc(a.name)}<span class="muted"> · ${esc(a.city)}</span></li>`).join("");
      list.classList.toggle("hidden", !items.length);
    };

    input.addEventListener("input", () => {
      const q = input.value.trim();
      input.dataset.code = /^[A-Za-z0-9]{3}$/.test(q) ? q.toUpperCase() : "";
      clearTimeout(timer);
      if (q.length < 2) { items = []; render(); return; }
      timer = setTimeout(async () => {
        try { items = await api(`/api/airports/search?q=${encodeURIComponent(q)}&limit=8`); }
        catch { items = []; }
        active = -1;
        render();
      }, 180);
    });
    input.addEventListener("keydown", e => {
      if (list.classList.contains("hidden")) return;
      if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
      else if (e.key === "Enter" && active >= 0) { pick(items[active]); e.preventDefault(); }
      else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", e => {
      const li = e.target.closest("li");
      if (li) { pick(items[+li.dataset.i]); e.preventDefault(); }
    });
    input.addEventListener("blur", () => setTimeout(close, 120));

    return {
      set(code, label) {
        input.value = label ? `${code} – ${label}` : code;
        input.dataset.code = code;
      },
      get: () => input.dataset.code || "",
    };
  }

  document.addEventListener("DOMContentLoaded", () => {
    topbar();
    footer();
    refreshAdmin();
  });

  return { api, toast, attempt, token, esc, minutes, number, airportPicker, refreshAdmin,
           isAdmin: () => admin };
})();
