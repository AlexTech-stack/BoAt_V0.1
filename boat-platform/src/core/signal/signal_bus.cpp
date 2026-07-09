#include "signal/signal_bus.h"

#include <algorithm>
#include <utility>

namespace boat::core {

BusSubscriberId SignalBus::Subscribe(std::vector<std::string> names,
                                     BusSubscribeFn fn) {
  std::lock_guard<std::mutex> lock(mutex_);
  const BusSubscriberId id = next_id_++;
  subscriptions_[id] = Subscription{names, std::move(fn)};

  if (names.empty()) {
    wildcard_subscribers_.push_back(id);
  } else {
    for (const auto& n : names) {
      signal_index_[n].push_back(id);
    }
  }
  return id;
}

void SignalBus::Unsubscribe(BusSubscriberId id) {
  std::lock_guard<std::mutex> lock(mutex_);
  auto it = subscriptions_.find(id);
  if (it == subscriptions_.end()) return;

  if (it->second.names.empty()) {
    std::erase(wildcard_subscribers_, id);
  } else {
    for (const auto& n : it->second.names) {
      auto idx = signal_index_.find(n);
      if (idx != signal_index_.end()) {
        std::erase(idx->second, id);
        if (idx->second.empty()) signal_index_.erase(idx);
      }
    }
  }
  subscriptions_.erase(it);
}

void SignalBus::Publish(const std::string& name,
                        const BusSignalValue& value) {
  // Collect matching callbacks under lock via the index.
  std::vector<BusSubscribeFn> to_call;
  {
    std::lock_guard<std::mutex> lock(mutex_);

    // Named subscribers — O(1) lookup.
    auto it = signal_index_.find(name);
    if (it != signal_index_.end()) {
      to_call.reserve(it->second.size() + wildcard_subscribers_.size());
      for (auto sid : it->second) {
        to_call.push_back(subscriptions_[sid].fn);
      }
    }

    // Wildcard subscribers (match-all).
    for (auto sid : wildcard_subscribers_) {
      to_call.push_back(subscriptions_[sid].fn);
    }
  }
  // Invoke callbacks without holding the lock.
  BusSignal signal{name, value};
  for (auto& fn : to_call) {
    fn(signal);
  }
}

void SignalBus::Publish(const std::string& name, double value) {
  Publish(name, BusSignalValue{value});
}

}  // namespace boat::core
