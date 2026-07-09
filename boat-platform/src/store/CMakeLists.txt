add_library(boat_store STATIC
  event_store/event_store.cpp
  trace_store/trace_store.cpp
  config_store/config_store.cpp
)

target_include_directories(
  boat_store
  PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}"
  PRIVATE "${sqlite3_SOURCE_DIR}"
)

target_link_libraries(
  boat_store
  PUBLIC boat_core
  PUBLIC tomlplusplus::tomlplusplus
  PRIVATE sqlite3 spdlog::spdlog
)

target_compile_features(boat_store PUBLIC cxx_std_20)
