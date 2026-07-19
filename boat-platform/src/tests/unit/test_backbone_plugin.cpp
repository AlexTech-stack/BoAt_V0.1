// Catch2 test for the backbone plugin ABI loading and basic lifecycle.
// Note: The plugin depends on gRPC/protobuf symbols from the gateway process,
// so we use RTLD_LAZY and avoid calling initialize (which starts a gRPC server).

#include <catch2/catch_test_macros.hpp>

#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <string>

#include "boat/frame.h"
#include "boat/plugin.h"

#ifndef BACKBONE_SO
#define BACKBONE_SO "src/plugins/backbone/backbone.so"
#endif

TEST_CASE("Backbone plugin exports ABI symbols", "[backbone]") {
  // Use RTLD_LAZY — gRPC/protobuf symbols are resolved by the gateway at runtime
  void* handle = dlopen(BACKBONE_SO, RTLD_LAZY | RTLD_LOCAL);
  REQUIRE(handle != nullptr);

  auto create_fn = reinterpret_cast<boat_plugin_create_fn>(
      dlsym(handle, "boat_plugin_create"));
  auto destroy_fn = reinterpret_cast<boat_plugin_destroy_fn>(
      dlsym(handle, "boat_plugin_destroy"));
  auto abi_version_fn = reinterpret_cast<boat_plugin_abi_version_fn>(
      dlsym(handle, "boat_plugin_abi_version"));

  REQUIRE(create_fn != nullptr);
  REQUIRE(destroy_fn != nullptr);
  REQUIRE(abi_version_fn != nullptr);

  // Check ABI version
  CHECK(abi_version_fn() == BOAT_PLUGIN_ABI_VERSION);
  CHECK(abi_version_fn() == 9);

  // Create plugin instance
  BoatPlugin* plugin = create_fn();
  REQUIRE(plugin != nullptr);
  REQUIRE(plugin->vtable != nullptr);
  REQUIRE(plugin->ctx != nullptr);

  // Verify vtable slots are populated (not null)
  CHECK(plugin->vtable->initialize != nullptr);
  CHECK(plugin->vtable->on_tick != nullptr);
  CHECK(plugin->vtable->shutdown != nullptr);
  CHECK(plugin->vtable->on_frame != nullptr);
  CHECK(plugin->vtable->set_frame_publisher != nullptr);
  CHECK(plugin->vtable->declared_buses != nullptr);

  // Don't call initialize() — it starts a gRPC server which requires
  // symbols from the gateway process.  Just verify the vtable structure.

  destroy_fn(plugin);
  int rc = dlclose(handle);
  CHECK(rc == 0);
}
