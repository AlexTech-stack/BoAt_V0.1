#pragma once

#include <cstdint>
#include <functional>
#include <mutex>
#include <queue>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace boat::core {

/* Payload for types that cross layer boundaries (CAN/ETH frames, protobuf, …).
   type_tag discriminates the original type; data holds the serialized bytes. */
struct UnknownPayload {
  std::uint32_t type_tag;
  std::vector<std::uint8_t> data;
};

using BusEventPayload = std::variant<
    std::monostate,
    std::int64_t,
    double,
    std::string,
    UnknownPayload
>;

struct BusEvent {
  std::uint32_t type;
  BusEventPayload payload;
  std::uint64_t tick;
};

class EventBus {
 public:
  using HandlerFn = std::function<void(const BusEvent&)>;
  using SubscriptionHandle = std::uint64_t;

  void Publish(BusEvent event);
  SubscriptionHandle Subscribe(std::uint32_t type, HandlerFn handler);
  void Unsubscribe(SubscriptionHandle handle);
  void Dispatch();

 private:
  struct HandlerEntry {
    SubscriptionHandle handle;
    HandlerFn handler;
  };

  std::mutex mutex_;
  std::queue<BusEvent> queue_;
  std::unordered_map<std::uint32_t, std::vector<HandlerEntry>> subscribers_;
  std::unordered_map<SubscriptionHandle, std::uint32_t> handle_to_type_;
  SubscriptionHandle next_handle_{1};
};

}  // namespace boat::core
