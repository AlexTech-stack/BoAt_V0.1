add_boat_plugin(backbone backbone_plugin.cpp)

target_include_directories(backbone PRIVATE
  ${BOAT_PROTO_GENERATED_DIR}
  ${protobuf_SOURCE_DIR}/src
  ${protobuf_SOURCE_DIR}/third_party/abseil-cpp
  ${grpc_SOURCE_DIR}/include
  ${grpc_SOURCE_DIR}/third_party/abseil-cpp
)

target_link_libraries(backbone PRIVATE
  boat_plugin_sdk
  boat_proto_generated
  grpc++
  grpc
  protobuf::libprotobuf
)
