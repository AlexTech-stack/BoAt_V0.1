#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <string>
#include <thread>
#include <vector>

#include "signal/signal_bus.h"

TEST_CASE("SignalBus subscribe publish and unsubscribe", "[unit][signal_bus]") {
  boat::core::SignalBus bus;

  SECTION("Empty bus publish is a no-op") {
    bus.Publish("test.signal", 42.0);
    REQUIRE(true);  // no crash
  }

  SECTION("Subscribe and publish round-trip") {
    std::atomic<int> hits{0};
    bus.Subscribe({"test.signal"}, [&](const boat::core::BusSignal&) {
      hits.fetch_add(1);
    });
    bus.Publish("test.signal", 1.0);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(hits.load() == 1);
  }

  SECTION("Subscribe with empty filter receives all signals") {
    std::atomic<int> hits{0};
    bus.Subscribe({}, [&](const boat::core::BusSignal&) { hits.fetch_add(1); });
    bus.Publish("alpha", 1.0);
    bus.Publish("beta", 2.0);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(hits.load() == 2);
  }

  SECTION("Unsubscribe stops deliveries") {
    std::atomic<int> hits{0};
    auto id = bus.Subscribe({"x"}, [&](const boat::core::BusSignal&) { hits.fetch_add(1); });
    bus.Publish("x", 1.0);
    bus.Unsubscribe(id);
    bus.Publish("x", 2.0);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(hits.load() == 1);
  }

  SECTION("Multiple subscribers to same signal") {
    std::atomic<int> hits{0};
    bus.Subscribe({"shared"}, [&](const boat::core::BusSignal&) { hits.fetch_add(1); });
    bus.Subscribe({"shared"}, [&](const boat::core::BusSignal&) { hits.fetch_add(1); });
    bus.Publish("shared", 1.0);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(hits.load() == 2);
  }

  SECTION("Name filtering — only matching subscribers fire") {
    std::atomic<int> a_hits{0}, b_hits{0};
    bus.Subscribe({"a"}, [&](const boat::core::BusSignal&) { a_hits.fetch_add(1); });
    bus.Subscribe({"b"}, [&](const boat::core::BusSignal&) { b_hits.fetch_add(1); });
    bus.Publish("a", 1.0);
    bus.Publish("a", 2.0);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(a_hits.load() == 2);
    REQUIRE(b_hits.load() == 0);
  }

  SECTION("Thread safety — concurrent publish") {
    std::atomic<int> hits{0};
    bus.Subscribe({"stress"}, [&](const boat::core::BusSignal&) { hits.fetch_add(1); });
    std::vector<std::thread> threads;
    for (int i = 0; i < 10; ++i) {
      threads.emplace_back([&]() {
        for (int j = 0; j < 100; ++j) {
          bus.Publish("stress", static_cast<double>(j));
        }
      });
    }
    for (auto& t : threads) t.join();
    REQUIRE(hits.load() == 1000);
  }

  SECTION("Double convenience overload") {
    double received = -1.0;
    bus.Subscribe({"d"}, [&](const boat::core::BusSignal& s) {
      if (auto* v = std::get_if<double>(&s.value)) {
        received = *v;
      }
    });
    bus.Publish("d", 3.14);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(received == 3.14);
  }

  SECTION("Signal carries correct name and value type") {
    std::string received_name;
    bool received_value = false;
    bus.Subscribe({"flag"}, [&](const boat::core::BusSignal& s) {
      received_name = s.name;
      if (auto* v = std::get_if<bool>(&s.value)) {
        received_value = *v;
      }
    });
    bus.Publish("flag", boat::core::BusSignalValue{true});
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(received_name == "flag");
    REQUIRE(received_value == true);
  }
}
