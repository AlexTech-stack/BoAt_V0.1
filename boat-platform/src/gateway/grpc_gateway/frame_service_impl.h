#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/frame.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class FrameServiceImpl final : public boat::v1::FrameService::Service {
 public:
  explicit FrameServiceImpl(GatewayContext& ctx);

  grpc::Status SendFrame(grpc::ServerContext* context,
                         const boat::v1::SendFrameRequest* request,
                         boat::v1::SendFrameResponse* response) override;

  grpc::Status SubscribeFrames(grpc::ServerContext* context,
                               const boat::v1::SubscribeFramesRequest* request,
                               grpc::ServerWriter<boat::v1::Frame>* writer) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
