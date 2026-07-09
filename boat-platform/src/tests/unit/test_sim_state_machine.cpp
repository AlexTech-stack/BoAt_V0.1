#include <catch2/catch_test_macros.hpp>

#include <vector>

#include "state/sim_state_machine.h"

TEST_CASE("SimStateMachine transition rules and observers", "[unit][sim_state_machine]") {
  boat::core::SimStateMachine sm;
  std::vector<std::pair<boat::core::SimState, boat::core::SimState>> observed;
  const auto token = sm.OnTransition([&](boat::core::SimState from, boat::core::SimState to) { observed.emplace_back(from, to); });

  REQUIRE(sm.Transition(boat::core::SimState::RUNNING));
  REQUIRE(sm.Transition(boat::core::SimState::PAUSED));
  REQUIRE(sm.Transition(boat::core::SimState::RUNNING));
  REQUIRE(sm.Transition(boat::core::SimState::STOPPED));

  SECTION("Invalid transition returns false") {
    REQUIRE_FALSE(sm.Transition(boat::core::SimState::RUNNING));
    REQUIRE(sm.Current() == boat::core::SimState::STOPPED);
  }

  REQUIRE(observed.size() == 4);

  SECTION("Observers can be unregistered") {
    REQUIRE(sm.RemoveObserver(token));
    REQUIRE_FALSE(sm.RemoveObserver(token));
  }
}
