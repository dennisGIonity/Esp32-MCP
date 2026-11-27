# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | component: netobserve
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
# NetObserve (PLAN-v3): the LAN DNS forwarder, watchlist flagging, LAN device
# discovery (ARP / DHCP / mDNS) and the NetObserve EWP tools 0x40-0x47.
# Everything except EspNetPlatform.cpp is portable and host-tested (test/host).

idf_component_register(
    SRCS "src/DnsWire.cpp" "src/Watchlist.cpp" "src/DnsCache.cpp" "src/QueryLog.cpp" "src/ClientTable.cpp"
         "src/Discovery.cpp" "src/NetObserve.cpp" "src/NetTools.cpp" "src/EspNetPlatform.cpp"
    INCLUDE_DIRS "include"
    REQUIRES ewp mcp_server tools lwip esp_netif
    PRIV_REQUIRES nvs_flash log)

if(IONITY_STRICT_WARNINGS)
    target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow)
endif()
