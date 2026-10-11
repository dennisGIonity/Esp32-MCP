# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | host test build
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
#
# The firmware's portable code (EWP codec, MCPServer, tools, provisioning
# protocol) compiled for the build machine with the same C++ dialect as the
# device (-fno-exceptions -fno-rtti, C++17) plus sanitizers:
#
#   cmake -S test/host -B build/host -G Ninja          # ASan + UBSan (default)
#   cmake -S test/host -B build/host-tsan -G Ninja -DSANITIZER=thread
#   ninja -C build/host && build/host/fw_tests && build/host/fw_fuzz 200000
cmake_minimum_required(VERSION 3.16)
project(esp32_node_host_tests CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(FW_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
get_filename_component(FW_ROOT "${FW_ROOT}" ABSOLUTE)
set(REPO_ROOT "${FW_ROOT}/../..")
get_filename_component(REPO_ROOT "${REPO_ROOT}" ABSOLUTE)

set(EWP_PROTOCOL_DIR "${REPO_ROOT}/packages/protocol/generated")
if(DEFINED ENV{EWP_PROTOCOL_DIR})
    set(EWP_PROTOCOL_DIR "$ENV{EWP_PROTOCOL_DIR}")
endif()

# cJSON: the copy shipped inside ESP-IDF (components/json/cJSON), so the host
# prov tests parse exactly like the device. Set IDF_PATH or -DCJSON_DIR=...
if(NOT CJSON_DIR)
    if(DEFINED ENV{IDF_PATH})
        set(CJSON_DIR "$ENV{IDF_PATH}/components/json/cJSON" CACHE PATH "cJSON sources")
    else()
        message(FATAL_ERROR "Set IDF_PATH (ESP-IDF checkout) or pass -DCJSON_DIR=<dir with cJSON.c>")
    endif()
endif()
if(NOT EXISTS "${CJSON_DIR}/cJSON.c")
    message(FATAL_ERROR "cJSON.c not found in ${CJSON_DIR}")
endif()

set(SANITIZER "address,undefined" CACHE STRING "address,undefined | thread | none")
set(SAN_FLAGS "")
if(NOT SANITIZER STREQUAL "none")
    set(SAN_FLAGS -fsanitize=${SANITIZER} -fno-sanitize-recover=all -fno-omit-frame-pointer)
endif()

set(STRICT_FLAGS
    -fno-exceptions -fno-rtti
    -Wall -Wextra -Werror -Wshadow -Wformat=2 -Wcast-align -Wnull-dereference
    -Wno-unused-parameter
    -g -O1)

include_directories(
    "${FW_ROOT}/test/host"
    "${FW_ROOT}/components/ewp/include"
    "${FW_ROOT}/components/mcp_server/include"
    "${FW_ROOT}/components/tools/include"
    "${FW_ROOT}/components/provisioning/include"
    "${CJSON_DIR}"
    "${EWP_PROTOCOL_DIR}")

find_package(Threads REQUIRED)

set(PORTABLE_SRCS
    "${FW_ROOT}/components/ewp/src/crc32.cpp"
    "${FW_ROOT}/components/ewp/src/cbor.cpp"
    "${FW_ROOT}/components/ewp/src/frame.cpp"
    "${FW_ROOT}/components/mcp_server/src/os.cpp"
    "${FW_ROOT}/components/mcp_server/src/Uplink.cpp"
    "${FW_ROOT}/components/mcp_server/src/MCPServer.cpp"
    "${FW_ROOT}/components/tools/src/Telemetry.cpp"
    "${FW_ROOT}/components/tools/src/Identity.cpp"
    "${FW_ROOT}/components/tools/src/EdgeTools.cpp"
    "${FW_ROOT}/components/provisioning/src/Json.cpp"
    "${FW_ROOT}/components/provisioning/src/Validate.cpp"
    "${FW_ROOT}/components/provisioning/src/Config.cpp"
    "${FW_ROOT}/components/provisioning/src/ProvHandler.cpp")

# cJSON is C and third-party: built without our -Werror policy.
add_library(cjson STATIC "${CJSON_DIR}/cJSON.c")
target_compile_options(cjson PRIVATE ${SAN_FLAGS})

add_library(fw_portable STATIC ${PORTABLE_SRCS})
target_link_libraries(fw_portable PUBLIC Threads::Threads cjson)
target_compile_options(fw_portable PRIVATE ${STRICT_FLAGS} ${SAN_FLAGS})

add_executable(fw_tests main.cpp test_ewp.cpp test_mcp.cpp test_tools.cpp test_prov.cpp)
target_compile_options(fw_tests PRIVATE ${STRICT_FLAGS} ${SAN_FLAGS})
target_link_libraries(fw_tests PRIVATE fw_portable ${SAN_FLAGS})

# Randomised robustness runs (ASan/UBSan abort on any violation).
add_executable(fw_fuzz fuzz.cpp)
target_compile_options(fw_fuzz PRIVATE ${STRICT_FLAGS} ${SAN_FLAGS})
target_link_libraries(fw_fuzz PRIVATE fw_portable ${SAN_FLAGS})

# Interop helpers driven by test/interop (TypeScript ProvClient, cbor-x).
add_executable(prov_host prov_host.cpp)
target_compile_options(prov_host PRIVATE ${STRICT_FLAGS} ${SAN_FLAGS})
target_link_libraries(prov_host PRIVATE fw_portable ${SAN_FLAGS})

add_executable(ewp_dump ewp_dump.cpp)
target_compile_options(ewp_dump PRIVATE ${STRICT_FLAGS} ${SAN_FLAGS})
target_link_libraries(ewp_dump PRIVATE fw_portable ${SAN_FLAGS})
