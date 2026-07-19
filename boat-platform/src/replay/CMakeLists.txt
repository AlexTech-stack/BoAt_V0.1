add_library(boat_replay STATIC
  replay_engine/replay_engine.cpp
  bus_signal_recorder.cpp
)

target_include_directories(boat_replay PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(boat_replay PUBLIC boat_core boat_store boat_hil boat_proto_generated)
target_compile_features(boat_replay PUBLIC cxx_std_20)
