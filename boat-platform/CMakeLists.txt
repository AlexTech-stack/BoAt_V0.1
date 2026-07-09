cmake_minimum_required(VERSION 3.24)

project(boat_platform VERSION 0.1.0 LANGUAGES C CXX)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(CTest)
include(GNUInstallDirs)
include(BoAtPlugin)
include(BoAtTest)
include(BoAtProto)
include(Packaging)
include(FetchContent)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BOAT_ENABLE_PROTO "Enable protobuf generation checks" ON)
option(BOAT_BUILD_TESTS "Build test targets" ON)
option(BOAT_HIL_ENABLED "Enable HIL hardware tests" OFF)

# iceoryx_hoofs requires sys/acl.h (libacl1-dev). If it is not installed
# system-wide, download and extract the package into the build tree.
if(NOT EXISTS "/usr/include/sys/acl.h")
  set(_acl_vendor "${CMAKE_BINARY_DIR}/vendor/libacl")
  if(NOT EXISTS "${_acl_vendor}/usr/include/sys/acl.h")
    find_program(_apt_get apt-get REQUIRED)
    message(STATUS "sys/acl.h not found — downloading libacl1-dev into build tree")
    execute_process(
      COMMAND ${_apt_get} download libacl1-dev
      WORKING_DIRECTORY "${_acl_vendor}"
      RESULT_VARIABLE _acl_dl_result)
    if(_acl_dl_result EQUAL 0)
      file(GLOB _acl_deb "${_acl_vendor}/libacl1-dev*.deb")
      execute_process(COMMAND dpkg-deb -x "${_acl_deb}" "${_acl_vendor}")
    endif()
  endif()
  if(EXISTS "${_acl_vendor}/usr/include/sys/acl.h")
    include_directories(SYSTEM "${_acl_vendor}/usr/include")
    link_directories("${_acl_vendor}/usr/lib/x86_64-linux-gnu")
  endif()
endif()

set(gRPC_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(gRPC_BUILD_CSHARP_EXT OFF CACHE BOOL "" FORCE)
set(IOX2_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(protobuf_BUILD_TESTS OFF CACHE BOOL "" FORCE)
# Tell gRPC to use find_package(protobuf) instead of its bundled third_party copy.
# Combined with OVERRIDE_FIND_PACKAGE on the protobuf FetchContent below, this
# redirects gRPC to the already-fetched protobuf and prevents duplicate gtest targets.
set(gRPC_PROTOBUF_PROVIDER "package" CACHE STRING "" FORCE)

FetchContent_Declare(
  spdlog
  GIT_REPOSITORY https://github.com/gabime/spdlog.git
  GIT_TAG v1.14.1
)

FetchContent_Declare(
  protobuf
  GIT_REPOSITORY https://github.com/protocolbuffers/protobuf.git
  GIT_TAG v27.3
  OVERRIDE_FIND_PACKAGE
)

FetchContent_Declare(
  gRPC
  GIT_REPOSITORY https://github.com/grpc/grpc.git
  GIT_TAG v1.65.0
)

FetchContent_Declare(
  iceoryx2
  GIT_REPOSITORY https://github.com/eclipse-iceoryx/iceoryx2.git
  GIT_TAG v0.4.1
)

FetchContent_Declare(
  sqlite3
  URL https://www.sqlite.org/2024/sqlite-amalgamation-3460000.zip
)

FetchContent_Declare(
  tomlplusplus
  GIT_REPOSITORY https://github.com/marzer/tomlplusplus.git
  GIT_TAG v3.4.0
)

# protobuf must be available before gRPC so that gRPC's find_package(protobuf)
# is redirected to our FetchContent copy via OVERRIDE_FIND_PACKAGE.
FetchContent_MakeAvailable(spdlog protobuf tomlplusplus)

# gRPC needs patching before add_subdirectory: when using gRPC_PROTOBUF_PROVIDER
# "package", gRPC's cmake/protobuf.cmake calls:
#   get_target_property(_gRPC_PROTOBUF_WELLKNOWN_INCLUDE_DIR protobuf::libprotoc
#                       INTERFACE_INCLUDE_DIRECTORIES)
# libprotoc only sets $<BUILD_INTERFACE:...> on libprotobuf (not libprotoc), so
# the property is NOTFOUND and protoc can't find well-known .proto files.
# Fix: replace that line with a direct use of protobuf_SOURCE_DIR, which is in
# scope because FetchContent_MakeAvailable(protobuf) already ran above.
FetchContent_GetProperties(grpc)
if(NOT grpc_POPULATED)
  FetchContent_Populate(grpc)
endif()
FetchContent_GetProperties(protobuf SOURCE_DIR _protobuf_src_dir)
set(_grpc_pb_cmake "${grpc_SOURCE_DIR}/cmake/protobuf.cmake")
file(READ "${_grpc_pb_cmake}" _grpc_pb_content)
string(REPLACE
    "get_target_property(_gRPC_PROTOBUF_WELLKNOWN_INCLUDE_DIR protobuf::libprotoc INTERFACE_INCLUDE_DIRECTORIES)"
    "set(_gRPC_PROTOBUF_WELLKNOWN_INCLUDE_DIR \"${_protobuf_src_dir}/src\")"
    _grpc_pb_content "${_grpc_pb_content}")
file(WRITE "${_grpc_pb_cmake}" "${_grpc_pb_content}")
add_subdirectory("${grpc_SOURCE_DIR}" "${grpc_BINARY_DIR}" EXCLUDE_FROM_ALL)

# iceoryx2 hardcodes WORKING_DIRECTORY ${CMAKE_SOURCE_DIR} in its cargo build
# step, which resolves to our project root when used via FetchContent instead of
# the iceoryx2 workspace root where Cargo.toml lives. Patch before add_subdirectory.
FetchContent_GetProperties(iceoryx2)
if(NOT iceoryx2_POPULATED)
  FetchContent_Populate(iceoryx2)
endif()
set(_iox2_c_cmake "${iceoryx2_SOURCE_DIR}/iceoryx2-ffi/c/CMakeLists.txt")
file(READ "${_iox2_c_cmake}" _iox2_content)
# Fix 1: wrong WORKING_DIRECTORY when used via FetchContent
string(REPLACE
    "WORKING_DIRECTORY \${CMAKE_SOURCE_DIR}"
    "WORKING_DIRECTORY \${CMAKE_CURRENT_SOURCE_DIR}/../.."
    _iox2_content "${_iox2_content}")
# Fix 2: bindgen can't find stddef.h without the clang package — point it at the
# GCC built-in headers that are already present on the system.
execute_process(
    COMMAND ${CMAKE_C_COMPILER} -print-search-dirs
    OUTPUT_VARIABLE _gcc_search_dirs OUTPUT_STRIP_TRAILING_WHITESPACE)
string(REGEX MATCH "install: ([^\n]+)" _ "${_gcc_search_dirs}")
set(_gcc_install_dir "${CMAKE_MATCH_1}")
set(_gcc_include_dir "${_gcc_install_dir}include")
# Find cargo — it may live in ~/.cargo/bin which is not in the system PATH
find_program(_cargo_exe cargo HINTS "$ENV{HOME}/.cargo/bin" "/root/.cargo/bin")
if(NOT _cargo_exe)
  message(FATAL_ERROR "cargo not found; install Rust via rustup first")
endif()
message(STATUS "Found cargo: ${_cargo_exe}")
if(EXISTS "${_gcc_include_dir}/stddef.h")
    string(REPLACE
        "COMMAND cargo build"
        "COMMAND \${CMAKE_COMMAND} -E env \"BINDGEN_EXTRA_CLANG_ARGS=-I${_gcc_include_dir}\" ${_cargo_exe} build"
        _iox2_content "${_iox2_content}")
else()
    string(REPLACE
        "COMMAND cargo build"
        "COMMAND ${_cargo_exe} build"
        _iox2_content "${_iox2_content}")
endif()
file(WRITE "${_iox2_c_cmake}" "${_iox2_content}")
add_subdirectory("${iceoryx2_SOURCE_DIR}" "${iceoryx2_BINARY_DIR}" EXCLUDE_FROM_ALL)

FetchContent_GetProperties(sqlite3)
if(NOT sqlite3_POPULATED)
  FetchContent_Populate(sqlite3)
endif()

add_library(sqlite3 STATIC "${sqlite3_SOURCE_DIR}/sqlite3.c")
target_include_directories(sqlite3 PUBLIC "${sqlite3_SOURCE_DIR}")
target_compile_definitions(sqlite3 PUBLIC SQLITE_THREADSAFE=1 SQLITE_ENABLE_WAL)

if(NOT TARGET tomlplusplus::tomlplusplus)
  add_library(tomlplusplus INTERFACE)
  target_include_directories(tomlplusplus INTERFACE "${tomlplusplus_SOURCE_DIR}/include")
  add_library(tomlplusplus::tomlplusplus ALIAS tomlplusplus)
endif()

if(BOAT_ENABLE_PROTO)
  boat_register_proto_directory("${CMAKE_CURRENT_SOURCE_DIR}/proto/boat/v1")
endif()

add_subdirectory(src/core)
add_subdirectory(sdk/cpp)
add_subdirectory(src/plugins)
add_subdirectory(src/gateway)
add_subdirectory(src/store)
add_subdirectory(src/replay)
add_subdirectory(src/ipc)
add_subdirectory(src/hil)

if(BOAT_BUILD_TESTS AND BUILD_TESTING)
  add_subdirectory(src/tests)
endif()
