const $ = (sel, el = document) => el.querySelector(sel);

function headers() {
  const key = localStorage.getItem("cp_api_key") || "";
  const h = { "Content-Type": "application/json" };
  if (key) h.Authorization = `Bearer ${key}`;
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...headers(), ...(opts.headers || {}) } });
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = { raw: text }; }
  if (!res.ok) throw new Error(body?.detail || body?.error || res.statusText);
  return body;
}

function pill(status) {
  const s = (status || "").toLowerCase();
  const cls = s === "halted" || s === "error" || s === "throttled" ? "bad"
    : s === "completed" ? "ok" : "warn";
  return `<span class="pill ${cls}">${status || "—"}</span>`;
}

function hashParts() {
  const raw = (location.hash || "#admin").replace(/^#/, "");
  const [tab, ...rest] = raw.split("/");
  return { tab: tab || "admin", rest: rest.filter(Boolean) };
}

function switchTab(name) {
  const tab = name === "chronicle" || name.startsWith("chronicle") ? "chronicle" : name;
  document.querySelectorAll(".nav-links a.tab").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  ["admin", "chronicle", "tokenops"].forEach((id) => {
    $("#view-" + id).classList.toggle("hidden", id !== tab);
  });
  if (tab === "admin") renderAdmin();
  if (tab === "chronicle") renderChronicle();
  if (tab === "tokenops") renderTokenops();
}

function fmtTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function traceRecency(t) {
  const raw = t.ended_at || t.started_at || "";
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? 0 : ms;
}

async function renderAdmin() {
  const el = $("#view-admin");
  el.innerHTML = `<div class="eyebrow">Control plane</div><h1>Admin</h1><p class="sub">Sidecar keys used by Chronicle and TokenOps to reach this plane. No login — paste a key if auth is on.</p>
    <div class="banner">Browser key (local only): <input id="browser-key" placeholder="Bearer token" style="min-width:280px" />
    <button class="btn ghost" id="save-key">Use</button></div>
    <div id="admin-body">Loading…</div>`;
  $("#browser-key").value = localStorage.getItem("cp_api_key") || "";
  $("#save-key").onclick = () => {
    localStorage.setItem("cp_api_key", $("#browser-key").value.trim());
    renderAdmin();
  };
  try {
    const data = await api("/v1/admin/keys");
    const rows = (data.keys || []).map((k) => `<tr>
      <td>${k.name}</td>
      <td class="mono">${k.key_prefix}…</td>
      <td>${k.tenant_id}</td>
      <td class="mono">${(k.scopes || []).join(" + ")}</td>
      <td><span class="pill">${k.source}</span></td>
      <td>${k.secret ? `<code>${k.secret}</code>` : ""}</td>
      <td>${k.source === "ui" ? `<button class="btn ghost" data-del="${k.id}">Revoke</button>` : ""}</td>
    </tr>`).join("");
    $("#admin-body").innerHTML = `
      ${data.auth_disabled ? `<div class="banner">Auth is off (no keys). Sidecars may call without a bearer. Create a key to lock ingest.</div>` : ""}
      <div class="card">
        <h2>Keys</h2>
        <table><thead><tr><th>Name</th><th>Prefix</th><th>Tenant</th><th>Scopes</th><th>Source</th><th>Secret</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="7">None yet</td></tr>`}</tbody></table>
      </div>
      <div class="card" style="margin-top:12px">
        <h2>Create sidecar key</h2>
        <div class="row">
          <input id="k-name" placeholder="chronicle-agent" />
          <input id="k-tenant" placeholder="tenant" value="local" />
          <select id="k-scopes">
            <option value="ingest+read">ingest + read (sidecar)</option>
            <option value="read+admin">read + admin (UI)</option>
            <option value="ingest">ingest only</option>
            <option value="read">read only</option>
          </select>
          <button class="btn" id="k-create">Create</button>
        </div>
        <p class="sub" id="k-once"></p>
      </div>`;
    el.querySelectorAll("[data-del]").forEach((btn) => {
      btn.onclick = async () => {
        await api("/v1/admin/keys/" + btn.dataset.del, { method: "DELETE" });
        renderAdmin();
      };
    });
    $("#k-create").onclick = async () => {
      const created = await api("/v1/admin/keys", {
        method: "POST",
        body: JSON.stringify({
          name: $("#k-name").value,
          tenant_id: $("#k-tenant").value || "local",
          scopes: $("#k-scopes").value.split("+"),
        }),
      });
      $("#k-once").innerHTML = `Copy now — shown once: <code>${created.secret}</code>`;
    };
  } catch (err) {
    $("#admin-body").innerHTML = `<p class="err">${err.message}. Set a browser key above if auth is enabled.</p>`;
  }
}

async function renderChronicle() {
  const el = $("#view-chronicle");
  const { rest } = hashParts();
  const openId = rest[0] ? decodeURIComponent(rest[0]) : "";
  if (openId) {
    el.innerHTML = `<div class="eyebrow">Chronicle</div><h1>Trace</h1><p class="sub"><a href="#chronicle">← All traces</a></p><div id="c-water"></div>`;
    await showWaterfall(openId);
    return;
  }
  el.innerHTML = `<div class="eyebrow">Chronicle</div><h1>Traces</h1><p class="sub">Newest first. Click a row for the waterfall.</p>
    <div class="row">
      <input id="c-q" placeholder="Filter by trace id or dims" style="flex:1" />
      <input id="c-session" placeholder="session_id" />
      <button class="btn" id="c-search">Filter</button>
    </div>
    <div id="c-list">Loading…</div>`;
  const search = async () => {
    const q = $("#c-q").value.trim();
    const session_id = $("#c-session").value.trim();
    const params = new URLSearchParams({ limit: "200" });
    if (q) params.set("q", q);
    if (session_id) params.set("session_id", session_id);
    try {
      const data = await api("/v1/traces?" + params.toString());
      const traces = (data.traces || []).slice().sort((a, b) => traceRecency(b) - traceRecency(a));
      $("#c-list").innerHTML = `<div class="card"><h2>${traces.length} traces · recent first</h2>
        <table><thead><tr><th>When</th><th>Trace</th><th>Spans</th><th>Dims</th></tr></thead><tbody>
        ${traces.map((t) => `<tr class="clickable" data-tr="${t.trace_id}">
          <td>${fmtTime(t.ended_at || t.started_at)}</td>
          <td class="mono">${t.trace_id}</td>
          <td>${t.span_count ?? "—"}</td>
          <td class="mono">${JSON.stringify(t.dims || {})}</td>
        </tr>`).join("") || `<tr><td colspan="4">No traces yet. Run an agent with Chronicle RemoteStore pointed here.</td></tr>`}
        </tbody></table></div>`;
      el.querySelectorAll("[data-tr]").forEach((row) => {
        row.onclick = () => { location.hash = "chronicle/" + encodeURIComponent(row.dataset.tr); };
      });
    } catch (err) {
      $("#c-list").innerHTML = `<p class="err">${err.message}</p>`;
    }
  };
  $("#c-search").onclick = search;
  $("#c-q").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  await search();
}

async function showWaterfall(traceId) {
  const box = $("#c-water");
  box.innerHTML = "Loading waterfall…";
  try {
    const data = await api("/v1/traces/" + encodeURIComponent(traceId) + "/envelopes");
    const envs = data.envelopes || [];
    const byParent = {};
    envs.forEach((e) => {
      const p = e.parent_envelope_id || "";
      (byParent[p] ||= []).push(e);
    });
    function tree(parent, depth) {
      return (byParent[parent] || []).map((e) => {
        const name = e.node_id || e.name || e.envelope_id;
        const kind = e.boundary_kind || "";
        return `<li style="margin-left:${depth * 8}px">
          <div><span class="mono">${name}</span> <span class="pill">${kind}</span></div>
          <div class="meta">${e.envelope_id} · seq ${e.sequence ?? "—"}</div>
          <ul class="waterfall">${tree(e.envelope_id, depth + 1)}</ul>
        </li>`;
      }).join("");
    }
    box.innerHTML = `<div class="card"><h2>Waterfall · ${traceId}</h2>
      <ul class="waterfall">${tree("", 0) || envs.map((e) => `<li><span class="mono">${e.node_id || e.envelope_id}</span></li>`).join("")}</ul>
    </div>`;
  } catch (err) {
    box.innerHTML = `<p class="err">${err.message}</p>`;
  }
}

async function renderTokenops() {
  const el = $("#view-tokenops");
  el.innerHTML = `<div class="eyebrow">TokenOps</div><h1>Governance</h1><p class="sub">Budgets and policies the sidecar pulls on each run. Breaching runs are those halted, throttled, or in error.</p>
    <div id="t-gov"></div>
    <div id="t-runs" style="margin-top:14px"></div>`;
  try {
    const [budgets, policies, runs, bad] = await Promise.all([
      api("/v1/budgets"),
      api("/v1/policies"),
      api("/v1/run-records?limit=100"),
      api("/v1/run-records?problematic_only=true&limit=50"),
    ]);
    $("#t-gov").innerHTML = `<div class="grid cols-2">
      <div class="card"><h2>Budgets</h2>
        <table><thead><tr><th>Id</th><th>Limit USD</th><th>Dim</th></tr></thead><tbody>
        ${(budgets || []).map((b) => `<tr><td class="mono">${b.id}</td><td>${b.limit_micros == null ? "∞" : (b.limit_micros / 1e6).toFixed(4)}</td><td>${b.dimension}</td></tr>`).join("")}
        </tbody></table>
        <div class="row" style="margin-top:10px">
          <input id="b-id" placeholder="budget id" />
          <input id="b-usd" type="number" step="0.01" value="2" />
          <button class="btn" id="b-save">Save budget</button>
        </div>
      </div>
      <div class="card"><h2>Policies</h2>
        <table><thead><tr><th>Template</th><th>Budget</th><th>On</th></tr></thead><tbody>
        ${(policies || []).map((p) => `<tr><td class="mono">${p.template}</td><td>${p.budget_id || "—"}</td><td>${p.enabled ? "yes" : "no"}</td></tr>`).join("")}
        </tbody></table>
      </div>
    </div>`;
    $("#b-save").onclick = async () => {
      const usd = parseFloat($("#b-usd").value);
      await api("/v1/budgets", {
        method: "PUT",
        body: JSON.stringify({
          id: $("#b-id").value.trim(),
          limit_micros: Number.isFinite(usd) ? Math.round(usd * 1e6) : null,
          dimension: "run",
        }),
      });
      renderTokenops();
    };
    const list = (title, items) => `<div class="card"><h2>${title}</h2>
      <table><thead><tr><th>Run</th><th>Agent</th><th>Status</th><th>Cost</th><th>Halt</th></tr></thead><tbody>
      ${(items || []).map((r) => `<tr>
        <td class="mono">${r.run_id}</td><td>${r.agent}</td><td>${pill(r.status)}</td>
        <td>$${((r.cost_micros || 0) / 1e6).toFixed(4)}</td>
        <td>${r.halt_reason || ""}</td>
      </tr>`).join("") || `<tr><td colspan="5">None</td></tr>`}
      </tbody></table></div>`;
    $("#t-runs").innerHTML = list("Breaches", bad) + `<div style="height:12px"></div>` + list("Recent runs", runs);
  } catch (err) {
    $("#t-gov").innerHTML = `<p class="err">${err.message}</p>`;
  }
}

window.addEventListener("hashchange", () => switchTab(hashParts().tab));
document.querySelectorAll(".nav-links a.tab").forEach((a) => {
  a.addEventListener("click", (e) => {
    e.preventDefault();
    location.hash = a.dataset.tab;
  });
});
const themeBtn = document.getElementById("theme-switch");
function syncThemeSwitch(theme) {
  if (!themeBtn) return;
  const dark = theme === "dark";
  themeBtn.setAttribute("aria-checked", dark ? "true" : "false");
  themeBtn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  themeBtn.setAttribute("title", dark ? "Switch to light mode" : "Switch to dark mode");
}
if (themeBtn) {
  syncThemeSwitch(document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light");
  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("agentplane-theme", next);
    syncThemeSwitch(next);
  });
}
switchTab(hashParts().tab);
