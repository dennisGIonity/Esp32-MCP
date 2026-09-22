/* AEDI - IONITY GLOBAL | Fleet Monitor client
   (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Policy 986 AED */
"use strict";

const $ = (id) => document.getElementById(id);
const API = "";

let LAST = { summary: null, devices: [] };
let RATE = [];
let FILTER = { search: "", health: "", site: "", group: "" };

/* ----------------------------------------------------------------- */
/* WebSocket                                                          */
/* ----------------------------------------------------------------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/fleet`);

  ws.onopen = () => {
    $("wsDot").className = "dot live";
    $("wsLabel").textContent = "live";
  };
  ws.onclose = () => {
    $("wsDot").className = "dot down";
    $("wsLabel").textContent = "reconnecting…";
    setTimeout(connect, 2500);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type !== "fleet_tick") return;
    LAST = msg;
    render(msg);
  };
}

/* ----------------------------------------------------------------- */
/* Render                                                             */
/* ----------------------------------------------------------------- */
function render(msg) {
  const s = msg.summary;
  $("kTotal").textContent = s.total_devices;
  $("kOnline").textContent = s.online;
  $("kStale").textContent = s.stale;
  $("kOffline").textContent = s.offline;
  $("kAlert").textContent = s.alerting;
  $("kRate").textContent = s.ingest_rate_per_s.toFixed(1);
  $("kQueue").textContent = msg.queue_depth;

  RATE.push(s.ingest_rate_per_s);
  if (RATE.length > 120) RATE.shift();
  drawSpark($("rateChart"), RATE);
  $("rateNow").textContent = `${s.messages_last_minute} msgs / last 60s`;

  drawHealth(s);
  fillSelect($("fSite"), s.sites, FILTER.site);
  fillSelect($("fGroup"), s.groups, FILTER.group);

  drawGrid(msg.devices);
  $("truncNote").textContent = msg.truncated
    ? `Showing the first ${msg.devices.length} devices by priority (alerting → offline → stale → online). Use search or filters, or the /api/v1/devices endpoint, to page through the full fleet.`
    : "";
}

function fillSelect(el, obj, current) {
  const keys = Object.keys(obj || {}).sort();
  const sig = keys.join("|");
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  const label = el.options[0].textContent;
  el.innerHTML = `<option value="">${label}</option>` +
    keys.map((k) => `<option value="${k}"${k === current ? " selected" : ""}>${k} (${obj[k]})</option>`).join("");
}

function drawHealth(s) {
  const parts = [
    ["ok", s.online], ["warn", s.stale], ["bad", s.offline], ["crit", s.alerting],
  ];
  const total = Math.max(1, s.total_devices);
  $("healthBar").innerHTML = parts.map(([c, n]) =>
    `<span class="sw-${c}" style="flex:${n};background:var(--${c === "ok" ? "ok" : c === "warn" ? "warn" : c === "bad" ? "bad" : "crit"})" title="${n}"></span>`
  ).join("");
  $("healthNow").textContent =
    `${((s.online / total) * 100).toFixed(1)}% reporting · ${s.open_alerts} open alerts`;
}

function drawSpark(cv, data) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.height;
  cv.width = w * dpr; cv.style.height = h + "px";
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (data.length < 2) return;

  const max = Math.max(1, ...data), pad = 4;
  const x = (i) => (i / (data.length - 1)) * w;
  const y = (v) => h - pad - (v / max) * (h - pad * 2);

  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, "rgba(40,211,245,.38)");
  g.addColorStop(1, "rgba(40,211,245,0)");
  ctx.beginPath(); ctx.moveTo(0, h);
  data.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(w, h); ctx.closePath(); ctx.fillStyle = g; ctx.fill();

  ctx.beginPath();
  data.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = "#28d3f5"; ctx.lineWidth = 1.6; ctx.stroke();
}

function passes(d) {
  if (FILTER.health && d.health !== FILTER.health) return false;
  if (FILTER.site && d.site !== FILTER.site) return false;
  if (FILTER.group && d.group !== FILTER.group) return false;
  if (FILTER.search) {
    const hay = `${d.device_id} ${d.label || ""} ${d.ip || ""}`.toLowerCase();
    if (!hay.includes(FILTER.search)) return false;
  }
  return true;
}

function fmtMetric(k, v) {
  if (typeof v === "number") return Number.isInteger(v) ? v : v.toFixed(1);
  return v;
}

function drawGrid(devices) {
  const list = devices.filter(passes);
  $("shown").textContent = `${list.length} shown`;
  $("grid").innerHTML = list.map((d) => {
    const m = d.metrics || {};
    const chips = Object.entries(m).slice(0, 4)
      .map(([k, v]) => `<span>${k.replace(/_/g, " ")} <b>${fmtMetric(k, v)}</b></span>`).join("");
    const age = d.last_seen_age_s == null ? "never" : `${d.last_seen_age_s.toFixed(0)}s ago`;
    return `<div class="card ${d.health}" data-id="${d.device_id}">
      <div class="c-id">${d.device_id}</div>
      <div class="c-lab">${d.label || d.group} · ${age}</div>
      <div class="c-m">${chips}</div>
    </div>`;
  }).join("") || `<div class="muted">No devices match. Nothing has reported yet? Run
      <code>python scripts/fleet_simulator.py --devices 50</code>.</div>`;
}

/* ----------------------------------------------------------------- */
/* Device drawer                                                      */
/* ----------------------------------------------------------------- */
async function openDevice(id) {
  const r = await fetch(`${API}/api/v1/devices/${encodeURIComponent(id)}?history=40`);
  if (!r.ok) return;
  const { device: d, history } = await r.json();

  const rows = [
    ["Health", d.health], ["Site", d.site], ["Group", d.group],
    ["Label", d.label || "—"], ["Firmware", d.fw || "—"],
    ["IP", d.ip || "—"], ["Transport", d.transport],
    ["Uptime", d.uptime_s != null ? `${(d.uptime_s / 3600).toFixed(1)} h` : "—"],
    ["Messages", d.msg_count],
    ["Last seen", d.last_seen_age_s != null ? `${d.last_seen_age_s.toFixed(0)}s ago` : "never"],
    ["Active alerts", d.active_alerts.length ? d.active_alerts.join(", ") : "none"],
  ];
  const metrics = Object.entries(d.metrics || {})
    .map(([k, v]) => `<tr><td>${k}</td><td>${fmtMetric(k, v)}</td></tr>`).join("");

  $("dTitle").textContent = d.device_id;
  $("dBody").innerHTML = `
    <div class="acts">
      <button class="btn sm" data-cmd="identify">Identify</button>
      <button class="btn sm ghost" data-cmd="ping">Ping</button>
      <button class="btn sm ghost" data-cmd="reboot">Reboot</button>
    </div>
    <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>
    <div class="chart-h"><span>Latest metrics</span></div>
    <table>${metrics || '<tr><td colspan="2" class="muted">no metrics yet</td></tr>'}</table>
    <div class="chart-h"><span>Recent readings</span><span class="muted">${history.length} rows</span></div>
    <pre class="out">${JSON.stringify(history.slice(0, 8), null, 1)}</pre>`;

  $("dBody").querySelectorAll("[data-cmd]").forEach((b) =>
    b.onclick = async () => {
      b.disabled = true;
      const res = await fetch(`${API}/api/v1/devices/${encodeURIComponent(d.device_id)}/cmd`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: b.dataset.cmd }),
      }).then((x) => x.json());
      b.textContent = res.ok ? "sent ✓" : "failed";
      setTimeout(() => { b.disabled = false; b.textContent = b.dataset.cmd; }, 1800);
    });

  $("drawer").classList.add("open");
}

/* ----------------------------------------------------------------- */
/* Alerts                                                             */
/* ----------------------------------------------------------------- */
async function refreshAlerts() {
  try {
    const { alerts } = await fetch(`${API}/api/v1/alerts?open_only=true&limit=60`).then((r) => r.json());
    $("alertCount").textContent = `${alerts.length} open`;
    $("alerts").innerHTML = alerts.map((a) => `
      <div class="alert ${a.severity}">
        <span class="a-dev">${a.device_id}</span>
        <span>${a.message}</span>
        <span class="a-t">${new Date(a.raised_at * 1000).toLocaleTimeString()}</span>
      </div>`).join("") || '<div class="muted">No open alerts. Fleet is clean.</div>';
  } catch { /* server not up yet */ }
}

/* ----------------------------------------------------------------- */
/* MCP console                                                        */
/* ----------------------------------------------------------------- */
async function rpc(method, params) {
  const r = await fetch(`${API}/api/v1/mcp/rpc`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method, params }),
  });
  return r.json();
}

const DEFAULT_ARGS = {
  fleet_summary: "{}",
  list_devices: '{"health":"alerting","limit":20}',
  get_device: '{"device_id":"esp32-000000000001"}',
  query_telemetry: '{"metric":"temp_c","minutes":60,"limit":100}',
  aggregate_metric: '{"metric":"rssi_dbm","minutes":60}',
  get_alerts: '{"open_only":true,"limit":20}',
  send_command: '{"device_id":"broadcast","action":"identify"}',
};

async function loadTools() {
  try {
    const res = await rpc("tools/list", {});
    const tools = res.result?.tools || [];
    $("mcpTool").innerHTML = tools
      .map((t) => `<option value="${t.name}" title="${t.description}">${t.name}</option>`).join("");
    syncArgs();
  } catch { /* ignore */ }
}
function syncArgs() {
  $("mcpArgs").value = DEFAULT_ARGS[$("mcpTool").value] ?? "{}";
}

/* ----------------------------------------------------------------- */
/* Wiring                                                             */
/* ----------------------------------------------------------------- */
$("search").oninput = (e) => { FILTER.search = e.target.value.trim().toLowerCase(); drawGrid(LAST.devices || []); };
$("fHealth").onchange = (e) => { FILTER.health = e.target.value; drawGrid(LAST.devices || []); };
$("fSite").onchange = (e) => { FILTER.site = e.target.value; drawGrid(LAST.devices || []); };
$("fGroup").onchange = (e) => { FILTER.group = e.target.value; drawGrid(LAST.devices || []); };

$("grid").onclick = (e) => {
  const card = e.target.closest(".card");
  if (card) openDevice(card.dataset.id);
};
$("dClose").onclick = () => $("drawer").classList.remove("open");
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("drawer").classList.remove("open");
});

$("btnBroadcast").onclick = async () => {
  const res = await fetch(`${API}/api/v1/devices/broadcast/cmd`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "identify" }),
  }).then((r) => r.json());
  $("btnBroadcast").textContent = res.ok ? "sent ✓" : (res.error || "failed");
  setTimeout(() => ($("btnBroadcast").textContent = "Identify all"), 2200);
};

$("mcpTool").onchange = syncArgs;
$("mcpRun").onclick = async () => {
  let args;
  try { args = JSON.parse($("mcpArgs").value || "{}"); }
  catch (err) { $("mcpOut").textContent = "Invalid JSON arguments: " + err.message; return; }
  $("mcpOut").textContent = "calling…";
  const res = await rpc("tools/call", { name: $("mcpTool").value, arguments: args });
  const text = res.result?.content?.[0]?.text;
  $("mcpOut").textContent = text || JSON.stringify(res, null, 2);
};

async function pollHealth() {
  try {
    const h = await fetch(`${API}/api/v1/health`).then((r) => r.json());
    $("mqttLabel").textContent = h.mqtt?.connected
      ? `mqtt ${h.mqtt.host} ✓` : "mqtt down → http fallback";
  } catch { $("mqttLabel").textContent = "server unreachable"; }
}

connect();
loadTools();
refreshAlerts();
pollHealth();
setInterval(refreshAlerts, 10000);
setInterval(pollHealth, 10000);
window.addEventListener("resize", () => drawSpark($("rateChart"), RATE));

/* ----------------------------------------------------------------- */
/* LAN DNS panel                                                      */
/* ----------------------------------------------------------------- */
let DNS_WINDOW = 1440;
let DNS_QUERY = "";

function devName(d) {
  return d.label || d.hostname || d.vendor || "unidentified";
}

async function refreshDns() {
  try {
    const [sum, devs, feed] = await Promise.all([
      fetch(`${API}/api/v1/dns/summary?minutes=${DNS_WINDOW}`).then((r) => r.json()),
      fetch(`${API}/api/v1/dns/devices?minutes=${DNS_WINDOW}&limit=40`).then((r) => r.json()),
      fetch(`${API}/api/v1/dns/recent?limit=120`).then((r) => r.json()),
    ]);

    const res = sum.resolver || {};
    if (res.running) {
      $("dnsState").textContent =
        `resolver up on ${res.bind} → ${(res.upstreams || []).join(", ")}`;
    } else if (res.bind_error) {
      $("dnsState").textContent = `resolver DOWN — ${res.bind_error}`;
    } else {
      $("dnsState").textContent = "resolver disabled";
    }

    $("dQueries").textContent = sum.queries ?? 0;
    $("dDomains").textContent = sum.domains ?? 0;
    $("dDevices").textContent = sum.devices ?? 0;
    $("dCache").textContent = res.cache_hit_rate != null
      ? `${(res.cache_hit_rate * 100).toFixed(0)}%` : "—";
    $("dLatency").textContent = sum.avg_latency_ms != null
      ? sum.avg_latency_ms.toFixed(0) : "—";

    // devices with their top domains
    const list = devs.devices || [];
    $("dnsDevices").innerHTML = list.map((d) => `
      <div class="dnsdev">
        <div class="dnsdev-h">
          <span class="dnsdev-ip">${d.client_ip}</span>
          <span class="dnsdev-name">${devName(d)}</span>
          <span class="dnsdev-meta">${d.queries} queries · ${d.distinct_domains} domains</span>
        </div>
        <div class="dnsdev-doms">
          ${(d.top_domains || []).map((t) =>
            `<span class="dom">${t.qname}<b>${t.hits}</b></span>`).join("")}
        </div>
      </div>`).join("") || `<div class="muted">
        No DNS queries logged yet. The resolver is listening, but devices are still
        using the router's DNS — point the router's DHCP at 192.168.2.11 to route
        the whole LAN through it.</div>`;

    // live feed
    const qs = feed.queries || [];
    $("dnsFeedCount").textContent = `${qs.length} most recent`;
    $("dnsFeed").innerHTML = qs.map((q) => {
      const t = new Date(q.ts * 1000).toLocaleTimeString([], { hour12: false });
      const who = q.label || q.hostname || q.client_ip;
      const mark = q.cached ? '<span class="q-c">·cached</span>' : "";
      return `<div class="q"><span class="q-t">${t}</span>
        <span class="q-ip">${who}</span>
        <span class="q-n">${q.qname} <span class="muted">${q.qtype}</span></span>${mark}</div>`;
    }).join("") || '<div class="muted">waiting for queries…</div>';
  } catch {
    $("dnsState").textContent = "resolver unreachable";
  }
}

async function runDnsSearch() {
  if (!DNS_QUERY) { $("dnsHint").textContent = ""; refreshDns(); return; }
  const r = await fetch(
    `${API}/api/v1/dns/search?pattern=${encodeURIComponent(DNS_QUERY)}&minutes=${DNS_WINDOW}&limit=200`
  ).then((x) => x.json());
  $("dnsHint").textContent = `${r.matches} matches for "${r.pattern}"`;
  $("dnsFeedCount").textContent = "search results";
  $("dnsFeed").innerHTML = (r.rows || []).map((q) => {
    const t = new Date(q.ts * 1000).toLocaleString([], { hour12: false });
    const who = q.label || q.hostname || q.client_ip;
    return `<div class="q"><span class="q-t">${t.split(", ")[1] || t}</span>
      <span class="q-ip">${who}</span>
      <span class="q-n">${q.qname} <span class="muted">${q.qtype}</span></span></div>`;
  }).join("") || '<div class="muted">nothing on the LAN has resolved that</div>';
}

let dnsSearchTimer = null;
$("dnsSearch").oninput = (e) => {
  DNS_QUERY = e.target.value.trim();
  clearTimeout(dnsSearchTimer);
  dnsSearchTimer = setTimeout(runDnsSearch, 350);
};
$("dnsWindow").onchange = (e) => {
  DNS_WINDOW = parseInt(e.target.value, 10);
  DNS_QUERY ? runDnsSearch() : refreshDns();
};

refreshDns();
setInterval(() => { if (!DNS_QUERY) refreshDns(); }, 5000);
