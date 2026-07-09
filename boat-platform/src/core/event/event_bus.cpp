#include "event/event_bus.h"

#include <algorithm>

namespace boat::core {

void EventBus::Publish(BusEvent event) {
  std::lock_guard<std::mutex> lock(mutex_);
  queue_.push(std::move(event));
}

EventBus::SubscriptionHandle EventBus::Subscribe(std::uint32_t type, HandlerFn handler) {
  std::lock_guard<std::mutex> lock(mutex_);
  const SubscriptionHandle handle = next_handle_++;
  subscribers_[type].push_back({handle, std::move(handler)});
  handle_to_type_[handle] = type;
  return handle;
}

void EventBus::Unsubscribe(SubscriptionHandle handle) {
  std::lock_guard<std::mutex> lock(mutex_);
  const auto it = handle_to_type_.find(handle);
  if (it == handle_to_type_.end()) {
    return;
  }
  const std::uint32_t type = it->second;
  auto& handlers = subscribers_[type];
  handlers.erase(std::remove_if(handlers.begin(), handlers.end(),
                                [handle](const HandlerEntry& entry) { return entry.handle == handle; }),
                 handlers.end());
  if (handlers.empty()) {
    subscribers_.erase(type);
  }
  handle_to_type_.erase(it);
}

void EventBus::Dispatch() {
  std::queue<BusEvent> pending;
  std::unordered_map<std::uint32_t, std::vector<HandlerEntry>> handlers_snapshot;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    std::swap(pending, queue_);
    handlers_snapshot = subscribers_;
  }

  while (!pending.empty()) {
    const BusEvent event = std::move(pending.front());
    pending.pop();
    auto it = handlers_snapshot.find(event.type);
    if (it == handlers_snapshot.end()) {
      continue;
    }
    for (const auto& handler : it->second) {
      handler.handler(event);
    }
  }
}

}  // namespace boat::core
