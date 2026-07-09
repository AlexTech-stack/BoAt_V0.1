#pragma once

#include <cstdint>

namespace boat::core {

class SimClock {
 public:
  explicit SimClock(std::uint64_t seed = 0) : seed_(seed), tick_(0) {}

  void Step(std::uint64_t delta = 1) { tick_ += delta; }
  [[nodiscard]] std::uint64_t tick() const { return tick_; }
  [[nodiscard]] std::uint64_t seed() const { return seed_; }

 private:
  std::uint64_t seed_;
  std::uint64_t tick_;
};

}  // namespace boat::core
