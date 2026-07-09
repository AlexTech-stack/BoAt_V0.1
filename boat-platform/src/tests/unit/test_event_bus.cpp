#include <catch2/catch_test_macros.hpp>

#include <string>

#include "event/event_bus.h"

TEST_CASE("EventBus publish dispatch and unsubscribe behavior", "[unit][event_bus]") {
  boat::core::EventBus bus;

  int type_a_hits = 0;
  int type_b_hits = 0;

  const auto a_handle = bus.Subscribe(1, [&](const boat::core::BusEvent& event) {
    REQUIRE(event.type == 1);
    REQUIRE(std::get<std::string>(event.payload) == "payload-a");
    ++type_a_hits;
  });

  bus.Subscribe(2, [&](const boat::core::BusEvent& event) {
    REQUIRE(event.type == 2);
    REQUIRE(std::get<std::int64_t>(event.payload) == 99);
    ++type_b_hits;
  });

  bus.Publish(boat::core::BusEvent{.type = 1, .payload = std::string("payload-a"), .tick = 1});
  bus.Publish(boat::core::BusEvent{.type = 2, .payload = std::int64_t{99}, .tick = 1});
  bus.Dispatch();

  REQUIRE(type_a_hits == 1);
  REQUIRE(type_b_hits == 1);

  bus.Unsubscribe(a_handle);
  bus.Publish(boat::core::BusEvent{.type = 1, .payload = std::string("payload-a"), .tick = 2});
  bus.Dispatch();
  REQUIRE(type_a_hits == 1);
}
