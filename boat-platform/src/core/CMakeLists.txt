add_library(boat_core STATIC
  frame.cpp
  scheduler/sim_clock.cpp
  scheduler/tick_scheduler.cpp
  signal/signal_router.cpp
  signal/signal_bus.cpp
  event/event_bus.cpp
  plugin/plugin_manager.cpp
  state/sim_state_machine.cpp
  determinism/determinism_engine.cpp
  fault/fault_injector.cpp
  scenario/scenario_loader.cpp
  simulation/simulation_context.cpp
)

target_include_directories(boat_core PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${CMAKE_SOURCE_DIR}/src"
  "${CMAKE_SOURCE_DIR}/sdk/cpp/include"
)
target_compile_features(boat_core PUBLIC cxx_std_20)
set_target_properties(boat_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(boat_core PRIVATE ${CMAKE_DL_LIBS})
