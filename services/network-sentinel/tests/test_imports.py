"""Every module must import: a NameError at import time took the whole
sentinel service down (and this CI job with it) in 2026-09."""
import importlib

import pytest


@pytest.mark.parametrize("mod", [
    "api.main", "api.routes", "core.loadshedding", "core.mikrotik_collector",
    "core.models", "core.probe_engine", "core.security_analyzer",
    "core.speedtest_engine", "core.traffic_analyzer", "mcp.mcp_server",
    "mcp.tools_and_resources",
])
def test_module_imports(mod):
    importlib.import_module(mod)
