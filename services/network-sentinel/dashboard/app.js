// ============================================================================
// Kelvin Drive Network Sentinel Dashboard Client
// ============================================================================

let ws = null;
let trafficChart = null;
const maxChartPoints = 20;
const chartLabels = [];
const rxDataPoints = [];
const txDataPoints = [];

// Initialize Chart.js
function initChart() {
    const ctx = document.getElementById('trafficChart').getContext('2d');
    
    // Fill initial placeholder data
    for (let i = 0; i < maxChartPoints; i++) {
        chartLabels.push('');
        rxDataPoints.push(0);
        txDataPoints.push(0);
    }

    trafficChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: chartLabels,
            datasets: [
                {
                    label: 'Ingress (RX Mbps)',
                    borderColor: '#00f0ff',
                    backgroundColor: 'rgba(0, 240, 255, 0.1)',
                    borderWidth: 2,
                    tension: 0.35,
                    fill: true,
                    data: rxDataPoints
                },
                {
                    label: 'Egress (TX Mbps)',
                    borderColor: '#a855f7',
                    backgroundColor: 'rgba(168, 85, 247, 0.05)',
                    borderWidth: 2,
                    tension: 0.35,
                    fill: true,
                    data: txDataPoints
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 400 },
            plugins: {
                legend: {
                    labels: { color: '#94a3b8', font: { family: 'JetBrains Mono', size: 11 } }
                }
            },
            scales: {
                x: { display: false },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#64748b', font: { family: 'JetBrains Mono' } }
                }
            }
        }
    });
}

// WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('[Sentinel WS] Connected to live telemetry stream.');
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateDashboard(data);
        } catch (e) {
            console.error('[Sentinel WS] Error parsing telemetry:', e);
        }
    };

    ws.onclose = () => {
        console.warn('[Sentinel WS] Disconnected. Reconnecting in 2s...');
        setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = (err) => {
        console.error('[Sentinel WS] Error:', err);
        ws.close();
    };
}

// Update DOM with Telemetry Data.
// Every value can be null (the sentinel never invents numbers): show "—" for unknown.
// Text from the router (firewall log) is external input: set it with textContent, never innerHTML.
const num = (v, d = 1) => (typeof v === 'number' && isFinite(v) ? v.toFixed(d) : '—');
const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
function cell(text, style) {
    const td = document.createElement('td');
    td.textContent = text;
    if (style) td.style.cssText = style;
    return td;
}

function updateDashboard(data) {
    if (!data) return;
    const src = data.data_sources || {};
    const routerSrc = src.router || {};

    // 0. Data-source banner: say plainly when the router is not feeding us, or when data is simulated
    let banner = document.getElementById('data-source-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'data-source-banner';
        banner.style.cssText = 'padding:.5rem 1rem;font-size:.85rem;font-weight:600;text-align:center;';
        document.body.prepend(banner);
    }
    if (routerSrc.simulated) {
        banner.textContent = 'SIMULATED router data (mikrotik_connection.simulate_if_offline = true) - not measurements';
        banner.style.background = 'rgba(255,80,80,.25)';
        banner.hidden = false;
    } else if (routerSrc.status && routerSrc.status !== 'LIVE') {
        banner.textContent = `Router data ${routerSrc.status}: ${routerSrc.error || ''}`;
        banner.style.background = 'rgba(255,190,60,.22)';
        banner.hidden = false;
    } else {
        banner.hidden = true;
    }

    // 1. Top Metrics
    const stab = data.stability || {};
    const scoreEl = document.getElementById('val-stability-score');
    const statusEl = document.getElementById('val-stability-status');
    if (scoreEl && statusEl) {
        const s = stab.stability_score;
        scoreEl.textContent = num(s);
        statusEl.textContent = `${stab.status_label || 'NO_DATA'} (${num(stab.packet_loss_avg_pct)}% Loss)`;
        const color = typeof s !== 'number' ? 'var(--text-dim)'
            : s >= 90 ? 'var(--accent-emerald)' : s >= 70 ? 'var(--accent-amber)' : 'var(--accent-rose)';
        scoreEl.style.color = color;
        statusEl.style.color = color;
    }
    setText('val-total-rx', num(data.total_rx_mbps));
    setText('val-total-tx', num(data.total_tx_mbps));
    setText('val-latency', num(stab.latency_avg_ms));
    setText('val-jitter', `Jitter: ${num(stab.jitter_avg_ms)} ms | Loss: ${num(stab.packet_loss_avg_pct)}%`);

    // 2. Hardware / Mains Status (null = no ESP32 sentinel report yet)
    const hw = data.hardware_sentinel || {};
    const mainsPill = document.getElementById('mains-power-pill');
    if (mainsPill) {
        if (hw.mains_power_ok === true) {
            mainsPill.className = 'status-pill';
            setText('mains-status-text', 'MAINS 230V AC OK');
        } else if (hw.mains_power_ok === false) {
            mainsPill.className = 'status-pill danger';
            setText('mains-status-text', 'GRID POWER LOSS (UPS ACTIVE)');
        } else {
            mainsPill.className = 'status-pill';
            setText('mains-status-text', 'MAINS: NO SENTINEL REPORT');
        }
    }

    // 3. WAN Interfaces
    const wans = data.wan_interfaces || [];
    for (const id of ['wan1', 'wan2']) {
        const w = wans.find(x => x.id === id) || {};
        setText(`${id}-rx`, `${num(w.rx_mbps)} Mbps`);
        setText(`${id}-tx`, `${num(w.tx_mbps)} Mbps`);
        const b = document.getElementById(`${id}-badge`);
        if (b) {
            b.textContent = w.status || 'UNKNOWN';
            b.className = `wan-badge ${w.status === 'ONLINE' ? 'badge-online' : 'badge-offline'}`;
        }
    }

    // 4. Chart: only measured points (a gap, not a fake zero, when the router is silent)
    if (trafficChart) {
        rxDataPoints.push(typeof data.total_rx_mbps === 'number' ? data.total_rx_mbps : null);
        txDataPoints.push(typeof data.total_tx_mbps === 'number' ? data.total_tx_mbps : null);
        if (rxDataPoints.length > maxChartPoints) {
            rxDataPoints.shift();
            txDataPoints.shift();
        }
        trafficChart.update('none');
    }

    // 5. Floor Breakdown
    const floorsContainer = document.getElementById('floor-list-container');
    if (floorsContainer && data.floors) {
        floorsContainer.replaceChildren();
        if (!data.floors.length) {
            const p = document.createElement('p');
            p.style.color = 'var(--text-dim)';
            p.textContent = 'No floors mapped yet - set floor_interfaces in config.yaml.';
            floorsContainer.appendChild(p);
        }
        for (const f of data.floors) {
            const share = typeof f.bandwidth_share_pct === 'number' ? f.bandwidth_share_pct : 0;
            const row = document.createElement('div');
            row.className = 'floor-row';
            const info = document.createElement('div');
            info.className = 'floor-info';
            const name = document.createElement('span');
            name.style.fontWeight = '600';
            const hosts = typeof f.active_devices === 'number' ? `${f.active_devices} hosts` :
                (f.interfaces && f.interfaces.length ? 'hosts: —' : 'not mapped');
            name.textContent = `${f.floor_name} (${hosts})`;
            const rate = document.createElement('span');
            rate.style.cssText = 'font-family: var(--font-mono); color: var(--accent-cyan);';
            rate.textContent = `${num(f.rx_mbps)} Mbps (${num(f.bandwidth_share_pct)}%)`;
            info.append(name, rate);
            const bg = document.createElement('div');
            bg.className = 'floor-bar-bg';
            const fill = document.createElement('div');
            fill.className = 'floor-bar-fill';
            fill.style.width = `${Math.min(100, share)}%`;
            bg.appendChild(fill);
            row.append(info, bg);
            floorsContainer.appendChild(row);
        }
    }

    // 6. Load shedding (EskomSePush) - "unavailable" without an API key
    const ls = data.loadshedding || {};
    const stageBadge = document.getElementById('ls-stage-badge');
    if (stageBadge) {
        if (!ls.available) {
            stageBadge.textContent = 'STAGE —';
            stageBadge.className = 'stage-badge stage-0';
            stageBadge.title = ls.reason || 'load-shedding data unavailable';
        } else {
            stageBadge.textContent = `STAGE ${ls.current_stage === null || ls.current_stage === undefined ? '?' : ls.current_stage}`;
            stageBadge.className = ls.current_stage === 0 ? 'stage-badge stage-0' : 'stage-badge';
            stageBadge.title = '';
        }
    }
    if (ls.area_name) setText('ls-area-label', ls.area_name);
    setText('ls-schedule-label', ls.next_event
        ? `Next slot: ${ls.next_event.start || '?'} - ${ls.next_event.end || '?'} ${ls.next_event.note || ''}`
        : (ls.available ? 'No upcoming slot' : (ls.reason || 'Load-shedding data unavailable')));
    if (ls.correlation_analysis) {
        const corr = ls.correlation_analysis;
        const box = document.getElementById('correlation-box-content');
        if (box) {
            const head = document.createElement('b');
            head.textContent = `Correlation Engine [${corr.severity}]: `;
            const action = document.createElement('span');
            action.style.cssText = 'color: var(--accent-cyan); font-size: 0.8rem;';
            action.textContent = `Action: ${corr.action}`;
            box.replaceChildren(head, document.createTextNode(corr.message || ''), document.createElement('br'), action);
        }
    }

    // 7. Security Threats (from the router's firewall log)
    const threatBody = document.getElementById('threat-table-body');
    if (threatBody && data.recent_threats) {
        threatBody.replaceChildren();
        if (!data.recent_threats.length) {
            const tr = document.createElement('tr');
            const td = cell(routerSrc.status === 'LIVE'
                ? 'No blocked-port drops in the router log (rules need log=yes).'
                : 'No router log available.', 'color: var(--text-dim);');
            td.colSpan = 5;
            tr.appendChild(td);
            threatBody.appendChild(tr);
        }
        for (const t of data.recent_threats) {
            const tr = document.createElement('tr');
            const d = t.timestamp ? new Date(t.timestamp) : null;
            const timeStr = d && !isNaN(d) ? d.toLocaleTimeString() : (t.timestamp || '--:--');
            const sev = document.createElement('td');
            const badge = document.createElement('span');
            badge.className = `threat-badge ${t.severity === 'CRITICAL' || t.severity === 'HIGH' ? 'threat-high' : 'threat-med'}`;
            badge.textContent = t.severity;
            sev.appendChild(badge);
            tr.append(cell(timeStr, 'color: var(--text-dim);'),
                cell(t.attacker_ip, 'color: var(--accent-cyan); font-weight: 700;'),
                cell(`${t.blocked_service} (${t.target_port})`), sev,
                cell(t.action_taken, 'color: var(--accent-rose);'));
            threatBody.appendChild(tr);
        }
    }
}

// Trigger Manual Speedtest
async function triggerSpeedtest() {
    const btn = document.getElementById('btn-run-speedtest');
    const label = document.getElementById('btn-speedtest-label');
    btn.disabled = true;
    label.innerText = 'Testing Line...';

    try {
        const res = await fetch('/api/telemetry/speedtest/run', { method: 'POST' });
        if (res.status === 401) { label.innerText = 'Token required'; return; }
        const data = await res.json();
        const fmt = (v, d = 1) => (typeof v === 'number' ? v.toFixed(d) : '—');

        document.getElementById('st-dl-val').innerText = fmt(data.download_mbps);
        document.getElementById('st-ul-val').innerText = fmt(data.upload_mbps);
        document.getElementById('st-ping-val').innerText = `${fmt(data.ping_ms)} ms`;
        document.getElementById('speed-bar-fill').style.width = `${data.speed_bar_level || 0}%`;
        // Never let a synthetic number pass as a measurement.
        const tag = document.getElementById('st-source') || (() => {
            const s = document.createElement('span'); s.id = 'st-source';
            s.style.cssText = 'margin-left:.5rem;font-size:.75rem;padding:.1rem .4rem;border-radius:4px;';
            document.getElementById('st-ping-val').parentElement.appendChild(s); return s;
        })();
        tag.innerText = data.simulated ? 'SIMULATED' : `measured · ${data.source}`;
        tag.style.background = data.simulated ? 'rgba(255,80,80,.18)' : 'rgba(80,220,120,.18)';
        tag.title = data.note || data.server_location || '';
    } catch (e) {
        console.error('Speedtest error:', e);
    } finally {
        btn.disabled = false;
        label.innerText = 'Run Speedtest';
    }
}

// Trigger MCP Tool from UI
async function callMCPTool(toolName) {
    const box = document.getElementById('mcp-output-box');
    box.innerText = `// Executing MCP tool: ${toolName}()...`;

    try {
        const res = await fetch('/api/mcp/rpc', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0',
                id: Date.now(),
                method: 'tools/call',
                params: {
                    name: toolName,
                    arguments: { detailed: true }
                }
            })
        });
        const result = await res.json();
        if (result.result && result.result.content) {
            box.innerText = result.result.content[0].text;
        } else {
            box.innerText = JSON.stringify(result, null, 2);
        }
    } catch (e) {
        box.innerText = `// Error calling MCP tool: ${e.message}`;
    }
}

// Start on page load
window.addEventListener('DOMContentLoaded', () => {
    initChart();
    connectWebSocket();
});
