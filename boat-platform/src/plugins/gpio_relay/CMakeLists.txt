add_boat_plugin(gpio_relay gpio_relay_plugin.cpp)
target_include_directories(gpio_relay PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(gpio_relay PRIVATE boat_plugin_sdk boat_hil)
