add_library(boat_hil STATIC
  can/socket_can_driver.cpp
  can/physical_can_driver.cpp
  virtual/virtual_can_driver.cpp
  hil_bridge.cpp
  can_bus_registry.cpp
  ethernet/virtual_ethernet_driver.cpp
  ethernet/raw_socket_ethernet_driver.cpp
  ethernet_bus_registry.cpp
  ethernet_hil_bridge.cpp
  pdu/pdu_router.cpp
  pdu/ipdumcontainer.cpp
  pdu/transmission_engine.cpp
  pdu/com/com_signal.cpp
  pdu/tick_timer.cpp
)
target_include_directories(boat_hil PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(boat_hil PUBLIC cxx_std_20)
set_target_properties(boat_hil PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(boat_hil PUBLIC boat_core)
