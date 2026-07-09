#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/debug.grpc.pb.h"
#include "rpc_audit_log.h"

namespace boat::gateway {

class DebugServiceImpl final : public boat::v1::DebugService::Service {
 public:
  explicit DebugServiceImpl(RpcAuditLog& log) : log_(log) {}

  grpc::Status StreamEvents(
      grpc::ServerContext*                            context,
      const boat::v1::StreamRpcEventsRequest*         request,
      grpc::ServerWriter<boat::v1::RpcEvent>*         writer) override;

 private:
  RpcAuditLog& log_;
};

}  // namespace boat::gateway
