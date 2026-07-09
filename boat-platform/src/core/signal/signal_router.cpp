#include "signal/signal_router.h"

#include <algorithm>
#include <chrono>
#include <mutex>

#include "fault/fault_injector.h"

namespace boat::core {

bool SignalRouter::SpscRingBuffer::Push(const SignalEvent& event) {
  const std::size_t head_index = head.load(std::memory_order_relaxed);
  const std::size_t next_head = (head_index + 1) & (SignalRouter::kRingBufferSize - 1);
  const std::size_t tail_index = tail.load(std::memory_order_acquire);
  if (next_head == tail_index) {
    return false;
  }
  buffer[head_index] = event;
  head.store(next_head, std::memory_order_release);
  return true;
}

bool SignalRouter::SpscRingBuffer::Pop(SignalEvent& out) {
  const std::size_t tail_index = tail.load(std::memory_order_relaxed);
  const std::size_t head_index = head.load(std::memory_order_acquire);
  if (tail_index == head_index) {
    return false;
  }
  out = buffer[tail_index];
  tail.store((tail_index + 1) & (SignalRouter::kRingBufferSize - 1), std::memory_order_release);
  return true;
}

SignalRouter::SignalRouter() : dispatcher_thread_(&SignalRouter::DispatchLoop, this) {}

SignalRouter::~SignalRouter() {
  running_.store(false, std::memory_order_release);
  if (dispatcher_thread_.joinable()) {
    dispatcher_thread_.join();
  }
}

SignalRouter::SubscriptionHandle SignalRouter::Subscribe(std::uint64_t signal_id, FilterPredicate filter, Callback callback) {
  auto subscription = std::make_shared<Subscription>();
  subscription->handle = next_handle_.fetch_add(1, std::memory_order_relaxed);
  subscription->signal_id = signal_id;
  filter.signal_id = signal_id;
  subscription->filter = std::move(filter);
  subscription->callback = std::move(callback);

  std::lock_guard<std::mutex> lock(subscriptions_mutex_);
  subscriptions_.push_back(std::move(subscription));
  return subscriptions_.back()->handle;
}

void SignalRouter::Unsubscribe(SubscriptionHandle handle) {
  std::lock_guard<std::mutex> lock(subscriptions_mutex_);
  for (const auto& sub : subscriptions_) {
    if (sub->handle == handle) {
      sub->active.store(false, std::memory_order_release);
      break;
    }
  }
}

bool SignalRouter::Matches(const Subscription& subscription, const SignalEvent& event) const {
  if (subscription.signal_id != event.signal_id) {
    return false;
  }
  if (subscription.filter.tick_min.has_value() && event.tick < subscription.filter.tick_min.value()) {
    return false;
  }
  if (subscription.filter.tick_max.has_value() && event.tick > subscription.filter.tick_max.value()) {
    return false;
  }
  if (subscription.filter.comparator) {
    return subscription.filter.comparator(event);
  }
  return true;
}

void SignalRouter::Publish(const SignalEvent& event) {
  SignalEvent routed_event = event;
  bool deferred = false;
  if (fault_injector_ != nullptr) {
    const FaultInjector::ApplyResult result = fault_injector_->Apply(routed_event, routed_event.tick);
    if (result == FaultInjector::ApplyResult::DROP) {
      return;
    }
    deferred = (result == FaultInjector::ApplyResult::DEFER);

    const std::vector<SignalEvent> delayed_events = fault_injector_->FlushDelayed(routed_event.tick);
    for (const auto& delayed_event : delayed_events) {
      EnqueueEvent(delayed_event);
    }
  }

  if (!deferred) {
    EnqueueEvent(routed_event);
  }
}

void SignalRouter::EnqueueEvent(const SignalEvent& event) {
  std::lock_guard<std::mutex> lock(subscriptions_mutex_);
  for (auto& subscription : subscriptions_) {
    if (!subscription->active.load(std::memory_order_acquire)) {
      continue;
    }
    if (!Matches(*subscription, event)) {
      continue;
    }
    if (!subscription->queue.Push(event)) {
      subscription->overflow_count.fetch_add(1, std::memory_order_relaxed);
    }
  }
}

void SignalRouter::SetFaultInjector(FaultInjector* injector) { fault_injector_ = injector; }

void SignalRouter::CleanupInactiveSubscriptions() {
  std::lock_guard<std::mutex> lock(subscriptions_mutex_);
  subscriptions_.erase(std::remove_if(subscriptions_.begin(), subscriptions_.end(),
                                      [](const std::shared_ptr<Subscription>& sub) {
                                        return !sub->active.load(std::memory_order_acquire);
                                      }),
                       subscriptions_.end());
}

void SignalRouter::DispatchLoop() {
  while (running_.load(std::memory_order_acquire)) {
    std::vector<std::shared_ptr<Subscription>> subs;
    {
      std::lock_guard<std::mutex> lock(subscriptions_mutex_);
      subs.reserve(subscriptions_.size());
      for (auto& sub : subscriptions_) {
        if (sub->active.load(std::memory_order_acquire)) {
          subs.push_back(sub);
        }
      }
    }
    for (const auto& sub : subs) {
      if (!sub->active.load(std::memory_order_acquire)) {
        continue;
      }
      SignalEvent event{};
      while (sub->queue.Pop(event)) {
        if (!sub->active.load(std::memory_order_acquire)) {
          break;
        }
        sub->callback(event);
      }
    }
    CleanupInactiveSubscriptions();
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}

}  // namespace boat::core
