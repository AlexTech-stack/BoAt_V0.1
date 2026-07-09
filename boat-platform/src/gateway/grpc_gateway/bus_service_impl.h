#pragma once

#include <cstddef>
#include <string>
#include <vector>

#include <grpcpp/grpcpp.h>

#include "boat/v1/bus.grpc.pb.h"
#include "core/signal/signal_bus.h"
#include "rpc_audit_log.h"

namespace boat::gateway {

/* Always-on signal bus gRPC service.
   Thin wrapper around core::SignalBus — converts protobuf ↔ core types and
   performs audit logging.  The core bus manages subscriptions and dispatch. */
class BusServiceImpl final : public boat::v1::BusService::Service {
 public:
  BusServiceImpl(RpcAuditLog& log, boat::core::SignalBus& signal_bus)
      : audit_log_(log), signal_bus_(signal_bus) {}

  grpc::Status Publish(grpc::ServerContext* context,
                       const boat::v1::BusPublishRequest* request,
                       boat::v1::BusPublishResponse* response) override;

  grpc::Status Subscribe(grpc::ServerContext* context,
                         const boat::v1::BusSubscribeRequest* request,
                         grpc::ServerWriter<boat::v1::BusSignal>* writer) override;

 private:
  RpcAuditLog& audit_log_;
  boat::core::SignalBus& signal_bus_;
};

}  // namespace boat::gateway
