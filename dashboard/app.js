/* AEDI - IONITY GLOBAL | Fleet Monitor client  v2
   (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Policy 986 AED */
"use strict";

const $ = (id) => document.getElementById(id);
const API = "";

let LAST = { summary: null, devices: [] };
let RATE = [];
let FILTER = { search: "", health: "", site: "", group: "" };
let SERVER_IP = location.hostname;

/* Everything rendered with innerHTML goes through esc(): DNS names, labels and
   hostnames come from arbitrary LAN devices and must never be treated as HTML. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ago = (s) => {
  if (s == null) return "never";
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`;
  return `${(s / 86400).toFixed(1)}d ago`;
};
const hms = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });

/* metric -> [label, formatter returning [value, unit]] ; order = display priority */
const METRICS = {
  temp_c:          ["Temp",    (v) => [v.toFixed(1), "°C"]],
  rssi_dbm:        ["WiFi",    (v) => [Math.round(v), "dBm"]],
  free_heap_bytes: ["Heap",    (v) => [(v / 1024).toFixed(0), "KB"]],
  analog_v:        ["Analog",  (v) => [v.toFixed(2), "V"]],
  cpu_mhz:         ["CPU",     (v) => [Math.round(v), "MHz"]],
  digital_state:   ["Digital", (v) => [v ? "HIGH" : "LOW", ""]],
  oled:            ["OLED",    (v) => [v ? "yes" : "none", ""]],
};
function metricTiles(m, max = 4) {
  const keys = [...Object.keys(METRICS).filter((k) => k in m),
                ...Object.keys(m).filter((k) => !(k in METRICS))].slice(0, max);
  return keys.map((k) => {
    const v = m[k];
    const [lab, fmt] = METRICS[k] || [k.replace(/_/g, " "), (x) => [typeof x === "number" ? +x.toFixed(2) : x, ""]];
    const [val, unit] = typeof v === "number" ? fmt(v) : [v ?? "—", ""];
    return `<div class="mt"><div class="mt-l">${esc(lab)}</div>
      <div class="mt-v">${esc(val)}${unit ? `<small>${esc(unit)}</small>` : ""}</div></div>`;
  }).join("");
}
function fmtMetric(v) {
  if (typeof v === "number") return Number.isInteger(v) ? v : v.toFixed(2);
  return v;
}

/* ----------------------------------------------------------------- */
/* WebSocket                                                          */
/* ----------------------------------------------------------------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/fleet`);
  ws.onopen = () => {
    $("wsDot").className = "dot live";
    $("wsLabel").textContent = "live";
    $("wsPill").className = "pill good";
  };
  ws.onclose = () => {
    $("wsDot").className = "dot down";
    $("wsLabel").textContent = "reconnecting…";
    $("wsPill").className = "pill badp";
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
  $("rateNow").textContent = `${s.messages_last_minute} msgs · last 60s`;

  drawHealth(s);
  fillSelect($("fSite"), s.sites, FILTER.site);
  fillSelect($("fGroup"), s.groups, FILTER.group);

  drawGrid(msg.devices);
  $("truncNote").textContent = msg.truncated
    ? `Showing the first ${msg.devices.length} devices by priority (alerting → offline → stale → online). Use search or filters, or /api/v1/devices, to page through the full fleet.`
    : "";
}

function fillSelect(el, obj, current) {
  const keys = Object.keys(obj || {}).sort();
  const sig = keys.join("|");
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  const label = el.options[0].textContent;
  el.innerHTML = `<option value="">${esc(label)}</option>` + keys.map((k) =>
    `<option value="${esc(k)}"${k === current ? " selected" : ""}>${esc(k)} (${obj[k]})</option>`).join("");
}

function drawHealth(s) {
  const parts = [["ok", s.online], ["warn", s.stale], ["bad", s.offline], ["crit", s.alerting]];
  const total = Math.max(1, s.total_devices);
  $("healthBar").innerHTML = parts.filter(([, n]) => n > 0).map(([c, n]) =>
    `<span style="flex:${n};background:var(--${c})" title="${n}"></span>`).join("");
  $("lgOk").textContent = s.online; $("lgWarn").textContent = s.stale;
  $("lgBad").textContent = s.offline; $("lgCrit").textContent = s.alerting;
  $("healthNow").textContent =
    `${((s.online / total) * 100).toFixed(0)}% reporting · ${s.open_alerts} open alerts`;
}

function drawSpark(cv, data) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 96;
  if (!w) return;
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + "px";
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  // faint guide lines
  ctx.strokeStyle = "rgba(148,178,220,.08)"; ctx.lineWidth = 1;
  for (const f of [0.25, 0.5, 0.75]) { ctx.beginPath(); ctx.moveTo(0, h * f); ctx.lineTo(w, h * f); ctx.stroke(); }
  if (data.length < 2) return;

  const max = Math.max(0.5, ...data) * 1.15, pad = 6;
  const x = (i) => (i / (data.length - 1)) * w;
  const y = (v) => h - pad - (v / max) * (h - pad * 2);

  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, "rgba(40,211,245,.32)");
  g.addColorStop(1, "rgba(40,211,245,0)");
  ctx.beginPath(); ctx.moveTo(0, h);
  data.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(w, h); ctx.closePath(); ctx.fillStyle = g; ctx.fill();

  ctx.beginPath();
  data.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = "#28d3f5"; ctx.lineWidth = 1.8; ctx.lineJoin = "round"; ctx.stroke();

  const lx = x(data.length - 1), ly = y(data[data.length - 1]);
  ctx.beginPath(); ctx.arc(lx - 2, ly, 3.2, 0, Math.PI * 2); ctx.fillStyle = "#28d3f5"; ctx.fill();
  ctx.fillStyle = "rgba(138,155,180,.9)"; ctx.font = "11px Segoe UI, system-ui, sans-serif";
  ctx.fillText(`peak ${Math.max(...data).toFixed(1)}/s`, 4, 13);
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

function drawGrid(devices) {
  const list = devices.filter(passes);
  $("shown").textContent = `${list.length} of ${devices.length} shown`;
  $("grid").innerHTML = list.map((d) => {
    const name = d.label || d.device_id;
    const where = d.ip || (d.transport === "serial" ? "USB serial" : "—");
    return `<div class="card ${esc(d.health)}" data-id="${esc(d.device_id)}" tabindex="0">
      <div class="c-top">
        <div class="c-name">
          <div class="c-lab" title="${esc(name)}">${esc(name)}</div>
          <div class="c-id">${esc(d.device_id)}</div>
        </div>
        <span class="hp">${esc(d.health)}</span>
      </div>
      <div class="c-badges">
        <span class="bdg acc">${esc(d.transport || "?")}</span>
        <span class="bdg">fw ${esc(d.fw || "?")}</span>
        <span class="bdg">${esc(d.site)}/${esc(d.group)}</span>
      </div>
      <div class="c-m">${metricTiles(d.metrics || {})}</div>
      <div class="c-foot"><span>seen ${esc(ago(d.last_seen_age_s))}</span><span>${esc(where)}</span></div>
    </div>`;
  }).join("") || `<div class="empty">No devices match these filters. Nothing reporting yet? Plug a board in and run
      <code>scripts\\add_device.ps1 -Port COMx</code>.</div>`;
}

/* ----------------------------------------------------------------- */
/* Device drawer                                                      */
/* ----------------------------------------------------------------- */
function closeDrawer() { $("drawer").classList.remove("open"); $("scrim").classList.remove("open"); }

async function openDevice(id) {
  const r = await fetch(`${API}/api/v1/devices/${encodeURIComponent(id)}?history=40`);
  if (!r.ok) return;
  const { device: d, history } = await r.json();

  const rows = [
    ["Health", d.health], ["Site / group", `${d.site} / ${d.group}`],
    ["Label", d.label || "—"], ["Firmware", d.fw || "—"],
    ["IP", d.ip || "—"], ["Transport", d.transport],
    ["Uptime", d.uptime_s != null ? `${(d.uptime_s / 3600).toFixed(1)} h` : "—"],
    ["Messages", d.msg_count],
    ["Last seen", ago(d.last_seen_age_s)],
    ["Active alerts", d.active_alerts.length ? d.active_alerts.join(", ") : "none"],
  ];
  const metrics = Object.entries(d.metrics || {})
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(fmtMetric(v))}</td></tr>`).join("");

  $("dTitle").textContent = d.label || d.device_id;
  $("dBody").innerHTML = `
    <div class="acts">
      <button class="btn sm" data-cmd="identify">Identify</button>
      <button class="btn sm ghost" data-cmd="ping">Ping</button>
      <button class="btn sm ghost" data-cmd="reboot">Reboot</button>
    </div>
    <table><tr><td>Device ID</td><td><code>${esc(d.device_id)}</code></td></tr>
      ${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>
    <div class="chart-h"><span>Latest metrics</span></div>
    <table>${metrics || '<tr><td colspan="2" class="muted">no metrics yet</td></tr>'}</table>
    <div class="chart-h"><span>Recent readings</span><span class="muted">${history.length} rows</span></div>
    <pre class="out">${esc(JSON.stringify(history.slice(0, 8), null, 1))}</pre>`;

  $("dBody").querySelectorAll("[data-cmd]").forEach((b) =>
    b.onclick = async () => {
      const label = b.textContent;
      b.disabled = true;
      const res = await fetch(`${API}/api/v1/devices/${encodeURIComponent(d.device_id)}/cmd`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: b.dataset.cmd }),
      }).then((x) => x.json()).catch(() => ({ ok: false }));
      b.textContent = res.ok ? "sent ✓" : "failed";
      setTimeout(() => { b.disabled = false; b.textContent = label; }, 1800);
    });

  $("drawer").classList.add("open");
  $("scrim").classList.add("open");
}

/* ----------------------------------------------------------------- */
/* Alerts                                                             */
/* ----------------------------------------------------------------- */
async function refreshAlerts() {
  try {
    const { alerts } = await fetch(`${API}/api/v1/alerts?open_only=true&limit=60`).then((r) => r.json());
    $("alertCount").textContent = `${alerts.length} open`;
    $("alerts").innerHTML = alerts.map((a) => `
      <div class="alert ${esc(a.severity)}">
        <span class="a-dev">${esc(a.device_id)}</span>
        <span>${esc(a.message)}</span>
        <span class="a-t">${esc(hms(a.raised_at))}</span>
      </div>`).join("") || '<div class="ok-note">No open alerts. Fleet is clean.</div>';
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

function defaultArgs(tool) {
  const first = (LAST.devices && LAST.devices[0] && LAST.devices[0].device_id) || "esp32-000000000001";
  return ({
    fleet_summary: "{}",
    list_devices: '{"limit":20}',
    get_device: JSON.stringify({ device_id: first }),
    query_telemetry: JSON.stringify({ device_id: first, metric: "temp_c", minutes: 60, limit: 50 }),
    aggregate_metric: '{"metric":"rssi_dbm","minutes":60}',
    get_alerts: '{"open_only":true,"limit":20}',
    send_command: JSON.stringify({ device_id: first, action: "ping" }),
    dns_summary: '{"minutes":60}',
    dns_top_domains: '{"minutes":1440,"limit":20}',
    dns_recent: '{"limit":30}',
    list_lan_devices: "{}",
  })[tool] ?? "{}";
}

async function loadTools() {
  try {
    const res = await rpc("tools/list", {});
    const tools = res.result?.tools || [];
    $("mcpTool").innerHTML = tools.map((t) =>
      `<option value="${esc(t.name)}" title="${esc(t.description)}">${esc(t.name)}</option>`).join("");
    $("mcpArgs").value = defaultArgs($("mcpTool").value);
  } catch { /* ignore */ }
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
$("grid").onkeydown = (e) => {
  const card = e.target.closest(".card");
  if (card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDevice(card.dataset.id); }
};
$("dClose").onclick = closeDrawer;
$("scrim").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

$("btnBroadcast").onclick = async () => {
  const res = await fetch(`${API}/api/v1/devices/broadcast/cmd`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "identify" }),
  }).then((r) => r.json()).catch(() => ({ ok: false }));
  $("btnBroadcast").textContent = res.ok ? "sent ✓" : (res.error || "failed");
  setTimeout(() => ($("btnBroadcast").textContent = "Identify all"), 2200);
};

$("mcpTool").onchange = () => { $("mcpArgs").value = defaultArgs($("mcpTool").value); };
$("mcpRun").onclick = async () => {
  let args;
  try { args = JSON.parse($("mcpArgs").value || "{}"); }
  catch (err) { $("mcpOut").textContent = "Invalid JSON arguments: " + err.message; return; }
  $("mcpOut").textContent = "calling…";
  try {
    const res = await rpc("tools/call", { name: $("mcpTool").value, arguments: args });
    const text = res.result?.content?.[0]?.text;
    let pretty = text;
    try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* plain text */ }
    $("mcpOut").textContent = pretty || JSON.stringify(res, null, 2);
  } catch (err) { $("mcpOut").textContent = "Call failed: " + err.message; }
};

async function pollHealth() {
  try {
    const h = await fetch(`${API}/api/v1/health`).then((r) => r.json());
    const mq = h.mqtt || {};
    $("mqttLabel").textContent = mq.connected ? `mqtt ${mq.host} ✓` : "mqtt down → http fallback";
    $("mqttPill").className = mq.connected ? "pill good" : "pill badp";
    const dsc = h.discovery || {};
    if (dsc.advertised_ip) SERVER_IP = dsc.advertised_ip;
    $("mdnsLabel").textContent = dsc.error ? "mdns off" : `${dsc.hostname || "mdns"} → ${dsc.advertised_ip || "?"}`;
    $("mdnsPill").className = dsc.error ? "pill badp" : (dsc.advertised_ip ? "pill good" : "pill");
    $("mdnsPill").title = dsc.error || "";
  } catch {
    $("mqttLabel").textContent = "server unreachable";
    $("mqttPill").className = "pill badp";
  }
}

function tickClock() { $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }

connect();
loadTools();
refreshAlerts();
pollHealth();
tickClock();
setInterval(tickClock, 1000);
setInterval(refreshAlerts, 10000);
setInterval(pollHealth, 10000);
window.addEventListener("resize", () => drawSpark($("rateChart"), RATE));

/* ----------------------------------------------------------------- */
/* LAN DNS panel                                                      */
/* ----------------------------------------------------------------- */
let DNS_WINDOW = 1440;
let DNS_QUERY = "";

function devName(d) { return d.label || d.hostname || d.vendor || "unidentified"; }

function feedRow(q, timeText) {
  const who = q.label || q.hostname || q.client_ip || "?";
  let status = "";
  if (q.cached) status = '<span class="chip c">cached</span>';
  else if (q.rcode && q.rcode !== "NOERROR") status = `<span class="chip x">${esc(q.rcode)}</span>`;
  return `<div class="q">
    <span class="q-t">${esc(timeText)}</span>
    <span class="q-who" title="${esc(who)} · ${esc(q.client_ip)}">${esc(who)}</span>
    <span class="q-n" title="${esc(q.qname)}">${esc(q.qname)}</span>
    <span class="q-ty">${esc(q.qtype || "")}</span>
    <span class="q-s">${status}</span>
  </div>`;
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
      $("dnsState").textContent = `resolver up on ${res.bind} → ${(res.upstreams || []).join(", ")}`;
    } else if (res.bind_error) {
      $("dnsState").textContent = `resolver DOWN — ${res.bind_error}`;
    } else {
      $("dnsState").textContent = "resolver disabled";
    }

    $("dQueries").textContent = sum.queries ?? 0;
    $("dDomains").textContent = sum.domains ?? 0;
    $("dDevices").textContent = sum.devices ?? 0;
    $("dCache").textContent = res.cache_hit_rate != null ? `${(res.cache_hit_rate * 100).toFixed(0)}%` : "—";
    $("dLatency").textContent = sum.avg_latency_ms != null ? sum.avg_latency_ms.toFixed(0) : "—";

    const list = devs.devices || [];
    $("dnsDevices").innerHTML = list.map((d) => `
      <div class="dnsdev">
        <div class="dnsdev-h">
          <span class="dnsdev-name">${esc(devName(d))}</span>
          <span class="dnsdev-ip">${esc(d.client_ip)}</span>
          <span class="dnsdev-meta">${esc(d.queries)} queries · ${esc(d.distinct_domains)} domains</span>
        </div>
        <div class="dnsdev-doms">
          ${(d.top_domains || []).map((t) =>
            `<span class="dom" title="${esc(t.qname)}">${esc(t.qname)}<b>${esc(t.hits)}</b></span>`).join("")}
        </div>
      </div>`).join("") || `<div class="dns-empty">
        No DNS queries in this window. The resolver is listening, but devices are still using the
        router's DNS. To see the whole LAN here, set the router's DHCP DNS server to
        <code>${esc(SERVER_IP)}</code>.</div>`;

    if (DNS_QUERY) return;              // a search result is on screen - leave it
    const qs = feed.queries || [];
    $("dnsFeedCount").textContent = `${qs.length} most recent`;
    $("dnsFeed").innerHTML = qs.map((q) => feedRow(q, hms(q.ts))).join("")
      || '<div class="feed-empty">Waiting for queries…</div>';
  } catch {
    $("dnsState").textContent = "resolver unreachable";
  }
}

async function runDnsSearch() {
  if (!DNS_QUERY) { $("dnsHint").textContent = ""; refreshDns(); return; }
  try {
    const r = await fetch(
      `${API}/api/v1/dns/search?pattern=${encodeURIComponent(DNS_QUERY)}&minutes=${DNS_WINDOW}&limit=200`
    ).then((x) => x.json());
    $("dnsHint").textContent = `${r.matches} matches for "${r.pattern}"`;
    $("dnsFeedCount").textContent = "search results";
    $("dnsFeed").innerHTML = (r.rows || []).map((q) => feedRow(q, hms(q.ts))).join("")
      || '<div class="feed-empty">Nothing on the LAN has resolved that.</div>';
  } catch { $("dnsHint").textContent = "search failed"; }
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
