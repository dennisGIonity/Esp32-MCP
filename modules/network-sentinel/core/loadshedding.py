import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
import aiohttp

logger = logging.getLogger("sentinel.loadshedding")

class LoadSheddingEngine:
    """
    South African Eskom Load Shedding intelligence & power grid correlation engine.
    Integrates with EskomSePush API for Sandton / Kelvin Drive area.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ls_config = config.get("loadshedding", {})
        self.site_config = config.get("site", {})
        self.area_id = self.site_config.get("location", {}).get("eskom_area_id", "city-of-johannesburg-block-3")
        self.area_name = self.site_config.get("location", {}).get("eskom_area_name", "Sandton / Kelvin Drive Substation")
        
        self.api_key = os.getenv("ESKOM_API_KEY", "")
        self.base_url = self.ls_config.get("eskom_api_url", "https://developer.sepush.co.za/business/2.0")
        self.cache_ttl = self.ls_config.get("cache_ttl_seconds", 900)
        self.mock_if_no_key = self.ls_config.get("mock_if_no_key", True)

        self._cached_data: Optional[Dict[str, Any]] = None
        self._last_fetch_time: float = 0.0

        # Simulated stage state for development
        self._mock_stage = 1
        self._mock_active = False

    async def get_loadshedding_status(self) -> Dict[str, Any]:
        """
        Retrieves real-time stage status, area schedules, and grid condition.
        Uses cached data if within TTL.
        """
        now = time.time()
        if self._cached_data and (now - self._last_fetch_time) < self.cache_ttl:
            return self._cached_data

        if self.api_key and not self.api_key.startswith("your_"):
            try:
                headers = {"token": self.api_key}
                async with aiohttp.ClientSession(headers=headers) as session:
                    # Fetch area schedule
                    url = f"{self.base_url}/area?id={self.area_id}"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            parsed = self._parse_api_response(data)
                            self._cached_data = parsed
                            self._last_fetch_time = now
                            return parsed
            except Exception as e:
                logger.warning(f"Failed to fetch from EskomSePush API: {e}. Using fallback model.")

        # Fallback / Mock generator
        data = self._generate_mock_status()
        self._cached_data = data
        self._last_fetch_time = now
        return data

    def _parse_api_response(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        events = raw.get("events", [])
        schedule = raw.get("schedule", {})
        info = raw.get("info", {})
        
        current_stage = 0
        is_active = False
        next_event = None

        if events:
            first_event = events[0]
            next_event = {
                "start": first_event.get("start"),
                "end": first_event.get("end"),
                "note": first_event.get("note")
            }
            # Check if event is happening now
            try:
                start_dt = datetime.fromisoformat(first_event.get("start"))
                end_dt = datetime.fromisoformat(first_event.get("end"))
                now_dt = datetime.now(start_dt.tzinfo)
                if start_dt <= now_dt <= end_dt:
                    is_active = True
                    current_stage = int(first_event.get("note", "Stage 1").split()[-1])
            except Exception:
                pass

        return {
            "area_id": self.area_id,
            "area_name": self.area_name,
            "current_stage": current_stage,
            "is_currently_loadshedding": is_active,
            "next_event": next_event,
            "power_grid_health": "LOADSHEDDING_ACTIVE" if is_active else "NORMAL",
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

    def _generate_mock_status(self) -> Dict[str, Any]:
        now_dt = datetime.now(timezone.utc)
        # Schedule next slot 4 hours from now
        next_start = (now_dt + timedelta(hours=3, minutes=15)).strftime("%Y-%m-%dT%H:00:00+02:00")
        next_end = (now_dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:30:00+02:00")

        return {
            "area_id": self.area_id,
            "area_name": self.area_name,
            "current_stage": self._mock_stage,
            "is_currently_loadshedding": self._mock_active,
            "next_event": {
                "start": next_start,
                "end": next_end,
                "stage": f"Stage {self._mock_stage}"
            },
            "power_grid_health": "ALERT_STAGE_1" if self._mock_stage > 0 else "NORMAL",
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

    def correlate_link_issue(self, wan_status: str, packet_loss_pct: float, mains_power_ok: bool) -> Dict[str, Any]:
        """
        Correlates network stability events with power grid status.
        Distinguishes ISP fibre cuts from municipal power outages.
        """
        ls_active = self._cached_data.get("is_currently_loadshedding", False) if self._cached_data else False
        stage = self._cached_data.get("current_stage", 0) if self._cached_data else 0

        if not mains_power_ok:
            return {
                "root_cause": "ONSITE_MAINS_POWER_LOSS",
                "severity": "CRITICAL",
                "message": "Building AC mains power dropped. Generator / Inverter UPS active.",
                "action": "Ensure generator fuel and battery backup are sustaining Kelvin Drive server room."
            }
        
        if wan_status == "OFFLINE":
            if ls_active:
                return {
                    "root_cause": "AREA_LOADSHEDDING_SUBSTATION_OUTAGE",
                    "severity": "HIGH",
                    "message": f"WAN offline during active Stage {stage} load shedding in Sandton. Local fibre pop may be on battery/inverter.",
                    "action": "Awaiting substation power restoration according to Eskom timetable."
                }
            else:
                return {
                    "root_cause": "ISP_FIBRE_PHYSICAL_BREAK",
                    "severity": "CRITICAL",
                    "message": "Mains power normal and no load shedding active. WAN is down due to physical Liquid Fibre fault.",
                    "action": "Escalate priority ticket to Liquid Intelligent Technologies NOC."
                }

        if packet_loss_pct > 15.0:
            return {
                "root_cause": "ISP_PEERING_OR_CONGESTION",
                "severity": "WARNING",
                "message": f"Elevated packet loss ({packet_loss_pct}%). Upstream routing latency detected.",
                "action": "Monitor WAN 1 / WAN 2 failover routing table."
            }

        return {
            "root_cause": "ALL_SYSTEMS_OPTIMAL",
            "severity": "NORMAL",
            "message": "Power grid stable. Both Liquid Fibre uplinks operating within SLA.",
            "action": "No action required."
        }
