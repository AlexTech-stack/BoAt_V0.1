#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/can.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class CanServiceImpl final : public boat::v1::CanService::Service {
 public:
  explicit CanServiceImpl(GatewayContext& ctx);

  grpc::Status SendCanFrame(grpc::ServerContext* context,
                            const boat::v1::SendCanFrameRequest* request,
                            boat::v1::SendCanFrameResponse* response) override;

  grpc::Status SubscribeCanFrames(grpc::ServerContext* context,
                                  const boat::v1::SubscribeCanFramesRequest* request,
                                  grpc::ServerWriter<boat::v1::CanFrame>* writer) override;

  grpc::Status ListBuses(grpc::ServerContext* context,
                         const boat::v1::ListBusesRequest* request,
                         boat::v1::ListBusesResponse* response) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
