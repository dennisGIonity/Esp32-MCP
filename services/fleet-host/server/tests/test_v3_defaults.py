# AEDI - IONITY GLOBAL | ESP32-MCP fleet host | Policy 986 AED
"""v3 (PLAN-v3-ESP-CENTRIC): the ESP node is the resolver, so the host DNS resolver
and host LAN sweep are lab-only fallbacks and must default OFF. The POC/tester kits
switch them on explicitly (tools/packaging)."""
from app.config import Settings


def test_host_resolver_and_sweep_default_off(monkeypatch):
    for k in ("IONITY_DNS_ENABLED", "IONITY_LAN_SWEEP_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.dns_enabled is False
    assert s.lan_sweep_enabled is False


def test_kits_enable_the_lab_fallbacks():
    from pathlib import Path
    root = Path(__file__).resolve().parents[4]
    tester = (root / "tools/packaging/testers/tools/tester.env").read_text(encoding="utf-8")
    poc = (root / "tools/packaging/poc/tools/poc.ps1").read_text(encoding="utf-8-sig")
    assert "IONITY_DNS_ENABLED=true" in tester and "IONITY_LAN_SWEEP_ENABLED=true" in tester
    assert "SetEnv 'IONITY_DNS_ENABLED' 'true'" in poc and "SetEnv 'IONITY_LAN_SWEEP_ENABLED' 'true'" in poc
