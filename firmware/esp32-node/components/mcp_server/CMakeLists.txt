# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | component: mcp_server
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
# MCPServer (tool registry, job pool, Core-0 -> Core-1 routing), the uplink
# frame pool and the thin RTOS abstraction they share with the host tests.

idf_component_register(
    SRCS "src/os.cpp" "src/Uplink.cpp" "src/MCPServer.cpp"
    INCLUDE_DIRS "include"
    REQUIRES ewp freertos esp_timer
    PRIV_REQUIRES esp_hw_support heap log)

if(IONITY_STRICT_WARNINGS)
    target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow)
endif()
