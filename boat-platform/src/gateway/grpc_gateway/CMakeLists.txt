add_executable(
  boat_gateway
  main.cpp
  bus_service_impl.cpp
  can_service_impl.cpp
  debug_service_impl.cpp
  ethernet_service_impl.cpp
  pdu_service_impl.cpp
  simulation_service_impl.cpp
  signal_service_impl.cpp
  scenario_service_impl.cpp
  replay_service_impl.cpp
  plugin_service_impl.cpp
  metrics_service_impl.cpp
  trace_service_impl.cpp
  fault_service_impl.cpp
  frame_service_impl.cpp
  frame_sink.cpp
)
target_include_directories(boat_gateway PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${BOAT_PROTO_GENERATED_DIR}
  ${CMAKE_SOURCE_DIR}/src/hil
)
target_link_libraries(
  boat_gateway
  PRIVATE
    boat_core
    boat_hil
    boat_ipc
    boat_store
    boat_replay
    grpc++
    protobuf::libprotobuf
    spdlog::spdlog
)
target_compile_features(boat_gateway PRIVATE cxx_std_20)

install(TARGETS boat_gateway RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
