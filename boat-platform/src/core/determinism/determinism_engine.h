#pragma once

#include <cstdint>
#include <random>

namespace boat::core {

/* Seeds a Mersenne Twister PRNG with (seed_ ⊕ tick) at the start of every
   simulation tick.  This guarantees bit-identical random sequences across
   runs and environments for the same (seed, tick) pair.

   Thread-safety: NOT thread-safe.  Must be called from the coordinator
   thread only (see TickScheduler::ExecuteTick).

   Used by: FaultInjector noise amplitude, plugin random decisions. */
class DeterminismEngine {
 public:
  explicit DeterminismEngine(std::uint64_t seed);

  void BeforeTick(std::uint64_t tick);
  [[nodiscard]] std::uint64_t NextRandom();
  [[nodiscard]] std::uint64_t Seed() const { return seed_; }

 private:
  std::uint64_t seed_;
  std::uint64_t last_tick_;
  std::mt19937_64 rng_;
};

}  // namespace boat::core
