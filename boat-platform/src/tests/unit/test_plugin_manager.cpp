#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <thread>
#include <vector>

#include "plugin/plugin_manager.h"

// A minimal mock plugin .so built by the test harness (see CMakeLists.txt).
// If MOCK_PLUGIN_SO is not defined the load tests are skipped.
#ifndef MOCK_PLUGIN_SO
#define MOCK_PLUGIN_SO ""
#endif

TEST_CASE("PluginManager safe behavior with no plugins", "[unit][plugin_manager]") {
  boat::core::PluginManager manager;

  SECTION("List is empty on initialization") { REQUIRE(manager.List().empty()); }

  SECTION("Unload unknown name is safe") {
    manager.Unload("does-not-exist");
    REQUIRE(manager.List().empty());
  }

  SECTION("TickAll with zero plugins is a no-op") {
    manager.TickAll(123);
    REQUIRE(manager.List().empty());
  }
}

TEST_CASE("PluginManager thread safety under concurrent access", "[unit][plugin_manager]") {
  boat::core::PluginManager manager;

  // Wire a no-op publisher so the setter path is exercised
  manager.SetPublisher([](const char*, std::uint64_t, double) {});
  manager.SetFramePublisher([](const BoatFrame&) {});
  manager.SetBusPublisher([](const char*, double) {});
  manager.SetPduPublisher([](const BoatPduFrame&) {});

  std::atomic<bool> done{false};

  // Background thread continuously calls TickAll
  std::thread ticker([&]() {
    while (!done.load(std::memory_order_acquire)) {
      manager.TickAll(1);
      manager.DispatchFrame(BoatFrame{});
    }
  });

  // Foreground thread loads and unloads repeatedly via ShutdownAll
  // (which uses Unload internally) and List
  for (int i = 0; i < 100; ++i) {
    // Load a dummy handle to populate the map (simulating load without real .so)
    // We cannot call Load without a real .so, so we exercise ShutdownAll/List
    // on an empty map — the main goal is to exercise the mutex paths.
    manager.ShutdownAll();
    auto names = manager.List();
    (void)names;
  }

  done.store(true, std::memory_order_release);
  ticker.join();
  REQUIRE(manager.List().empty());
}

#ifdef PDU_ROUTER_SO
TEST_CASE("PluginManager auto-registers and unregisters a plugin's exported service",
          "[unit][plugin_manager]") {
  // Exercises the real boat_plugin_service_name/boat_plugin_service_ptr
  // dlsym-based auto-registration path end-to-end, using the actual
  // pdu_router.so built by this same build -- this is the exact mechanism
  // that fixes PduService's gRPC RPCs previously always returning NOT_FOUND.
  boat::core::PluginManager manager;

  REQUIRE(manager.FindService("pdu_router") == nullptr);

  manager.Load(PDU_ROUTER_SO, "{}");
  REQUIRE(manager.FindService("pdu_router") != nullptr);

  // Unload must remove the registration too, or FindService would hand out
  // a dangling pointer into the now-destroyed plugin.
  manager.Unload(PDU_ROUTER_SO);
  REQUIRE(manager.FindService("pdu_router") == nullptr);
}
#endif
