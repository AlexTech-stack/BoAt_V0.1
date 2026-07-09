#include <catch2/catch_test_macros.hpp>

#include "determinism/determinism_engine.h"
#include "event/event_bus.h"
#include "scheduler/sim_clock.h"
#include "scheduler/tick_scheduler.h"

TEST_CASE("TickScheduler lifecycle and stepping", "[unit][tick_scheduler]") {
  boat::core::SimClock clock(42);
  boat::core::EventBus event_bus;
  boat::core::DeterminismEngine determinism(42);
  boat::core::TickScheduler scheduler(clock, event_bus, determinism, 2);

  SECTION("Step advances clock by n when stopped") {
    scheduler.Step(5);
    REQUIRE(clock.tick() == 5);
  }

  SECTION("Start pause resume stop sequence is callable") {
    scheduler.Start();
    scheduler.Pause();
    const std::uint64_t tick_before_step = clock.tick();
    scheduler.Step(3);
    REQUIRE(clock.tick() == tick_before_step + 3);
    scheduler.Resume();
    scheduler.Stop();
    const std::uint64_t tick_at_stop = clock.tick();
    scheduler.Step(3);
    REQUIRE(clock.tick() == tick_at_stop + 3);
  }
}
