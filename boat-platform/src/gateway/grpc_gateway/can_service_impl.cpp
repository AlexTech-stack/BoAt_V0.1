#include "can_service_impl.h"

#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <sstream>
#include <thread>
#include <vector>

#include "can_bus_registry.h"
#include "rpc_audit_log.h"

namespace boat::gateway {

CanServiceImpl::CanServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

// ── helpers ───────────────────────────────────────────────────────────────────

static std::string CanFrameSummary(const boat::hil::CanFrame& f,
                                   const std::string& iface) {
  std::ostringstream ss;
  const bool fd = (f.flags & boat::hil::kCanFdFlagFdf) != 0;
  ss << iface
     << "  0x" << std::hex << std::uppercase << f.can_id
     << std::dec << "  [" << static_cast<int>(f.dlc) << "]";
  if (fd) ss << " FD";
  if (f.dlc > 0) {
    ss << "  ";
    for (std::uint8_t i = 0; i < f.dlc && i < 64; ++i) {
      if (i) ss << ':';
      const auto b = static_cast<unsigned>(f.data[i]);
      ss << std::hex << std::uppercase;
      if (b < 16) ss << '0';
      ss << b;
    }
  }
  return ss.str();
}

// ── handlers ──────────────────────────────────────────────────────────────────

grpc::Status CanServiceImpl::SendCanFrame(grpc::ServerContext* context,
                                          const boat::v1::SendCanFrameRequest* request,
                                          boat::v1::SendCanFrameResponse* response) {
  const auto& f = request->frame();
  const bool is_fd = (f.flags() & boat::hil::kCanFdFlagFdf) != 0;
  const std::size_t max_len = is_fd ? 64u : 8u;

  std::size_t byte_count = f.dlc() > 0 ? static_cast<std::size_t>(f.dlc())
                                        : f.data().size();
  byte_count = std::min(byte_count, max_len);

  boat::hil::CanFrame frame{};
  frame.can_id       = f.can_id();
  frame.dlc          = static_cast<std::uint8_t>(byte_count);
  frame.flags        = static_cast<std::uint8_t>(f.flags());
  frame.timestamp_ns = f.timestamp_ns();
  std::memset(frame.data, 0, sizeof(frame.data));
  std::memcpy(frame.data, f.data().data(), std::min(f.data().size(), byte_count));

  const std::string& iface = f.iface();
  if (iface.empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "iface is required; specify a CAN interface name");
  }
  const bool accepted = ctx_.can_bus_registry.SendFrame(iface, frame);
  if (!accepted) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND,
                        "CAN interface not registered: " + iface);
  }

  // Audit: log the frame content with the peer who sent it.
  RpcEvent ev;
  ev.timestamp_ns = static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
  ev.method     = "CanService/SendCanFrame";
  ev.peer       = context->peer();
  ev.event_type = "DATA";
  ev.call_type  = "UNARY";
  ev.summary    = CanFrameSummary(frame, iface);
  ctx_.audit_log.Push(std::move(ev));

  response->set_accepted(accepted);
  return grpc::Status::OK;
}

grpc::Status CanServiceImpl::SubscribeCanFrames(
    grpc::ServerContext* context,
    const boat::v1::SubscribeCanFramesRequest* request,
    grpc::ServerWriter<boat::v1::CanFrame>* writer) {

  const std::string iface_filter = request->iface();

  if (!iface_filter.empty() && !ctx_.can_bus_registry.Has(iface_filter)) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND,
                        "CAN interface not registered: " + iface_filter);
  }

  const std::string peer = context->peer();

  // Audit: subscription opened.
  {
    RpcEvent ev;
    ev.timestamp_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    ev.method     = "CanService/SubscribeCanFrames";
    ev.peer       = peer;
    ev.event_type = "SUBSCRIBE_OPEN";
    ev.call_type  = "SERVER_STREAM";
    ev.summary    = "filter=" + (iface_filter.empty() ? std::string("(all)") : iface_filter);
    ctx_.audit_log.Push(std::move(ev));
  }

  std::mutex queue_mutex;
  std::vector<boat::v1::CanFrame> queue;

  const auto sub_id = ctx_.can_bus_registry.Subscribe(
      iface_filter,
      [&queue_mutex, &queue](const boat::hil::CanFrame& f, const std::string& iface) {
        boat::v1::CanFrame proto;
        proto.set_can_id(f.can_id);
        proto.set_dlc(f.dlc);
        proto.set_data(f.data, f.dlc);
        proto.set_timestamp_ns(f.timestamp_ns);
        proto.set_iface(iface);
        proto.set_flags(f.flags);
        std::lock_guard<std::mutex> lock(queue_mutex);
        queue.push_back(std::move(proto));
      });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::CanFrame> pending;
    {
      std::lock_guard<std::mutex> lock(queue_mutex);
      pending.swap(queue);
    }
    for (const auto& proto : pending) {
      if (!writer->Write(proto)) {
        ctx_.can_bus_registry.Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
      // Audit: frame delivered to this subscriber.
      boat::hil::CanFrame raw{};
      raw.can_id = proto.can_id();
      raw.dlc    = static_cast<std::uint8_t>(proto.dlc());
      raw.flags  = static_cast<std::uint8_t>(proto.flags());
      const auto& d = proto.data();
      std::memcpy(raw.data, d.data(), std::min(d.size(), std::size_t{64}));
      RpcEvent ev;
      ev.timestamp_ns = static_cast<uint64_t>(
          std::chrono::duration_cast<std::chrono::nanoseconds>(
              std::chrono::system_clock::now().time_since_epoch()).count());
      ev.method     = "CanService/SubscribeCanFrames";
      ev.peer       = peer;
      ev.event_type = "DATA";
      ev.call_type  = "SERVER_STREAM";
      ev.summary    = CanFrameSummary(raw, proto.iface());
      ctx_.audit_log.Push(std::move(ev));
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  ctx_.can_bus_registry.Unsubscribe(sub_id);
  return grpc::Status::OK;
}

grpc::Status CanServiceImpl::ListBuses(grpc::ServerContext*,
                                       const boat::v1::ListBusesRequest*,
                                       boat::v1::ListBusesResponse* response) {
  for (const auto& iface : ctx_.can_bus_registry.Interfaces()) {
    const auto& info = ctx_.can_bus_registry.GetInterfaceInfo(iface);
    auto* proto = response->add_buses();
    proto->set_iface(iface);
    proto->set_driver(info.driver_name);
    proto->set_state(info.state);
    proto->set_fd_support(info.fd_support);
    proto->set_bitrate(info.bitrate);
  }
  return grpc::Status::OK;
}

}  // namespace boat::gateway
