#include "fault/fault_injector.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <string>

#include "determinism/determinism_engine.h"
#include "signal/signal_router.h"

namespace boat::core {

FaultInjector::FaultInjector(DeterminismEngine& engine) : engine_(engine) {}

void FaultInjector::Schedule(FaultSpec spec) {
  std::lock_guard<std::mutex> lock(mutex_);
  faults_.push_back(spec);
  std::sort(faults_.begin(), faults_.end(),
            [](const FaultSpec& lhs, const FaultSpec& rhs) { return lhs.start_tick < rhs.start_tick; });
}

FaultInjector::ApplyResult FaultInjector::Apply(SignalEvent& event, std::uint64_t tick) {
  std::lock_guard<std::mutex> lock(mutex_);
  for (const auto& fault : faults_) {
    if (fault.signal_id != event.signal_id) {
      continue;
    }
    if (tick < fault.start_tick || tick > fault.end_tick) {
      continue;
    }
    switch (fault.type) {
      case FaultType::STUCK:
        if (std::holds_alternative<double>(event.value)) {
          event.value = fault.magnitude;
        } else if (std::holds_alternative<std::int64_t>(event.value)) {
          event.value = static_cast<std::int64_t>(std::llround(fault.magnitude));
        } else if (std::holds_alternative<bool>(event.value)) {
          event.value = (std::abs(fault.magnitude) > std::numeric_limits<double>::epsilon());
        } else if (std::holds_alternative<std::vector<std::uint8_t>>(event.value)) {
          const auto byte = static_cast<std::uint8_t>(
              std::clamp<std::int64_t>(static_cast<std::int64_t>(std::llround(fault.magnitude)), 0, 255));
          auto& bytes = std::get<std::vector<std::uint8_t>>(event.value);
          std::fill(bytes.begin(), bytes.end(), byte);
        } else if (std::holds_alternative<std::string>(event.value)) {
          event.value = std::to_string(fault.magnitude);
        }
        break;
      case FaultType::NOISE:
        if (std::holds_alternative<double>(event.value)) {
          const double noise = static_cast<double>(engine_.NextRandom() % 10000ULL) / 10000.0 * fault.magnitude;
          event.value = std::get<double>(event.value) + noise;
        }
        break;
      case FaultType::DROPOUT:
        return ApplyResult::DROP;
      case FaultType::INVERT:
        if (std::holds_alternative<bool>(event.value)) {
          event.value = !std::get<bool>(event.value);
        } else if (std::holds_alternative<double>(event.value)) {
          event.value = -std::get<double>(event.value);
        } else if (std::holds_alternative<std::int64_t>(event.value)) {
          event.value = -std::get<std::int64_t>(event.value);
        }
        break;
      case FaultType::DELAY:
        delay_queue_.insert({tick + fault.delay_ticks, event});
        return ApplyResult::DEFER;
    }
  }
  return ApplyResult::PASS;
}

std::vector<SignalEvent> FaultInjector::FlushDelayed(std::uint64_t tick) {
  std::vector<SignalEvent> ready;
  std::lock_guard<std::mutex> lock(mutex_);
  auto end_it = delay_queue_.upper_bound(tick);
  for (auto it = delay_queue_.begin(); it != end_it; ++it) {
    ready.push_back(it->second);
  }
  delay_queue_.erase(delay_queue_.begin(), end_it);
  return ready;
}

void FaultInjector::Clear() {
  std::lock_guard<std::mutex> lock(mutex_);
  faults_.clear();
  delay_queue_.clear();
}

}  // namespace boat::core
