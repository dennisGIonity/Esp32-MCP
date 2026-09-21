"""
Storage interface. SQLite today, TimescaleDB later -- nothing above this
layer knows which one is running.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import TelemetryIn, StatusIn, Alert


class Store(ABC):
    @abstractmethod
    async def init(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def upsert_device(self, t: TelemetryIn | StatusIn) -> None: ...

    @abstractmethod
    async def insert_telemetry(self, t: TelemetryIn) -> None: ...

    @abstractmethod
    async def query_telemetry(
        self,
        device_id: str | None = None,
        site: str | None = None,
        group: str | None = None,
        metric: str | None = None,
        since_s: float | None = None,
        until_s: float | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def aggregate_metric(
        self,
        metric: str,
        since_s: float,
        site: str | None = None,
        group: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def list_devices(
        self, site: str | None = None, group: str | None = None
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def raise_alert(self, alert: Alert) -> int: ...

    @abstractmethod
    async def clear_alert(self, device_id: str, code: str, at: float) -> None: ...

    @abstractmethod
    async def list_alerts(
        self, device_id: str | None = None, open_only: bool = True, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def log_command(self, device_id: str, action: str, payload: str) -> int: ...

    @abstractmethod
    async def prune(self, older_than_s: float) -> int: ...
