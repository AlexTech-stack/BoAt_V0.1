#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/trace.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class TraceServiceImpl final : public boat::v1::TraceService::Service {
 public:
  explicit TraceServiceImpl(GatewayContext& ctx);

  grpc::Status GetTrace(grpc::ServerContext* context, const boat::v1::GetTraceRequest* request,
                        boat::v1::TraceResponse* response) override;
  grpc::Status ListTraces(grpc::ServerContext* context, const boat::v1::ListTracesRequest* request,
                          boat::v1::TraceResponse* response) override;
  grpc::Status StreamTrace(grpc::ServerContext* context, const boat::v1::StreamTraceRequest* request,
                           grpc::ServerWriter<boat::v1::TraceEvent>* writer) override;
  grpc::Status MarkStep(grpc::ServerContext* context,
                        const boat::v1::MarkStepRequest* request,
                        boat::v1::MarkStepResponse* response) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
