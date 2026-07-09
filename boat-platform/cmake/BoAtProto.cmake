function(boat_register_proto_directory proto_dir)
  if(NOT EXISTS "${proto_dir}")
    message(FATAL_ERROR "Proto directory does not exist: ${proto_dir}")
  endif()

  file(GLOB proto_files "${proto_dir}/*.proto")
  if(proto_files STREQUAL "")
    message(FATAL_ERROR "No .proto files found in: ${proto_dir}")
  endif()

  add_custom_target(boat_proto_contracts ALL
    COMMAND ${CMAKE_COMMAND} -E echo "Verified proto contracts in ${proto_dir}"
    DEPENDS ${proto_files}
    COMMENT "Checking proto contract availability"
    VERBATIM
  )

  message(STATUS "BoAtProto: protoc target exists=${TARGET_PROTOC}, grpc_cpp_plugin target exists=${TARGET_GRPC_PLUGIN}")
  if(TARGET protoc)
    set(TARGET_PROTOC YES)
  else()
    set(TARGET_PROTOC NO)
  endif()
  if(TARGET grpc_cpp_plugin)
    set(TARGET_GRPC_PLUGIN YES)
  else()
    set(TARGET_GRPC_PLUGIN NO)
  endif()
  message(STATUS "BoAtProto: protoc=${TARGET_PROTOC} grpc_cpp_plugin=${TARGET_GRPC_PLUGIN}")
  if(TARGET protoc AND TARGET grpc_cpp_plugin)
    set(BOAT_PROTO_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated/proto" CACHE PATH "Generated proto include directory" FORCE)
    file(MAKE_DIRECTORY "${BOAT_PROTO_GENERATED_DIR}")

    get_filename_component(proto_base_dir "${proto_dir}/../.." ABSOLUTE)
    set(generated_cc_files "")
    set(generated_h_files "")

    foreach(proto_file IN LISTS proto_files)
      get_filename_component(proto_name "${proto_file}" NAME_WE)
      get_filename_component(proto_relative "${proto_file}" DIRECTORY)
      file(RELATIVE_PATH proto_rel_dir "${proto_base_dir}" "${proto_relative}")
      set(output_dir "${BOAT_PROTO_GENERATED_DIR}/${proto_rel_dir}")
      file(MAKE_DIRECTORY "${output_dir}")

      set(pb_cc "${output_dir}/${proto_name}.pb.cc")
      set(pb_h "${output_dir}/${proto_name}.pb.h")
      set(grpc_cc "${output_dir}/${proto_name}.grpc.pb.cc")
      set(grpc_h "${output_dir}/${proto_name}.grpc.pb.h")

      add_custom_command(
        OUTPUT "${pb_cc}" "${pb_h}" "${grpc_cc}" "${grpc_h}"
        COMMAND $<TARGET_FILE:protoc>
          "--proto_path=${proto_base_dir}"
          "--cpp_out=${BOAT_PROTO_GENERATED_DIR}"
          "--grpc_out=${BOAT_PROTO_GENERATED_DIR}"
          "--plugin=protoc-gen-grpc=$<TARGET_FILE:grpc_cpp_plugin>"
          "${proto_file}"
        DEPENDS "${proto_file}" protoc grpc_cpp_plugin
        COMMENT "Generating protobuf/grpc sources for ${proto_file}"
        VERBATIM
      )

      list(APPEND generated_cc_files "${pb_cc}" "${grpc_cc}")
      list(APPEND generated_h_files "${pb_h}" "${grpc_h}")
    endforeach()

    add_library(boat_proto_generated STATIC ${generated_cc_files})
    target_include_directories(boat_proto_generated PUBLIC "${BOAT_PROTO_GENERATED_DIR}")
    target_link_libraries(boat_proto_generated PUBLIC protobuf::libprotobuf grpc++)
    add_dependencies(boat_proto_generated boat_proto_contracts)
  endif()
endfunction()
