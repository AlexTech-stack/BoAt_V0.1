include_guard(GLOBAL)

# ── add_boat_test ──────────────────────────────────────────────────────────────
#
# Creates a Catch2 test executable with the test harness.
#
# Usage:
#   add_boat_test(boat_test_<name> test_<name>.cpp)
#
# Links: boat_core boat_hil Catch2::Catch2WithMain
# Sets include dirs for: ${CMAKE_SOURCE_DIR}, ${CMAKE_SOURCE_DIR}/src,
#   ${CMAKE_SOURCE_DIR}/src/hil, ${CMAKE_SOURCE_DIR}/sdk/cpp/include
#
function(add_boat_test target_name)
  add_executable(${target_name} ${ARGN})
  target_compile_features(${target_name} PRIVATE cxx_std_20)
  target_include_directories(${target_name} PRIVATE
    ${CMAKE_SOURCE_DIR}
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/src/hil
    ${CMAKE_SOURCE_DIR}/sdk/cpp/include
  )
  target_link_libraries(${target_name} PRIVATE
    boat_core
    boat_hil
    Catch2::Catch2WithMain
  )
  catch_discover_tests(${target_name})
endfunction()
