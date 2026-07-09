#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <string_view>

#include <grpcpp/grpcpp.h>
#include <grpcpp/support/server_interceptor.h>

#include "rpc_audit_log.h"

namespace boat::gateway {

/* ── Helper ───────────────────────────────────────────────────────────────── */

inline uint64_t NowNs() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count());
}

inline std::string_view CallTypeName(
    grpc::experimental::ServerRpcInfo::Type t) {
  using T = grpc::experimental::ServerRpcInfo::Type;
  switch (t) {
    case T::UNARY:          return "UNARY";
    case T::SERVER_STREAMING: return "SERVER_STREAM";
    case T::CLIENT_STREAMING: return "CLIENT_STREAM";
    case T::BIDI_STREAMING:   return "BIDI_STREAM";
    default:                  return "UNKNOWN";
  }
}

/* ── Per-call interceptor ────────────────────────────────────────────────── */

class RpcAuditInterceptor final
    : public grpc::experimental::Interceptor {
 public:
  RpcAuditInterceptor(RpcAuditLog&                        log,
                      grpc::experimental::ServerRpcInfo*  info)
      : log_(log),
        info_(info),
        method_(info->method()),
        call_type_(std::string(CallTypeName(info->type()))),
        t0_(std::chrono::steady_clock::now()),
        // Skip intercepting the DebugService itself to avoid feedback loops.
        skip_(std::string_view(info->method()).find("DebugService") !=
              std::string_view::npos) {}

  void Intercept(
      grpc::experimental::InterceptorBatchMethods* methods) override {
    if (skip_) { methods->Proceed(); return; }
    namespace H = grpc::experimental;
    using Hook  = H::InterceptionHookPoints;

    if (methods->QueryInterceptionHookPoint(
            Hook::POST_RECV_INITIAL_METADATA)) {
      // First metadata received from client → call has started.
      RpcEvent ev;
      ev.timestamp_ns = NowNs();
      ev.method       = method_;
      ev.peer         = info_->server_context()->peer();
      ev.event_type   = "CALL_START";
      ev.call_type    = call_type_;
      log_.Push(std::move(ev));
    }

    if (methods->QueryInterceptionHookPoint(Hook::POST_RECV_MESSAGE)) {
      // A message was received from the client.
      uint32_t bytes = 0;
      if (auto* msg = methods->GetRecvMessage()) {
        auto* pb = static_cast<const google::protobuf::MessageLite*>(msg);
        bytes = static_cast<uint32_t>(pb->ByteSizeLong());
      }
      RpcEvent ev;
      ev.timestamp_ns = NowNs();
      ev.method       = method_;
      ev.peer         = info_->server_context()->peer();
      ev.event_type   = "MSG_RECV";
      ev.call_type    = call_type_;
      ev.msg_bytes    = bytes;
      log_.Push(std::move(ev));
    }

    if (methods->QueryInterceptionHookPoint(Hook::PRE_SEND_MESSAGE)) {
      // A message is about to be sent to the client.
      uint32_t bytes = 0;
      if (auto* msg = methods->GetSendMessage()) {
        auto* pb = static_cast<const google::protobuf::MessageLite*>(msg);
        bytes = static_cast<uint32_t>(pb->ByteSizeLong());
      }
      RpcEvent ev;
      ev.timestamp_ns = NowNs();
      ev.method       = method_;
      ev.peer         = info_->server_context()->peer();
      ev.event_type   = "MSG_SEND";
      ev.call_type    = call_type_;
      ev.msg_bytes    = bytes;
      log_.Push(std::move(ev));
    }

    if (methods->QueryInterceptionHookPoint(Hook::PRE_SEND_STATUS)) {
      // Call is ending — capture duration and status.
      auto dur = std::chrono::duration_cast<std::chrono::microseconds>(
                     std::chrono::steady_clock::now() - t0_)
                     .count();
      grpc::Status st = methods->GetSendStatus();
      RpcEvent ev;
      ev.timestamp_ns   = NowNs();
      ev.method         = method_;
      ev.peer           = info_->server_context()->peer();
      ev.event_type     = "CALL_END";
      ev.call_type      = call_type_;
      ev.duration_us    = dur;
      ev.status_code    = static_cast<int32_t>(st.error_code());
      ev.status_message = st.error_message();
      log_.Push(std::move(ev));
    }

    methods->Proceed();
  }

 private:
  RpcAuditLog&                        log_;
  grpc::experimental::ServerRpcInfo*  info_;
  std::string                         method_;
  std::string                         call_type_;
  std::chrono::steady_clock::time_point t0_;
  bool                                skip_;
};

/* ── Factory (registered with ServerBuilder) ─────────────────────────────── */

class RpcAuditInterceptorFactory final
    : public grpc::experimental::ServerInterceptorFactoryInterface {
 public:
  explicit RpcAuditInterceptorFactory(RpcAuditLog& log) : log_(log) {}

  grpc::experimental::Interceptor* CreateServerInterceptor(
      grpc::experimental::ServerRpcInfo* info) override {
    return new RpcAuditInterceptor(log_, info);
  }

 private:
  RpcAuditLog& log_;
};

}  // namespace boat::gateway
