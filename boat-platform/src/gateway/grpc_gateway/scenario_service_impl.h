#pragma once

#include <mutex>
#include <string>
#include <unordered_map>

#include <grpcpp/grpcpp.h>

#include "boat/v1/scenario.grpc.pb.h"
#include "gateway_context.h"
#include "store/config_store/config_store.h"

namespace boat::gateway {

class ScenarioServiceImpl final : public boat::v1::ScenarioService::Service {
 public:
  explicit ScenarioServiceImpl(GatewayContext& ctx);

  grpc::Status CreateScenario(grpc::ServerContext* context, const boat::v1::CreateScenarioRequest* request,
                              boat::v1::ScenarioResponse* response) override;
  grpc::Status GetScenario(grpc::ServerContext* context, const boat::v1::GetScenarioRequest* request,
                           boat::v1::ScenarioResponse* response) override;
  grpc::Status ListScenarios(grpc::ServerContext* context, const boat::v1::ListScenariosRequest* request,
                             boat::v1::ListScenariosResponse* response) override;
  grpc::Status ValidateScenario(grpc::ServerContext* context, const boat::v1::ValidateScenarioRequest* request,
                                boat::v1::ValidateScenarioResponse* response) override;
  grpc::Status DeleteScenario(grpc::ServerContext* context, const boat::v1::DeleteScenarioRequest* request,
                              boat::v1::DeleteScenarioResponse* response) override;

 private:
  GatewayContext& ctx_;
  boat::store::SqliteTomlConfigStore config_store_;
  std::unordered_map<std::string, boat::v1::Scenario> scenarios_;
  std::mutex scenarios_mutex_;
};

}  // namespace boat::gateway
