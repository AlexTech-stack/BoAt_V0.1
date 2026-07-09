#include "fault_service_impl.h"

#include <algorithm>
#include <random>

namespace boat::gateway {
namespace {
std::size_t ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  return static_cast<std::size_t>(std::stoull(token));
}

std::string GenerateFaultId() {
  static std::mt19937_64 rng(std::random_device{}());
  static std::uniform_int_distribution<std::uint64_t> dist;
  return "fault-" + std::to_string(dist(rng));
}
}  // namespace

FaultServiceImpl::FaultServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status FaultServiceImpl::InjectFault(grpc::ServerContext*, const boat::v1::InjectFaultRequest* request,
                                           boat::v1::InjectFaultResponse* response) {
  boat::core::FaultSpec spec{
      .signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}(request->target())),
      .start_tick = request->tick(),
      .end_tick = request->tick() + 1,
      .type = ToCoreFaultType(request->type()),
  };
  ctx_.sim.fault_injector().Schedule(spec);

  boat::v1::FaultEvent event;
  event.set_fault_id(GenerateFaultId());
  event.set_simulation_id(request->simulation_id());
  event.set_target(request->target());
  event.set_type(request->type());
  event.set_tick(request->tick());
  {
    std::lock_guard<std::mutex> lock(faults_mutex_);
    faults_.push_back(event);
  }
  *response->mutable_fault() = event;
  return grpc::Status::OK;
}

grpc::Status FaultServiceImpl::ListFaults(grpc::ServerContext*, const boat::v1::ListFaultsRequest* request,
                                          boat::v1::ListFaultsResponse* response) {
  std::vector<boat::v1::FaultEvent> filtered;
  {
    std::lock_guard<std::mutex> lock(faults_mutex_);
    for (const auto& fault : faults_) {
      if (request->simulation_id().empty() || fault.simulation_id() == request->simulation_id()) {
        filtered.push_back(fault);
      }
    }
  }
  const std::size_t offset = ParseToken(request->page().page_token());
  const std::size_t page_size = request->page().page_size() == 0 ? filtered.size() : request->page().page_size();
  const std::size_t end = std::min(filtered.size(), offset + page_size);
  for (std::size_t i = offset; i < end; ++i) {
    *response->add_faults() = filtered[i];
  }
  response->mutable_page()->set_total_size(static_cast<std::uint32_t>(filtered.size()));
  if (end < filtered.size()) {
    response->mutable_page()->set_next_page_token(std::to_string(end));
  }
  return grpc::Status::OK;
}

boat::core::FaultType FaultServiceImpl::ToCoreFaultType(boat::v1::FaultType type) {
  switch (type) {
    case boat::v1::FAULT_TYPE_SIGNAL_CORRUPTION:
      return boat::core::FaultType::NOISE;
    case boat::v1::FAULT_TYPE_PACKET_DROP:
      return boat::core::FaultType::DROPOUT;
    case boat::v1::FAULT_TYPE_DELAY:
      return boat::core::FaultType::DELAY;
    case boat::v1::FAULT_TYPE_UNSPECIFIED:
      break;
  }
  return boat::core::FaultType::NOISE;
}

}  // namespace boat::gateway
