#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Start mcpo (OpenAPI/REST front-end for the ESP32-MCP fleet tools)
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
Owner: github.com/Ionity-Global-Pty-Ltd | www.ionity.today | ai@ionity.today

Reads IONITY_MCPO_API_KEY from the environment or .env and hands it to mcpo
in-process, so the key never appears on a process command line (Task Manager,
Get-CimInstance Win32_Process, ps). --strict-auth protects the docs page too.
Clients send:  Authorization: Bearer <key>

Run with the mcpo venv:   .venv-mcpo\\Scripts\\python.exe scripts\\run_mcpo.py
Started automatically by scripts\\start_lab.ps1 (step 4).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env_value(name: str) -> str:
    v = os.environ.get(name, "")
    if not v and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if s.startswith(name) and "=" in s and s.split("=", 1)[0].strip() == name:
                v = s.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return v


def main() -> None:
    key = env_value("IONITY_MCPO_API_KEY")
    port = env_value("IONITY_MCPO_PORT") or "8000"
    args = ["mcpo", "--host", "0.0.0.0", "--port", port, "--name", "Ionity-ESP32-Fleet-mcpo"]
    if key:
        args += ["--api-key", key, "--strict-auth"]  # in-process only, never on a command line
    else:
        print("WARNING: IONITY_MCPO_API_KEY is not set - mcpo is open to the whole LAN", file=sys.stderr)
    args += ["--", str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "server" / "mcp_stdio_proxy.py")]
    sys.argv = args
    from mcpo import app
    app()


if __name__ == "__main__":
    main()
