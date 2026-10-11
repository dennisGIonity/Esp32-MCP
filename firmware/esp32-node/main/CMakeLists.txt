# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | component: main
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
# The application: boot order, task topology and the glue between components.
# PlatformIO builds this directory as its src_dir (platformio.ini).

idf_component_register(
    SRCS "main.cpp" "NodeApp.cpp"
    INCLUDE_DIRS "."
    REQUIRES ewp mcp_server tools net provisioning
    # espcoredump is listed explicitly: set(COMPONENTS main) trims the build to
    # main's dependency graph, and without it the CONFIG_ESP_COREDUMP_* lines in
    # sdkconfig.defaults are silently ignored (no core dump on a crash).
    PRIV_REQUIRES nvs_flash esp_event esp_netif esp_timer esp_system mbedtls log espcoredump)

if(IONITY_STRICT_WARNINGS)
    target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow)
endif()
