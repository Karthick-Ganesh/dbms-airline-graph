// Analytics dashboard: one card per query in queries.QUERIES (/api/analytics).
(() => {
  // Queries shown first, with a bar chart of one column.
  const CHARTS = {
    5: { label: "airport", value: "betweenness" },
    6: { label: "airport", value: "pagerank" },
    10: { label: "category", value: "g_co2_per_seat_km" },
    7: { label: "airline", value: "cancelled_pct" },
    12: { label: "airline", value: "pct_routes_from_hubs" },
    8: { label: "airport", value: "cancelled" },
  };
  const PATH_COLUMNS = new Set(["path", "itinerary"]);
  const $ = s => document.querySelector(s);

  function cell(key, v) {
    if (v == null) return '<span class="muted">–</span>';
    if (Array.isArray(v)) return App.esc(v.join(PATH_COLUMNS.has(key) ? " → " : ", "));
    if (typeof v === "number") return App.number(v, 2);
    if (typeof v === "boolean") return v ? "yes" : "no";
    return App.esc(v);
  }

  function table(rows) {
    if (!rows.length) return '<div class="empty small">No rows.</div>';
    const cols = Object.keys(rows[0]);
    return `<div class="table-wrap"><table>
      <thead><tr>${cols.map(c => `<th class="${typeof rows[0][c] === "number" ? "num" : ""}">${App.esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr>${cols.map(c =>
        `<td class="${typeof r[c] === "number" ? "num" : ""}">${cell(c, r[c])}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>`;
  }

  /** Horizontal bar chart as inline SVG (scales with the card). */
  function bars(rows, { label, value }) {
    const data = rows.filter(r => typeof r[value] === "number").slice(0, 10);
    if (!data.length) return "";
    const max = Math.max(...data.map(r => r[value])) || 1;
    const rowH = 24, labelW = 120, W = 560, H = data.length * rowH;
    return `<svg class="bars" viewBox="0 0 ${W} ${H}" role="img" aria-label="${App.esc(value)} by ${App.esc(label)}">
      ${data.map((r, i) => {
        const w = Math.max(2, (W - labelW - 70) * r[value] / max), y = i * rowH;
        return `<text x="${labelW - 8}" y="${y + 16}" text-anchor="end" class="bar-label">${App.esc(String(r[label]).slice(0, 18))}</text>
          <rect x="${labelW}" y="${y + 4}" width="${w}" height="${rowH - 8}" rx="3" class="bar"/>
          <text x="${labelW + w + 6}" y="${y + 16}" class="bar-value">${App.number(r[value], 1)}</text>`;
      }).join("")}
    </svg>`;
  }

  function cardHtml(q) {
    const inputs = Object.entries(q.params).map(([k, v]) =>
      `<label class="param">${App.esc(k)}<input name="${App.esc(k)}" value="${App.esc(v)}" size="6"></label>`).join("");
    return `<article class="card query" id="q${q.number}">
      <div class="strip"><h2>${App.esc(q.title)}</h2><span class="badge">Q${q.number}</span></div>
      <p class="small muted">${App.esc(q.feature)}</p>
      <form class="params">${inputs}<button class="btn btn-secondary">Run</button></form>
      <div class="output"><div class="empty"><span class="spinner"></span></div></div>
      <details><summary class="small">Cypher</summary><pre class="cypher">${App.esc(q.cypher)}</pre></details>
    </article>`;
  }

  async function run(q, el) {
    const out = el.querySelector(".output");
    const params = new URLSearchParams(new FormData(el.querySelector(".params")));
    out.innerHTML = '<div class="empty"><span class="spinner"></span></div>';
    const t0 = performance.now();
    try {
      const res = await App.api(`/api/analytics/${q.number}?${params}`);
      const ms = Math.round(performance.now() - t0);
      out.innerHTML = (CHARTS[q.number] ? bars(res.rows, CHARTS[q.number]) : "") + table(res.rows)
        + `<div class="small muted" style="margin-top:var(--space-2)">${res.rows.length} rows · ${ms} ms</div>`;
    } catch (e) {
      out.innerHTML = `<p class="delta worse">${App.esc(e.message)}</p>`;
    }
  }

  async function stats() {
    try {
      const s = await App.api("/api/stats");
      const total = list => list.reduce((n, r) => n + (r.nodes ?? r.count), 0);
      $("#stats").innerHTML = `
        <div class="stat"><div class="value">${App.number(total(s.nodes))}</div><div class="label">nodes</div></div>
        <div class="stat"><div class="value">${App.number(total(s.relationships))}</div><div class="label">relationships</div></div>
        ${s.nodes.map(r => `<div class="stat"><div class="value">${App.number(r.nodes)}</div><div class="label">:${App.esc(r.label)}</div></div>`).join("")}
        ${s.relationships.map(r => `<div class="stat"><div class="value">${App.number(r.count)}</div><div class="label">[:${App.esc(r.relationship)}]</div></div>`).join("")}`;
    } catch (e) {
      $("#stats").innerHTML = `<p class="delta worse">${App.esc(e.message)}</p>`;
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    stats();
    const all = await App.api("/api/analytics").catch(e => { App.toast(e.message, "error"); return []; });
    const featured = Object.keys(CHARTS).map(Number).map(n => all.find(q => q.number === n)).filter(Boolean);
    const others = all.filter(q => !CHARTS[q.number]);
    $("#featured").innerHTML = featured.map(cardHtml).join("");
    $("#others").innerHTML = others.map(cardHtml).join("");
    for (const q of [...featured, ...others]) {
      const el = $(`#q${q.number}`);
      el.querySelector(".params").addEventListener("submit", e => { e.preventDefault(); run(q, el); });
      run(q, el);
    }
  });
})();
