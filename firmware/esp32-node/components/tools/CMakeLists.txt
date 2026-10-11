# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | component: tools
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
# Edge tools (read_telemetry, get_device_info, identify), the TELEMETRY /
# HELLO / STATUS encoders (portable, host-tested) and their ESP-IDF probes.

idf_component_register(
    SRCS "src/Telemetry.cpp" "src/Identity.cpp" "src/EdgeTools.cpp" "src/EspPlatform.cpp"
    INCLUDE_DIRS "include"
    REQUIRES ewp mcp_server esp_timer freertos
    PRIV_REQUIRES esp_driver_gpio esp_driver_tsens esp_adc esp_app_format esp_hw_support esp_system
                  spi_flash heap log esp_psram)

if(IONITY_STRICT_WARNINGS)
    target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow)
endif()
