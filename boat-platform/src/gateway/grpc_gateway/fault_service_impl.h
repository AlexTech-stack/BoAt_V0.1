#pragma once

#include <mutex>
#include <vector>

#include <grpcpp/grpcpp.h>

#include "boat/v1/fault.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class FaultServiceImpl final : public boat::v1::FaultService::Service {
 public:
  explicit FaultServiceImpl(GatewayContext& ctx);

  grpc::Status InjectFault(grpc::ServerContext* context, const boat::v1::InjectFaultRequest* request,
                           boat::v1::InjectFaultResponse* response) override;
  grpc::Status ListFaults(grpc::ServerContext* context, const boat::v1::ListFaultsRequest* request,
                          boat::v1::ListFaultsResponse* response) override;

 private:
  static boat::core::FaultType ToCoreFaultType(boat::v1::FaultType type);

  GatewayContext& ctx_;
  std::vector<boat::v1::FaultEvent> faults_;
  std::mutex faults_mutex_;
};

}  // namespace boat::gateway
