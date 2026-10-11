# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | component: provisioning
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
# ionity-prov/2 over the serial console: protocol (portable, host-tested),
# NVS-backed configuration with fw 2.x migration, console + log hook.
#
# cJSON: part of ESP-IDF 5.x ("json" component); from ESP-IDF 6.0 it is the
# managed component espressif/cjson (see main/idf_component.yml).

if(IDF_VERSION_MAJOR GREATER_EQUAL 6)
    set(json_component espressif__cjson)
else()
    set(json_component json)
endif()

idf_component_register(
    SRCS "src/Json.cpp" "src/Validate.cpp" "src/Config.cpp" "src/ProvHandler.cpp"
         "src/EspNvs.cpp" "src/Console.cpp"
    INCLUDE_DIRS "include"
    REQUIRES ewp mcp_server nvs_flash
    PRIV_REQUIRES ${json_component} esp_driver_uart esp_driver_usb_serial_jtag esp_timer log vfs)

if(IONITY_STRICT_WARNINGS)
    target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow)
endif()
