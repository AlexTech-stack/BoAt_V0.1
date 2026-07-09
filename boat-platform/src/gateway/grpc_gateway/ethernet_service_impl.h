#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/ethernet.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class EthernetServiceImpl final : public boat::v1::EthernetService::Service {
 public:
  explicit EthernetServiceImpl(GatewayContext& ctx);

  grpc::Status SendFrame(grpc::ServerContext* context,
                         const boat::v1::SendEthernetFrameRequest* request,
                         boat::v1::SendEthernetFrameResponse* response) override;

  grpc::Status SubscribeFrames(
      grpc::ServerContext* context,
      const boat::v1::SubscribeEthernetFramesRequest* request,
      grpc::ServerWriter<boat::v1::EthernetFrame>* writer) override;

  grpc::Status ListInterfaces(
      grpc::ServerContext* context,
      const boat::v1::ListEthernetInterfacesRequest* request,
      boat::v1::ListEthernetInterfacesResponse* response) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
