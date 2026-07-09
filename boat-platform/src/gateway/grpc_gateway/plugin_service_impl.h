#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/plugin.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class PluginServiceImpl final : public boat::v1::PluginService::Service {
 public:
  explicit PluginServiceImpl(GatewayContext& ctx);

  grpc::Status RegisterPlugin(grpc::ServerContext* context, const boat::v1::RegisterPluginRequest* request,
                              boat::v1::PluginResponse* response) override;
  grpc::Status ListPlugins(grpc::ServerContext* context, const boat::v1::ListPluginsRequest* request,
                           boat::v1::ListPluginsResponse* response) override;
  grpc::Status GetPluginInfo(grpc::ServerContext* context, const boat::v1::GetPluginInfoRequest* request,
                             boat::v1::PluginResponse* response) override;
  grpc::Status UnloadPlugin(grpc::ServerContext* context, const boat::v1::UnloadPluginRequest* request,
                            boat::v1::UnloadPluginResponse* response) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
