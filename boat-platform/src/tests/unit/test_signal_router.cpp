#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <thread>

#include "signal/signal_router.h"

TEST_CASE("SignalRouter subscribe publish and unsubscribe", "[unit][signal_router]") {
  boat::core::SignalRouter router;

  SECTION("Subscribe and publish round-trip with filtering") {
    std::atomic<int> hits{0};
    boat::core::FilterPredicate filter{
        .signal_id = 10,
        .tick_min = 5,
        .tick_max = 20,
        .comparator =
            [](const boat::core::SignalEvent& event) {
              return std::holds_alternative<double>(event.value) && std::get<double>(event.value) > 1.0;
            },
    };

    const auto handle =
        router.Subscribe(10, filter, [&](const boat::core::SignalEvent&) { hits.fetch_add(1); });

    router.Publish(boat::core::SignalEvent{.signal_id = 10, .tick = 6, .value = 2.5});
    for (int i = 0; i < 100 && hits.load() < 1; ++i) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    REQUIRE(hits.load() == 1);

    router.Publish(boat::core::SignalEvent{.signal_id = 10, .tick = 7, .value = 0.5});
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    REQUIRE(hits.load() == 1);

    router.Unsubscribe(handle);
    router.Publish(boat::core::SignalEvent{.signal_id = 10, .tick = 8, .value = 3.0});
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    REQUIRE(hits.load() == 1);
  }
}
