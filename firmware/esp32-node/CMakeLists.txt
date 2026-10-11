# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 (ESP-IDF project)
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
#   idf.py set-target esp32s3 && idf.py build          (sdkconfig.defaults + .esp32s3 + console fragment)
#   python release.py                                   (all four variants + manifest)
#   pio run -e s3_usb                                    (PlatformIO, framework = espidf)
#
# The firmware version is version.txt (esp_app_desc.version, HELLO.fw.version).
cmake_minimum_required(VERSION 3.16)
# Build only main and what it requires (not every IDF component): faster, smaller.
set(COMPONENTS main)
# PlatformIO injects a "__pio_env" component into its ESP-IDF copy; keep it in the build.
# PlatformIO's SCons build re-applies component compile options to every IDF
# source file, so our -Werror/-Wshadow policy is enforced by idf.py and the host
# tests (test/run.sh) and switched off there.
if(EXISTS "$ENV{IDF_PATH}/components/__pio_env")
    list(APPEND COMPONENTS __pio_env)
    set(IONITY_STRICT_WARNINGS OFF CACHE BOOL "-Werror -Wshadow on our components" FORCE)
else()
    set(IONITY_STRICT_WARNINGS ON CACHE BOOL "-Werror -Wshadow on our components" FORCE)
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(ionity-esp32-node)
