#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <vector>

#include "signal/signal_router.h"

namespace boat::core {

class DeterminismEngine;

enum class FaultType { STUCK, NOISE, DROPOUT, INVERT, DELAY };

struct FaultSpec {
  std::uint64_t signal_id;
  std::uint64_t start_tick;
  std::uint64_t end_tick;
  FaultType type;
  double magnitude{0.0};
  std::uint64_t delay_ticks{0};
};

class FaultInjector {
 public:
  enum class ApplyResult { PASS, DROP, DEFER };

  explicit FaultInjector(DeterminismEngine& engine);

  void Schedule(FaultSpec spec);
  ApplyResult Apply(SignalEvent& event, std::uint64_t tick);
  std::vector<SignalEvent> FlushDelayed(std::uint64_t tick);
  void Clear();

 private:
  DeterminismEngine& engine_;
  std::vector<FaultSpec> faults_;
  std::multimap<std::uint64_t, SignalEvent> delay_queue_;
  std::mutex mutex_;
};

}  // namespace boat::core
