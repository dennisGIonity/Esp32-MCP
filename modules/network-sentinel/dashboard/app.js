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

// Update DOM with Telemetry Data
function updateDashboard(data) {
    if (!data) return;

    // 1. Top Metrics
    const stab = data.stability || {};
    const scoreEl = document.getElementById('val-stability-score');
    const statusEl = document.getElementById('val-stability-status');
    if (scoreEl && stab.stability_score !== undefined) {
        scoreEl.innerText = stab.stability_score.toFixed(1);
        statusEl.innerText = `${stab.status_label} (${(stab.packet_loss_avg_pct || 0).toFixed(1)}% Loss)`;
        
        // Color coding
        if (stab.stability_score >= 90) {
            scoreEl.style.color = 'var(--accent-emerald)';
            statusEl.style.color = 'var(--accent-emerald)';
        } else if (stab.stability_score >= 70) {
            scoreEl.style.color = 'var(--accent-amber)';
            statusEl.style.color = 'var(--accent-amber)';
        } else {
            scoreEl.style.color = 'var(--accent-rose)';
            statusEl.style.color = 'var(--accent-rose)';
        }
    }

    document.getElementById('val-total-rx').innerText = (data.total_rx_mbps || 0).toFixed(1);
    document.getElementById('val-total-tx').innerText = (data.total_tx_mbps || 0).toFixed(1);
    
    if (stab.latency_avg_ms) {
        document.getElementById('val-latency').innerText = stab.latency_avg_ms.toFixed(1);
    }
    if (stab.jitter_avg_ms) {
        document.getElementById('val-jitter').innerText = `Jitter: ${stab.jitter_avg_ms.toFixed(1)} ms | Loss: ${(stab.packet_loss_avg_pct || 0).toFixed(1)}%`;
    }

    // 2. Hardware / Mains Status
    const hw = data.hardware_sentinel || {};
    const mainsPill = document.getElementById('mains-power-pill');
    const mainsText = document.getElementById('mains-status-text');
    if (mainsPill && hw.mains_power_ok !== undefined) {
        if (hw.mains_power_ok) {
            mainsPill.className = 'status-pill';
            mainsText.innerText = 'MAINS 230V AC OK';
        } else {
            mainsPill.className = 'status-pill danger';
            mainsText.innerText = 'GRID POWER LOSS (UPS ACTIVE)';
        }
    }

    // 3. WAN Interfaces
    const wans = data.wan_interfaces || [];
    const wan1 = wans.find(w => w.id === 'wan1') || {};
    const wan2 = wans.find(w => w.id === 'wan2') || {};

    if (wan1.rx_mbps !== undefined) {
        document.getElementById('wan1-rx').innerText = `${wan1.rx_mbps.toFixed(1)} Mbps`;
        document.getElementById('wan1-tx').innerText = `${wan1.tx_mbps.toFixed(1)} Mbps`;
        const b1 = document.getElementById('wan1-badge');
        b1.innerText = wan1.status;
        b1.className = `wan-badge ${wan1.status === 'ONLINE' ? 'badge-online' : 'badge-offline'}`;
    }

    if (wan2.rx_mbps !== undefined) {
        document.getElementById('wan2-rx').innerText = `${wan2.rx_mbps.toFixed(1)} Mbps`;
        document.getElementById('wan2-tx').innerText = `${wan2.tx_mbps.toFixed(1)} Mbps`;
        const b2 = document.getElementById('wan2-badge');
        b2.innerText = wan2.status;
        b2.className = `wan-badge ${wan2.status === 'ONLINE' ? 'badge-online' : 'badge-offline'}`;
    }

    // 4. Update Chart
    if (trafficChart && data.total_rx_mbps !== undefined) {
        rxDataPoints.push(data.total_rx_mbps);
        txDataPoints.push(data.total_tx_mbps);
        if (rxDataPoints.length > maxChartPoints) {
            rxDataPoints.shift();
            txDataPoints.shift();
        }
        trafficChart.update('none');
    }

    // 5. Floor Breakdown
    const floorsContainer = document.getElementById('floor-list-container');
    if (floorsContainer && data.floors) {
        floorsContainer.innerHTML = '';
        data.floors.forEach(f => {
            const share = f.bandwidth_share_pct || 0;
            const row = document.createElement('div');
            row.className = 'floor-row';
            row.innerHTML = `
                <div class="floor-info">
                    <span style="font-weight: 600;">${f.floor_name} <span style="font-size: 0.75rem; color: var(--text-dim);">(${f.active_devices} hosts)</span></span>
                    <span style="font-family: var(--font-mono); color: var(--accent-cyan);">${f.rx_mbps.toFixed(1)} Mbps (${share}%)</span>
                </div>
                <div class="floor-bar-bg">
                    <div class="floor-bar-fill" style="width: ${Math.min(100, share)}%;"></div>
                </div>
            `;
            floorsContainer.appendChild(row);
        });
    }

    // 6. Eskom Load Shedding
    const ls = data.loadshedding || {};
    const stageBadge = document.getElementById('ls-stage-badge');
    if (stageBadge && ls.current_stage !== undefined) {
        stageBadge.innerText = `STAGE ${ls.current_stage}`;
        stageBadge.className = ls.current_stage === 0 ? 'stage-badge stage-0' : 'stage-badge';
    }
    if (ls.area_name) {
        document.getElementById('ls-area-label').innerText = ls.area_name;
    }
    if (ls.next_event) {
        document.getElementById('ls-schedule-label').innerText = `Scheduled Slot: ${ls.next_event.start || ''} (${ls.next_event.stage || 'Stage 1'})`;
    }
    if (ls.correlation_analysis) {
        const corr = ls.correlation_analysis;
        document.getElementById('correlation-box-content').innerHTML = `
            <b>Correlation Engine [${corr.severity}]:</b> ${corr.message} 
            <br><span style="color: var(--accent-cyan); font-size: 0.8rem;">Action: ${corr.action}</span>
        `;
    }

    // 7. Security Threats
    const threatBody = document.getElementById('threat-table-body');
    if (threatBody && data.recent_threats) {
        threatBody.innerHTML = '';
        data.recent_threats.forEach(t => {
            const tr = document.createElement('tr');
            const timeStr = t.timestamp ? new Date(t.timestamp).toLocaleTimeString() : '--:--';
            const sevClass = t.severity === 'CRITICAL' || t.severity === 'HIGH' ? 'threat-high' : 'threat-med';
            tr.innerHTML = `
                <td style="color: var(--text-dim);">${timeStr}</td>
                <td><b style="color: var(--accent-cyan);">${t.attacker_ip}</b></td>
                <td>${t.blocked_service} (${t.target_port})</td>
                <td><span class="threat-badge ${sevClass}">${t.severity}</span></td>
                <td style="color: var(--accent-rose);">${t.action_taken}</td>
            `;
            threatBody.appendChild(tr);
        });
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
        const data = await res.json();
        
        document.getElementById('st-dl-val').innerText = data.download_mbps.toFixed(1);
        document.getElementById('st-ul-val').innerText = data.upload_mbps.toFixed(1);
        document.getElementById('st-ping-val').innerText = `${data.ping_ms.toFixed(1)} ms`;
        document.getElementById('speed-bar-fill').style.width = `${data.speed_bar_level}%`;
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
