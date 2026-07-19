add_boat_plugin(scpi_device scpi_device_plugin.cpp)
target_include_directories(scpi_device PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(scpi_device PRIVATE boat_plugin_sdk boat_hil)
