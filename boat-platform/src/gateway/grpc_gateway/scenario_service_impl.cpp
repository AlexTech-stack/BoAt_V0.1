#include "scenario_service_impl.h"

#include <algorithm>
#include <exception>
#include <vector>

namespace boat::gateway {
namespace {
std::size_t ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  return static_cast<std::size_t>(std::stoull(token));
}
}  // namespace

ScenarioServiceImpl::ScenarioServiceImpl(GatewayContext& ctx) : ctx_(ctx), config_store_("boat_config.db") {}

grpc::Status ScenarioServiceImpl::CreateScenario(grpc::ServerContext*, const boat::v1::CreateScenarioRequest* request,
                                                 boat::v1::ScenarioResponse* response) {
  if (request->scenario().content().empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "scenario content must be non-empty");
  }
  {
    std::lock_guard<std::mutex> lock(scenarios_mutex_);
    scenarios_[request->scenario().scenario_id()] = request->scenario();
  }
  config_store_.Set("scenario." + request->scenario().scenario_id(), request->scenario().content());
  *response->mutable_scenario() = request->scenario();
  return grpc::Status::OK;
}

grpc::Status ScenarioServiceImpl::GetScenario(grpc::ServerContext*, const boat::v1::GetScenarioRequest* request,
                                              boat::v1::ScenarioResponse* response) {
  std::lock_guard<std::mutex> lock(scenarios_mutex_);
  const auto it = scenarios_.find(request->scenario_id());
  if (it == scenarios_.end()) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "scenario not found");
  }
  *response->mutable_scenario() = it->second;
  return grpc::Status::OK;
}

grpc::Status ScenarioServiceImpl::ListScenarios(grpc::ServerContext*, const boat::v1::ListScenariosRequest* request,
                                                boat::v1::ListScenariosResponse* response) {
  std::vector<boat::v1::Scenario> values;
  {
    std::lock_guard<std::mutex> lock(scenarios_mutex_);
    values.reserve(scenarios_.size());
    for (const auto& [_, scenario] : scenarios_) {
      values.push_back(scenario);
    }
  }
  const std::size_t offset = ParseToken(request->page().page_token());
  const std::size_t page_size = request->page().page_size() == 0 ? values.size() : request->page().page_size();
  const std::size_t end = std::min(values.size(), offset + page_size);
  for (std::size_t i = offset; i < end; ++i) {
    *response->add_scenarios() = values[i];
  }
  response->mutable_page()->set_total_size(static_cast<std::uint32_t>(values.size()));
  if (end < values.size()) {
    response->mutable_page()->set_next_page_token(std::to_string(end));
  }
  return grpc::Status::OK;
}

grpc::Status ScenarioServiceImpl::ValidateScenario(grpc::ServerContext*, const boat::v1::ValidateScenarioRequest* request,
                                                   boat::v1::ValidateScenarioResponse* response) {
  try {
    (void)boat::core::ScenarioLoader::LoadFromJson(request->content());
    response->set_valid(true);
  } catch (const std::exception& ex) {
    response->set_valid(false);
    response->add_issues(ex.what());
  }
  return grpc::Status::OK;
}

grpc::Status ScenarioServiceImpl::DeleteScenario(grpc::ServerContext*, const boat::v1::DeleteScenarioRequest* request,
                                                 boat::v1::DeleteScenarioResponse* response) {
  std::lock_guard<std::mutex> lock(scenarios_mutex_);
  response->set_deleted(scenarios_.erase(request->scenario_id()) > 0);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
