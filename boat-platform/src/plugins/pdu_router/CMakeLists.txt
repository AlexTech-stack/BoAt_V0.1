add_boat_plugin(pdu_router pdu_router_plugin.cpp)
target_include_directories(pdu_router PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${CMAKE_BINARY_DIR}/generated/proto
)
target_link_libraries(pdu_router PRIVATE boat_hil)
