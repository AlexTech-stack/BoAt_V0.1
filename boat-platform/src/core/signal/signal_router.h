#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <variant>
#include <vector>

namespace boat::core {

class FaultInjector;

struct SignalEvent {
  std::uint64_t signal_id;
  std::uint64_t tick;
  std::variant<double, std::int64_t, bool, std::vector<std::uint8_t>, std::string> value;
};

struct FilterPredicate {
  std::uint64_t signal_id;
  std::optional<std::uint64_t> tick_min;
  std::optional<std::uint64_t> tick_max;
  std::function<bool(const SignalEvent&)> comparator;
};

class SignalRouter {
 public:
  using SubscriptionHandle = std::uint64_t;
  using Callback = std::function<void(const SignalEvent&)>;

  SignalRouter();
  ~SignalRouter();

  SubscriptionHandle Subscribe(std::uint64_t signal_id, FilterPredicate filter, Callback callback);
  void Unsubscribe(SubscriptionHandle handle);
  void Publish(const SignalEvent& event);
  void SetFaultInjector(FaultInjector* injector);

 private:
  static constexpr std::size_t kRingBufferSize = 4096;

  struct SpscRingBuffer {
    std::array<SignalEvent, kRingBufferSize> buffer{};
    std::atomic<std::size_t> head{0};
    std::atomic<std::size_t> tail{0};

    bool Push(const SignalEvent& event);
    bool Pop(SignalEvent& out);
  };

  struct Subscription {
    SubscriptionHandle handle;
    std::uint64_t signal_id;
    FilterPredicate filter;
    Callback callback;
    SpscRingBuffer queue;
    std::atomic<std::uint64_t> overflow_count{0};
    std::atomic<bool> active{true};
  };

  bool Matches(const Subscription& subscription, const SignalEvent& event) const;
  void EnqueueEvent(const SignalEvent& event);
  void CleanupInactiveSubscriptions();
  void DispatchLoop();

  std::atomic<bool> running_{true};
  std::thread dispatcher_thread_;
  std::atomic<SubscriptionHandle> next_handle_{1};
  std::vector<std::shared_ptr<Subscription>> subscriptions_;
  mutable std::mutex subscriptions_mutex_;
  FaultInjector* fault_injector_{nullptr};
};

}  // namespace boat::core
