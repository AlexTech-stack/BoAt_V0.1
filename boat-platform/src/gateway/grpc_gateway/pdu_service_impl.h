#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/pdu.grpc.pb.h"
#include "core/pdu_router_interface.h"
#include "gateway_context.h"

namespace boat::gateway {

class PduServiceImpl final : public boat::v1::PduService::Service {
 public:
  explicit PduServiceImpl(GatewayContext& ctx);

  grpc::Status SendPdu(grpc::ServerContext* context,
                       const boat::v1::SendPduRequest* request,
                       boat::v1::SendPduResponse* response) override;

  grpc::Status SubscribePdus(grpc::ServerContext* context,
                               const boat::v1::SubscribePdusRequest* request,
                               grpc::ServerWriter<boat::v1::PduFrame>* writer) override;

  grpc::Status ConfigureRoute(grpc::ServerContext* context,
                                const boat::v1::ConfigureRouteRequest* request,
                                boat::v1::ConfigureRouteResponse* response) override;

  grpc::Status ListRoutes(grpc::ServerContext* context,
                           const boat::v1::ListRoutesRequest* request,
                           boat::v1::ListRoutesResponse* response) override;

  grpc::Status ConfigureContainer(
      grpc::ServerContext* context,
      const boat::v1::ConfigureContainerRequest* request,
      boat::v1::ConfigureContainerResponse* response) override;

  grpc::Status ConfigureGroup(
      grpc::ServerContext* context,
      const boat::v1::ConfigureGroupRequest* request,
      boat::v1::ConfigureGroupResponse* response) override;

  grpc::Status EnableGroup(
      grpc::ServerContext* context,
      const boat::v1::EnableGroupRequest* request,
      boat::v1::EnableGroupResponse* response) override;

  grpc::Status DisableGroup(
      grpc::ServerContext* context,
      const boat::v1::DisableGroupRequest* request,
      boat::v1::DisableGroupResponse* response) override;

  grpc::Status ListGroups(
      grpc::ServerContext* context,
      const boat::v1::ListGroupsRequest* request,
      boat::v1::ListGroupsResponse* response) override;

  grpc::Status RemoveRoute(
      grpc::ServerContext* context,
      const boat::v1::RemoveRouteRequest* request,
      boat::v1::RemoveRouteResponse* response) override;

 private:
  GatewayContext& ctx_;
  boat::core::IPduRouter* GetRouter();
};

}  // namespace boat::gateway
