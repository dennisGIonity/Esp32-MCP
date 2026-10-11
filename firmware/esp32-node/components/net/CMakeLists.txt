# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | component: net
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
# Wi-Fi station manager and the EWP/1 MQTT 5 link (esp-mqtt) with net_task,
# the single publisher. esp-mqtt is part of ESP-IDF 5.x ("mqtt"); from
# ESP-IDF 6.0 it is the managed component espressif/mqtt (main/idf_component.yml).

if(IDF_VERSION_MAJOR GREATER_EQUAL 6)
    set(mqtt_component espressif__mqtt)
else()
    set(mqtt_component mqtt)
endif()

idf_component_register(
    SRCS "src/WifiManager.cpp" "src/MqttLink.cpp"
    INCLUDE_DIRS "include"
    REQUIRES ewp mcp_server tools ${mqtt_component} esp_wifi esp_netif esp_event esp_timer freertos
    PRIV_REQUIRES mbedtls lwip log)

if(IONITY_STRICT_WARNINGS)
    target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow)
endif()
