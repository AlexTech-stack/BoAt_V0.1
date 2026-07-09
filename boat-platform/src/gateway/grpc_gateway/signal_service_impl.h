#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/signal.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class SignalServiceImpl final : public boat::v1::SignalService::Service {
 public:
  explicit SignalServiceImpl(GatewayContext& ctx);

  grpc::Status InjectSignal(grpc::ServerContext* context, const boat::v1::InjectSignalRequest* request,
                            boat::v1::InjectSignalResponse* response) override;
  grpc::Status SubscribeSignals(grpc::ServerContext* context, const boat::v1::SubscribeSignalsRequest* request,
                                grpc::ServerWriter<boat::v1::SignalValue>* writer) override;
  grpc::Status GetSignalHistory(grpc::ServerContext* context, const boat::v1::GetSignalHistoryRequest* request,
                                boat::v1::SignalHistoryResponse* response) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
