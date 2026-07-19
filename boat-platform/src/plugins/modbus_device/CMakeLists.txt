add_boat_plugin(modbus_device modbus_device_plugin.cpp)
target_include_directories(modbus_device PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(modbus_device PRIVATE boat_plugin_sdk boat_hil)
