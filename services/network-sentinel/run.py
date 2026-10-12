import os
import sys
import uvicorn
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is on PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

if __name__ == "__main__":
    port = int(os.getenv("SENTINEL_PORT", 8000))
    host = os.getenv("SENTINEL_HOST", "0.0.0.0")

    print("==================================================================")
    print("   [+] Ionity_ESP_Reporter | AEDI Systems Core Initializing...   ")
    print(f"   [>] Ionity Dashboard: http://localhost:{port}/                 ")
    print(f"   [>] MCP JSON-RPC:     http://localhost:{port}/api/mcp/rpc      ")
    print(f"   [>] Live WS Stream:    ws://localhost:{port}/ws/telemetry       ")
    print("==================================================================")

    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
