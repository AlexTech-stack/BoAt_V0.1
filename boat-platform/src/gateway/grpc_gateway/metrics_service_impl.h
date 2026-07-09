#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/metrics.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

class MetricsServiceImpl final : public boat::v1::MetricsService::Service {
 public:
  explicit MetricsServiceImpl(GatewayContext& ctx);

  grpc::Status GetMetrics(grpc::ServerContext* context, const boat::v1::GetMetricsRequest* request,
                          boat::v1::MetricsResponse* response) override;
  grpc::Status StreamMetrics(grpc::ServerContext* context, const boat::v1::StreamMetricsRequest* request,
                             grpc::ServerWriter<boat::v1::MetricPoint>* writer) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
