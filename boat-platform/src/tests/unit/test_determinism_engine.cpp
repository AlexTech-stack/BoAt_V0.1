#include <catch2/catch_test_macros.hpp>

#include "determinism/determinism_engine.h"

TEST_CASE("DeterminismEngine produces deterministic sequences", "[unit][determinism_engine]") {
  boat::core::DeterminismEngine a(1234);
  boat::core::DeterminismEngine b(1234);

  for (std::uint64_t tick = 1; tick <= 10; ++tick) {
    a.BeforeTick(tick);
    b.BeforeTick(tick);
    REQUIRE(a.NextRandom() == b.NextRandom());
  }
}
