# ===========================================================================
# AEDI - IONITY GLOBAL | Start the fleet server (kept for compatibility)
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# The fleet server alone is no longer the whole system: commands need the
# MQTT broker and serial-only boards need the serial bridge. This now simply
# delegates to start_lab.ps1, which brings up all three in the right order.
# ===========================================================================
param([switch]$Restart)
& (Join-Path $PSScriptRoot 'start_lab.ps1') -Restart:$Restart
