#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace boat::gateway {

/* One observed RPC lifecycle event. */
struct RpcEvent {
  uint64_t    timestamp_ns  = 0;
  std::string method;           // "/boat.v1.CanService/SendCanFrame"
  std::string peer;             // "ipv4:127.0.0.1:54321"
  std::string event_type;       // CALL_START | MSG_RECV | MSG_SEND | CALL_END
  std::string call_type;        // UNARY | SERVER_STREAM | CLIENT_STREAM | BIDI_STREAM
  uint32_t    msg_bytes    = 0; // serialised bytes (MSG_RECV / MSG_SEND)
  int64_t     duration_us  = 0; // elapsed µs (CALL_END)
  int32_t     status_code  = 0; // gRPC status int (CALL_END)
  std::string status_message;
  // One-line human-readable description of the payload (service-level events).
  std::string summary;
};

/* Thread-safe ring buffer + subscriber fan-out for RPC audit events. */
class RpcAuditLog {
 public:
  using CallbackId = std::size_t;
  using Callback   = std::function<void(const RpcEvent&)>;

  explicit RpcAuditLog(std::size_t max_entries = 4000)
      : max_(max_entries) {}

  /* Push an event and fan out to all live subscribers. */
  void Push(RpcEvent ev) {
    {
      std::lock_guard<std::mutex> lock(ring_mutex_);
      if (ring_.size() >= max_) ring_.pop_front();
      ring_.push_back(ev);
    }
    // Fan out under subscriber lock (snapshot first to avoid deadlock).
    std::vector<Callback> to_call;
    {
      std::lock_guard<std::mutex> lock(subs_mutex_);
      to_call.reserve(subs_.size());
      for (auto& [id, cb] : subs_) to_call.push_back(cb);
    }
    for (auto& cb : to_call) cb(ev);
  }

  /* Subscribe to live events.  Returns an id to pass to Unsubscribe. */
  CallbackId Subscribe(Callback cb) {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    const CallbackId id = next_id_++;
    subs_[id] = std::move(cb);
    return id;
  }

  void Unsubscribe(CallbackId id) {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    subs_.erase(id);
  }

 private:
  const std::size_t max_;
  std::mutex        ring_mutex_;
  std::deque<RpcEvent> ring_;

  std::mutex subs_mutex_;
  std::unordered_map<CallbackId, Callback> subs_;
  CallbackId next_id_{0};
};

}  // namespace boat::gateway
