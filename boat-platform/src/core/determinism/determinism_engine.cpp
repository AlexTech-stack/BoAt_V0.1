#include "determinism/determinism_engine.h"

#include <limits>
#include <stdexcept>

namespace boat::core {

DeterminismEngine::DeterminismEngine(std::uint64_t seed)
    : seed_(seed), last_tick_(std::numeric_limits<std::uint64_t>::max()), rng_(seed) {}

void DeterminismEngine::BeforeTick(std::uint64_t tick) {
  if (last_tick_ != std::numeric_limits<std::uint64_t>::max() && tick <= last_tick_) {
    throw std::logic_error("tick must be monotonically increasing");
  }
  rng_.seed(seed_ ^ tick);
  last_tick_ = tick;
}

std::uint64_t DeterminismEngine::NextRandom() { return rng_(); }

}  // namespace boat::core
