"""
AEDI - IONITY GLOBAL | Network sentinel | core/models.py   Policy 986 AED
Schemas of the live telemetry. Defaults are null / UNKNOWN: a value nobody measured is not a number.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone

class WANInterfaceStatus(BaseModel):
    id: str
    name: str
    interface: str
    ip_pool: str
    gateway_ip: str
    status: str = "UNKNOWN" # ONLINE, OFFLINE, UNKNOWN
    rx_mbps: Optional[float] = None
    tx_mbps: Optional[float] = None
    latency_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None
    jitter_ms: Optional[float] = None
    priority: int = 1
    committed_bandwidth_mbps: Optional[float] = None

class FloorTrafficBreakdown(BaseModel):
    floor_id: str
    floor_name: str
    active_devices: Optional[int] = None
    rx_mbps: Optional[float] = None
    tx_mbps: Optional[float] = None
    bandwidth_share_pct: Optional[float] = None
    primary_vlans: List[str] = Field(default_factory=list)

class StabilityMetrics(BaseModel):
    stability_score: Optional[float] = None # 0.0 - 100.0 %, null = no probe data
    status_label: str = "NO_DATA" # EXCELLENT, STABLE, DEGRADED, CRITICAL, CRITICAL_OUTAGE, NO_DATA
    packet_loss_avg_pct: Optional[float] = None
    jitter_avg_ms: Optional[float] = None
    latency_avg_ms: Optional[float] = None
    router_uptime_seconds: Optional[int] = None
    sentinel_uptime_seconds: Optional[int] = None
    failover_active: bool = False
    active_wan_id: Optional[str] = None

class LoadSheddingInfo(BaseModel):
    area_id: str = "city-of-johannesburg-block-3"
    area_name: str = "Sandton / Kelvin Drive"
    current_stage: Optional[int] = None # 0 = none, 1-8, null = unknown
    is_currently_loadshedding: Optional[bool] = None
    next_event_start: Optional[str] = None
    next_event_end: Optional[str] = None
    power_grid_health: str = "UNKNOWN" # NORMAL, LOADSHEDDING_ACTIVE, UNKNOWN
    correlation_reason: Optional[str] = None
    last_updated: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class SecurityAlert(BaseModel):
    alert_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = "MEDIUM" # LOW, MEDIUM, HIGH, CRITICAL
    blocked_service: str # Telnet, FTP, SSH, WWW, API
    target_port: int
    attacker_ip: str
    protocol: str = "TCP"
    action_taken: str = "DROPPED_BY_ROUTER"
    message: str

class SpeedtestResult(BaseModel):
    timestamp: Optional[str] = None
    source: str = "none"
    simulated: bool = False
    download_mbps: Optional[float] = None
    upload_mbps: Optional[float] = None
    ping_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    server_location: Optional[str] = None
    isp_detected: Optional[str] = None
    speed_bar_level: Optional[int] = None # 0 - 100 %

class LiveNetworkTelemetry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    site_id: str = "kelvin-drive-site-01"
    site_name: str = "Kelvin Drive HQ"
    router_hostname: str = "Dynamic@KelvinDrive"
    router_model: str = "MikroTik CRS326-24G-2S+"
    total_rx_mbps: Optional[float] = None
    total_tx_mbps: Optional[float] = None
    peak_throughput_capacity_mbps: Optional[float] = None
    wan_interfaces: List[WANInterfaceStatus] = Field(default_factory=list)
    floors: List[FloorTrafficBreakdown] = Field(default_factory=list)
    stability: StabilityMetrics = Field(default_factory=StabilityMetrics)
    loadshedding: LoadSheddingInfo = Field(default_factory=LoadSheddingInfo)
    speedtest: SpeedtestResult = Field(default_factory=SpeedtestResult)
    recent_threats: List[SecurityAlert] = Field(default_factory=list)
    hardware_sentinel_connected: bool = False
    mains_ac_power_ok: Optional[bool] = None
