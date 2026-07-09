include(BoAtTest)

FetchContent_Declare(
  Catch2
  GIT_REPOSITORY https://github.com/catchorg/Catch2.git
  GIT_TAG v3.6.0
)
FetchContent_MakeAvailable(Catch2)
include(Catch)

add_executable(boat_unit_tick_scheduler unit/test_tick_scheduler.cpp)
target_link_libraries(boat_unit_tick_scheduler PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_tick_scheduler)

add_executable(boat_unit_signal_router unit/test_signal_router.cpp)
target_link_libraries(boat_unit_signal_router PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_signal_router)

add_executable(boat_unit_event_bus unit/test_event_bus.cpp)
target_link_libraries(boat_unit_event_bus PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_event_bus)

add_executable(boat_unit_plugin_manager unit/test_plugin_manager.cpp)
target_link_libraries(boat_unit_plugin_manager PRIVATE boat_core Catch2::Catch2WithMain)
if(TARGET pdu_router)
  add_dependencies(boat_unit_plugin_manager pdu_router)
  target_compile_definitions(boat_unit_plugin_manager PRIVATE
    PDU_ROUTER_SO="$<TARGET_FILE:pdu_router>")
endif()
catch_discover_tests(boat_unit_plugin_manager)

add_executable(boat_unit_signal_bus unit/test_signal_bus.cpp)
target_link_libraries(boat_unit_signal_bus PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_signal_bus)

add_executable(boat_unit_sim_state_machine unit/test_sim_state_machine.cpp)
target_link_libraries(boat_unit_sim_state_machine PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_sim_state_machine)

add_executable(boat_unit_determinism_engine unit/test_determinism_engine.cpp)
target_link_libraries(boat_unit_determinism_engine PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_determinism_engine)

add_executable(boat_unit_fault_injector unit/test_fault_injector.cpp)
target_link_libraries(boat_unit_fault_injector PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_fault_injector)

add_executable(boat_unit_frame unit/test_frame.cpp)
target_link_libraries(boat_unit_frame PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_frame)

add_executable(boat_unit_sqlite_event_store unit/test_sqlite_event_store.cpp)
target_link_libraries(boat_unit_sqlite_event_store PRIVATE boat_core boat_store Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_sqlite_event_store)

add_executable(boat_unit_com_signal unit/test_com_signal.cpp)
target_link_libraries(boat_unit_com_signal PRIVATE boat_hil Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_com_signal)

add_executable(boat_unit_ipc_channel_selector unit/test_ipc_channel_selector.cpp)
target_link_libraries(boat_unit_ipc_channel_selector PRIVATE boat_ipc Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_ipc_channel_selector)

add_executable(boat_unit_replay_engine unit/test_replay_engine.cpp)
target_include_directories(boat_unit_replay_engine PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(boat_unit_replay_engine PRIVATE boat_core boat_store boat_replay Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_replay_engine)

add_executable(
  boat_integration_gateway
  integration/test_gateway.cpp
  ../gateway/grpc_gateway/pdu_service_impl.cpp
  ../gateway/grpc_gateway/simulation_service_impl.cpp
  ../gateway/grpc_gateway/scenario_service_impl.cpp
  ../gateway/grpc_gateway/signal_service_impl.cpp
)
target_include_directories(boat_integration_gateway PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${BOAT_PROTO_GENERATED_DIR}
)
target_link_libraries(
  boat_integration_gateway
  PRIVATE
    boat_core
    boat_hil
    boat_store
    boat_replay
    boat_proto_generated
    grpc++
    protobuf::libprotobuf
    Catch2::Catch2WithMain
)
catch_discover_tests(boat_integration_gateway)

add_executable(boat_integration_ipc_control_transport integration/test_ipc_control_transport.cpp)
target_include_directories(boat_integration_ipc_control_transport PRIVATE ${CMAKE_SOURCE_DIR}/src ${BOAT_PROTO_GENERATED_DIR})
target_link_libraries(
  boat_integration_ipc_control_transport
  PRIVATE
    boat_ipc
    boat_proto_generated
    Catch2::Catch2WithMain
)
catch_discover_tests(boat_integration_ipc_control_transport)

add_executable(
  boat_integration_replay_service
  integration/test_replay_service.cpp
  ../gateway/grpc_gateway/replay_service_impl.cpp
)
target_include_directories(boat_integration_replay_service PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${BOAT_PROTO_GENERATED_DIR}
)
target_link_libraries(
  boat_integration_replay_service
  PRIVATE
    boat_core
    boat_hil
    boat_store
    boat_replay
    boat_proto_generated
    grpc++
    protobuf::libprotobuf
    Catch2::Catch2WithMain
)
catch_discover_tests(boat_integration_replay_service)

add_executable(boat_determinism_seed determinism/test_seed.cpp)
target_link_libraries(boat_determinism_seed PRIVATE boat_core Catch2::Catch2WithMain)
catch_discover_tests(boat_determinism_seed)

add_executable(boat_unit_ethernet_bus_registry unit/test_ethernet_bus_registry.cpp)
target_include_directories(boat_unit_ethernet_bus_registry PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_ethernet_bus_registry PRIVATE boat_hil Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_ethernet_bus_registry)

add_executable(boat_unit_pdu_router unit/test_pdu_router.cpp)
target_include_directories(boat_unit_pdu_router PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_pdu_router PRIVATE boat_hil Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_pdu_router)

add_executable(boat_hil_smoke hil/test_hil.cpp)
target_link_libraries(boat_hil_smoke PRIVATE boat_hil Catch2::Catch2WithMain)
catch_discover_tests(boat_hil_smoke)

add_executable(boat_hil_ethernet hil/test_ethernet_hil.cpp)
target_include_directories(boat_hil_ethernet PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_hil_ethernet PRIVATE boat_hil Catch2::Catch2WithMain)
catch_discover_tests(boat_hil_ethernet)

# ── TCP Plugin ───────────────────────────────────────────────────────────────

add_executable(boat_unit_tcp_options unit/test_tcp_options.cpp)
target_include_directories(boat_unit_tcp_options PRIVATE
    ${CMAKE_SOURCE_DIR}/src/plugins/tcp
    ${CMAKE_SOURCE_DIR}/sdk/cpp/include
)
target_compile_features(boat_unit_tcp_options PRIVATE cxx_std_20)
target_link_libraries(boat_unit_tcp_options PRIVATE Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_tcp_options)

# ── Test Harness (P6) ────────────────────────────────────────────────────────

add_boat_test(boat_test_harness unit/test_harness_example.cpp)
