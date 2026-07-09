function(add_boat_plugin target_name)
  add_library(${target_name} MODULE ${ARGN})
  target_compile_features(${target_name} PRIVATE cxx_std_20)
  set_target_properties(${target_name} PROPERTIES
    PREFIX ""
    OUTPUT_NAME "${target_name}"
  )
  install(TARGETS ${target_name}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}/boat/plugins
  )
endfunction()
