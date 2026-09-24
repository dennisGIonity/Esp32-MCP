from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

class WANInterfaceStatus(BaseModel):
    id: str
    name: str
    interface: str
    ip_pool: str
    gateway_ip: str
    status: str = "ONLINE" # ONLINE, DEGRADED, OFFLINE
    rx_mbps: float = 0.0
    tx_mbps: float = 0.0
    latency_ms: float = 0.0
    packet_loss_pct: float = 0.0
    jitter_ms: float = 0.0
    priority: int = 1
    committed_bandwidth_mbps: float = 200.0

class FloorTrafficBreakdown(BaseModel):
    floor_id: str
    floor_name: str
    active_devices: int = 0
    rx_mbps: float = 0.0
    tx_mbps: float = 0.0
    bandwidth_share_pct: float = 0.0
    primary_vlans: List[str] = Field(default_factory=list)

class StabilityMetrics(BaseModel):
    stability_score: float = 98.5 # 0.0 - 100.0 %
    status_label: str = "EXCELLENT" # EXCELLENT, STABLE, DEGRADED, CRITICAL
    packet_loss_avg_pct: float = 0.1
    jitter_avg_ms: float = 2.4
    latency_avg_ms: float = 12.8
    uptime_seconds: int = 864000
    dns_lookup_time_ms: float = 14.2
    failover_active: bool = False
    active_wan_id: str = "wan1"

class LoadSheddingInfo(BaseModel):
    area_id: str = "city-of-johannesburg-block-3"
    area_name: str = "Sandton / Kelvin Drive"
    current_stage: int = 0 # 0 = No Load Shedding, 1-8
    is_currently_loadshedding: bool = False
    next_event_start: Optional[str] = None
    next_event_end: Optional[str] = None
    power_grid_health: str = "NORMAL" # NORMAL, ALERT, CRITICAL_OUTAGE
    correlation_reason: str = "Power grid operational. Fibre link normal."
    last_updated: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class SecurityAlert(BaseModel):
    alert_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = "MEDIUM" # LOW, MEDIUM, HIGH, CRITICAL
    blocked_service: str # Telnet, FTP, SSH, WWW, API
    target_port: int
    attacker_ip: str
    protocol: str = "TCP"
    action_taken: str = "BLOCKED_AND_LOGGED"
    message: str

class SpeedtestResult(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    download_mbps: float = 185.4
    upload_mbps: float = 178.2
    ping_ms: float = 8.5
    jitter_ms: float = 1.8
    server_location: str = "Johannesburg, ZA"
    isp_detected: str = "Liquid Intelligent Technologies"
    speed_bar_level: int = 88 # 0 - 100 %

class LiveNetworkTelemetry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    site_id: str = "kelvin-drive-site-01"
    site_name: str = "Kelvin Drive HQ"
    router_hostname: str = "Dynamic@KelvinDrive"
    router_model: str = "MikroTik CRS326-24G-2S+"
    total_rx_mbps: float = 0.0
    total_tx_mbps: float = 0.0
    peak_throughput_capacity_mbps: float = 400.0
    wan_interfaces: List[WANInterfaceStatus] = Field(default_factory=list)
    floors: List[FloorTrafficBreakdown] = Field(default_factory=list)
    stability: StabilityMetrics = Field(default_factory=StabilityMetrics)
    loadshedding: LoadSheddingInfo = Field(default_factory=LoadSheddingInfo)
    speedtest: SpeedtestResult = Field(default_factory=SpeedtestResult)
    recent_threats: List[SecurityAlert] = Field(default_factory=list)
    hardware_sentinel_connected: bool = True
    mains_ac_power_ok: bool = True
