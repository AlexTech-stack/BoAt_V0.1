#pragma once

#include <mutex>
#include <string>
#include <unordered_map>

#include <grpcpp/grpcpp.h>

#include "boat/v1/replay.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class ReplayServiceImpl final : public boat::v1::ReplayService::Service {
 public:
  explicit ReplayServiceImpl(GatewayContext& ctx);

  grpc::Status StartReplay(grpc::ServerContext* context, const boat::v1::StartReplayRequest* request,
                           boat::v1::ReplayControlResponse* response) override;
  grpc::Status SeekReplay(grpc::ServerContext* context, const boat::v1::SeekReplayRequest* request,
                          boat::v1::ReplayControlResponse* response) override;
  grpc::Status StreamReplay(grpc::ServerContext* context, const boat::v1::StreamReplayRequest* request,
                            grpc::ServerWriter<boat::v1::ReplayEvent>* writer) override;
  grpc::Status PauseReplay(grpc::ServerContext* context, const boat::v1::PauseReplayRequest* request,
                           boat::v1::ReplayControlResponse* response) override;
  grpc::Status ResumeReplay(grpc::ServerContext* context, const boat::v1::ResumeReplayRequest* request,
                            boat::v1::ReplayControlResponse* response) override;
  grpc::Status StopReplay(grpc::ServerContext* context, const boat::v1::StopReplayRequest* request,
                          boat::v1::ReplayControlResponse* response) override;
  grpc::Status ImportTraceData(grpc::ServerContext* context, const boat::v1::ImportTraceDataRequest* request,
                               boat::v1::ReplayControlResponse* response) override;
  grpc::Status StartReplayFromEvents(grpc::ServerContext* context,
                                     const boat::v1::StartReplayFromEventsRequest* request,
                                     boat::v1::ReplayControlResponse* response) override;

 private:
  boat::replay::ReplaySpeed ProtoSpeedToInternal(boat::v1::ReplaySpeed proto_speed) const;

  GatewayContext& ctx_;
  std::unordered_map<std::string, boat::replay::ReplayConfig> active_replays_;
  std::mutex replay_mutex_;
};

}  // namespace boat::gateway
