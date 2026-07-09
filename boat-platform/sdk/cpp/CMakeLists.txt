add_library(boat_plugin_sdk INTERFACE)

target_include_directories(boat_plugin_sdk
  INTERFACE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
)

install(
  FILES "${CMAKE_CURRENT_SOURCE_DIR}/include/boat/plugin.h"
  DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/boat"
)
