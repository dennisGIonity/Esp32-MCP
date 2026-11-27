import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import aiohttp

logger = logging.getLogger("sentinel.loadshedding")

class LoadSheddingEngine:
    """
    South African load-shedding status from the EskomSePush API (ESKOM_API_KEY)
    and correlation of link problems with power. Without a key - or when the API
    fails - the status is reported as unavailable (current_stage null), never
    guessed: there is no built-in stage.
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
        self._cached_data: Optional[Dict[str, Any]] = None
        self._last_fetch_time: float = 0.0
        self.last_error: Optional[str] = None

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
                        self.last_error = f"EskomSePush HTTP {resp.status}"
            except Exception as e:
                self.last_error = f"EskomSePush: {type(e).__name__}: {e}"[:200]
                logger.warning(self.last_error)
            reason = self.last_error or "EskomSePush did not answer"
        else:
            reason = "set ESKOM_API_KEY (EskomSePush) to see the area's load-shedding status"

        data = self._unavailable(reason)
        self._cached_data = data
        self._last_fetch_time = now - self.cache_ttl + 60     # retry an unavailable API after a minute
        return data

    def _parse_api_response(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        events = raw.get("events", [])
        info = raw.get("info", {}) or {}
        area_name = info.get("name") or self.area_name     # EskomSePush reports the area's own name

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
                    m = re.search(r"stage\s*(\d+)", str(first_event.get("note", "")), re.I)
                    current_stage = int(m.group(1)) if m else None   # active, stage not stated
            except (TypeError, ValueError):
                pass

        return {
            "available": True,
            "source": "eskomsepush",
            "area_id": self.area_id,
            "area_name": area_name,
            "current_stage": current_stage,
            "is_currently_loadshedding": is_active,
            "next_event": next_event,
            "power_grid_health": "LOADSHEDDING_ACTIVE" if is_active else "NORMAL",
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

    def _unavailable(self, reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "source": "none",
            "reason": reason,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "current_stage": None,
            "is_currently_loadshedding": None,
            "next_event": None,
            "power_grid_health": "UNKNOWN",
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

    def correlate_link_issue(self, wan_status: str, packet_loss_pct: Optional[float], mains_power_ok: bool) -> Dict[str, Any]:
        """
        Correlates network stability events with power grid status.
        Distinguishes ISP fibre cuts from municipal power outages.
        """
        known = bool(self._cached_data and self._cached_data.get("available"))
        ls_active = bool(known and self._cached_data.get("is_currently_loadshedding"))
        stage = self._cached_data.get("current_stage") if known else None

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
            elif not known:
                return {
                    "root_cause": "WAN_DOWN_CAUSE_UNKNOWN",
                    "severity": "CRITICAL",
                    "message": "WAN is down and mains power is reported normal; load-shedding status is unknown "
                               "(no EskomSePush data), so an area outage cannot be ruled out.",
                    "action": "Check the area's load-shedding schedule, then escalate to the ISP NOC."
                }
            else:
                return {
                    "root_cause": "ISP_FIBRE_PHYSICAL_BREAK",
                    "severity": "CRITICAL",
                    "message": "Mains power normal and no load shedding active. WAN is down due to physical Liquid Fibre fault.",
                    "action": "Escalate priority ticket to Liquid Intelligent Technologies NOC."
                }

        if wan_status == "UNKNOWN":
            return {
                "root_cause": "NO_ROUTER_DATA",
                "severity": "INFO",
                "message": "WAN state unknown: the router's REST API is not configured or not reachable.",
                "action": "Configure MIKROTIK_HOST / MIKROTIK_USER / MIKROTIK_PASSWORD for a read-only REST user."
            }

        if packet_loss_pct is not None and packet_loss_pct > 15.0:
            return {
                "root_cause": "ISP_PEERING_OR_CONGESTION",
                "severity": "WARNING",
                "message": f"Elevated packet loss ({packet_loss_pct}%). Upstream routing latency detected.",
                "action": "Monitor WAN 1 / WAN 2 failover routing table."
            }

        return {
            "root_cause": "ALL_SYSTEMS_OPTIMAL",
            "severity": "NORMAL",
            "message": "Mains power normal and the WAN links are up." + ("" if known else
                       " (load-shedding status unknown - no EskomSePush data)"),
            "action": "No action required."
        }
