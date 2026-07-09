#include "bus_service_impl.h"

#include <chrono>
#include <cstdint>
#include <mutex>
#include <sstream>
#include <thread>
#include <vector>

namespace boat::gateway {

// ── Helpers ──────────────────────────────────────────────────────────────────

static uint64_t NowNsBus() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
}

static std::string SignalSummary(const boat::v1::BusSignal& sig) {
  using VC = boat::v1::BusSignal::ValueCase;
  std::ostringstream ss;
  ss << sig.name() << " = ";
  switch (sig.value_case()) {
    case VC::kNumberValue: ss << sig.number_value(); break;
    case VC::kStringValue: ss << '"' << sig.string_value() << '"'; break;
    case VC::kBoolValue:   ss << (sig.bool_value() ? "true" : "false"); break;
    case VC::kBytesValue: {
      const auto& b = sig.bytes_value();
      for (std::size_t i = 0; i < b.size() && i < 8; ++i) {
        if (i) ss << ':';
        const auto v = static_cast<unsigned>(static_cast<uint8_t>(b[i]));
        if (v < 16) ss << '0';
        ss << std::hex << std::uppercase << v;
      }
      if (b.size() > 8) ss << "...";
      break;
    }
    default: ss << "(empty)"; break;
  }
  if (!sig.publisher().empty()) ss << "  (pub: " << sig.publisher() << ')';
  return ss.str();
}

static boat::core::BusSignalValue BusValueFromProto(const boat::v1::BusSignal& sig) {
  using VC = boat::v1::BusSignal::ValueCase;
  switch (sig.value_case()) {
    case VC::kNumberValue: return sig.number_value();
    case VC::kBoolValue:   return sig.bool_value();
    case VC::kBytesValue: {
      const auto& b = sig.bytes_value();
      return std::vector<std::uint8_t>(b.begin(), b.end());
    }
    case VC::kStringValue: return sig.string_value();
    default:               return 0.0;
  }
}

static void ProtoFromBusSignal(const boat::core::BusSignal& core_sig,
                               boat::v1::BusSignal& out) {
  out.set_name(core_sig.name);
  out.set_timestamp_ns(NowNsBus());
  std::visit([&](const auto& v) {
    using T = std::decay_t<decltype(v)>;
    if constexpr (std::is_same_v<T, double>) {
      out.set_number_value(v);
    } else if constexpr (std::is_same_v<T, std::int64_t>) {
      out.set_number_value(static_cast<double>(v));
    } else if constexpr (std::is_same_v<T, bool>) {
      out.set_bool_value(v);
    } else if constexpr (std::is_same_v<T, std::vector<std::uint8_t>>) {
      out.set_bytes_value(v.data(), v.size());
    } else if constexpr (std::is_same_v<T, std::string>) {
      out.set_string_value(v);
    }
  }, core_sig.value);
}

// ── RPC handlers ─────────────────────────────────────────────────────────────

grpc::Status BusServiceImpl::Publish(grpc::ServerContext* context,
                                     const boat::v1::BusPublishRequest* request,
                                     boat::v1::BusPublishResponse* response) {
  if (request->signal().name().empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "signal name required");
  }

  // Stamp wall-clock time if the publisher didn't set one.
  auto signal = request->signal();
  if (signal.timestamp_ns() == 0) {
    signal.set_timestamp_ns(NowNsBus());
  }

  signal_bus_.Publish(signal.name(), BusValueFromProto(signal));

  RpcEvent ev;
  ev.timestamp_ns = NowNsBus();
  ev.method     = "BusService/Publish";
  ev.peer       = context->peer();
  ev.event_type = "DATA";
  ev.call_type  = "UNARY";
  ev.summary    = SignalSummary(signal);
  audit_log_.Push(std::move(ev));

  response->set_accepted(true);
  return grpc::Status::OK;
}

grpc::Status BusServiceImpl::Subscribe(
    grpc::ServerContext* context,
    const boat::v1::BusSubscribeRequest* request,
    grpc::ServerWriter<boat::v1::BusSignal>* writer) {

  const std::string peer = context->peer();
  std::vector<std::string> names(request->names().begin(), request->names().end());

  // Audit: subscription opened.
  {
    std::ostringstream ss;
    if (names.empty()) {
      ss << "filter=(all)";
    } else {
      ss << "filter=";
      for (std::size_t i = 0; i < names.size(); ++i) {
        if (i) ss << ',';
        ss << names[i];
      }
    }
    RpcEvent ev;
    ev.timestamp_ns = NowNsBus();
    ev.method     = "BusService/Subscribe";
    ev.peer       = peer;
    ev.event_type = "SUBSCRIBE_OPEN";
    ev.call_type  = "SERVER_STREAM";
    ev.summary    = ss.str();
    audit_log_.Push(std::move(ev));
  }

  std::mutex queue_mutex;
  std::vector<boat::v1::BusSignal> queue;

  const auto sub_id = signal_bus_.Subscribe(
      names,
      [&queue_mutex, &queue](const boat::core::BusSignal& core_sig) {
        boat::v1::BusSignal proto_sig;
        ProtoFromBusSignal(core_sig, proto_sig);
        std::lock_guard<std::mutex> lock(queue_mutex);
        queue.push_back(std::move(proto_sig));
      });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::BusSignal> pending;
    {
      std::lock_guard<std::mutex> lock(queue_mutex);
      pending.swap(queue);
    }
    for (const auto& sig : pending) {
      if (!writer->Write(sig)) {
        signal_bus_.Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
      RpcEvent ev;
      ev.timestamp_ns = NowNsBus();
      ev.method     = "BusService/Subscribe";
      ev.peer       = peer;
      ev.event_type = "DATA";
      ev.call_type  = "SERVER_STREAM";
      ev.summary    = SignalSummary(sig);
      audit_log_.Push(std::move(ev));
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  signal_bus_.Unsubscribe(sub_id);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
