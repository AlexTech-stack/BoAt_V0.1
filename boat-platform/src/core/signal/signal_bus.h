#pragma once

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace boat::core {

/* Value type for always-on bus signals (wall-clock time, no tick dependency).
   Mirrors the type range of BusService protobuf without the gRPC dependency. */
using BusSignalValue = std::variant<double, std::int64_t, bool,
                                    std::vector<std::uint8_t>, std::string>;

struct BusSignal {
  std::string name;
  BusSignalValue value;
};

using BusSubscriberId = std::uint64_t;
using BusSubscribeFn  = std::function<void(const BusSignal&)>;

/* Always-on, simulation-independent signal bus.
   Thread-safe: Publish/Subscribe/Unsubscribe may be called from any thread.
   Signatures are not tick-coupled — values carry wall-clock time if the
   publisher stamps it (this class does not add timestamps). */
class SignalBus {
 public:
  /* Subscribe to one or more signal names (empty = all signals).
     Returns an ID for Unsubscribe(). */
  BusSubscriberId Subscribe(std::vector<std::string> names, BusSubscribeFn fn);

  /* Remove a subscription.  Safe to call with an invalid or already-removed ID. */
  void Unsubscribe(BusSubscriberId id);

  /* Publish a signal to all matching subscribers.
     Dispatches synchronously in the caller's thread.
     The callbacks are invoked without holding the internal lock. */
  void Publish(const std::string& name, const BusSignalValue& value);

  /* Convenience — wraps a double into BusSignalValue and calls Publish(). */
  void Publish(const std::string& name, double value);

 private:
  struct Subscription {
    std::vector<std::string> names;
    BusSubscribeFn fn;
  };

  mutable std::mutex mutex_;
  std::unordered_map<BusSubscriberId, Subscription> subscriptions_;
  std::unordered_map<std::string, std::vector<BusSubscriberId>> signal_index_;
  std::vector<BusSubscriberId> wildcard_subscribers_;
  BusSubscriberId next_id_{0};
};

}  // namespace boat::core
