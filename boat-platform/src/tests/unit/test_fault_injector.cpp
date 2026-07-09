#include <catch2/catch_test_macros.hpp>

#include "determinism/determinism_engine.h"
#include "fault/fault_injector.h"

TEST_CASE("FaultInjector applies configured fault modes", "[unit][fault_injector]") {
  boat::core::DeterminismEngine engine(99);
  boat::core::FaultInjector injector(engine);

  SECTION("DROPOUT returns DROP") {
    injector.Schedule(boat::core::FaultSpec{
        .signal_id = 1,
        .start_tick = 1,
        .end_tick = 10,
        .type = boat::core::FaultType::DROPOUT,
    });
    auto event = boat::core::SignalEvent{.signal_id = 1, .tick = 2, .value = std::int64_t{123}};
    REQUIRE(injector.Apply(event, 2) == boat::core::FaultInjector::ApplyResult::DROP);
  }

  SECTION("DELAY defers and flushes at expected tick") {
    injector.Schedule(boat::core::FaultSpec{
        .signal_id = 2,
        .start_tick = 1,
        .end_tick = 10,
        .type = boat::core::FaultType::DELAY,
        .delay_ticks = 5,
    });
    auto event = boat::core::SignalEvent{.signal_id = 2, .tick = 3, .value = std::int64_t{456}};
    REQUIRE(injector.Apply(event, 3) == boat::core::FaultInjector::ApplyResult::DEFER);
    REQUIRE(injector.FlushDelayed(7).empty());
    const auto flushed = injector.FlushDelayed(8);
    REQUIRE(flushed.size() == 1);
    REQUIRE(flushed.front().signal_id == 2);
  }

  SECTION("STUCK overwrites value") {
    injector.Schedule(boat::core::FaultSpec{
        .signal_id = 3,
        .start_tick = 1,
        .end_tick = 10,
        .type = boat::core::FaultType::STUCK,
        .magnitude = 7.0,
    });
    auto event = boat::core::SignalEvent{.signal_id = 3, .tick = 5, .value = std::int64_t{1}};
    REQUIRE(injector.Apply(event, 5) == boat::core::FaultInjector::ApplyResult::PASS);
    REQUIRE(std::get<std::int64_t>(event.value) != 1);
  }
}
