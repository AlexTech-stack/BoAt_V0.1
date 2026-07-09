#include "metrics_service_impl.h"

#include <chrono>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

namespace boat::gateway {
namespace {
constexpr std::uint32_t kMetricEventType = 9101;

double DecodeNumeric(const std::vector<std::uint8_t>& blob) {
  if (blob.size() >= sizeof(double)) {
    double value = 0.0;
    std::memcpy(&value, blob.data(), sizeof(double));
    return value;
  }
  return 0.0;
}
}  // namespace

MetricsServiceImpl::MetricsServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status MetricsServiceImpl::GetMetrics(grpc::ServerContext*, const boat::v1::GetMetricsRequest* request,
                                            boat::v1::MetricsResponse* response) {
  boat::store::EventFilter filter;
  filter.simulation_id = request->simulation_id();
  const auto events = ctx_.event_store.Query(filter);
  for (const auto& event : events) {
    auto* point = response->add_points();
    point->set_tick(event.tick);
    point->set_name(event.signal_id);
    point->set_value(DecodeNumeric(event.value_blob));
  }
  return grpc::Status::OK;
}

grpc::Status MetricsServiceImpl::StreamMetrics(grpc::ServerContext* context, const boat::v1::StreamMetricsRequest*,
                                               grpc::ServerWriter<boat::v1::MetricPoint>* writer) {
  std::mutex metrics_mutex;
  std::vector<boat::v1::MetricPoint> pending;

  const auto handle = ctx_.sim.event_bus().Subscribe(kMetricEventType, [&](const boat::core::BusEvent& event) {
    boat::v1::MetricPoint point;
    point.set_tick(event.tick);
    if (const auto* unknown = std::get_if<boat::core::UnknownPayload>(&event.payload)) {
      if (!point.ParseFromArray(unknown->data.data(), static_cast<int>(unknown->data.size()))) {
        point.set_name("metric");
        point.set_value(0.0);
      }
    } else {
      point.set_name("metric");
      point.set_value(0.0);
    }
    std::lock_guard<std::mutex> lock(metrics_mutex);
    pending.push_back(std::move(point));
  });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::MetricPoint> local;
    {
      std::lock_guard<std::mutex> lock(metrics_mutex);
      local.swap(pending);
    }
    for (const auto& metric : local) {
      if (!writer->Write(metric)) {
        break;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  ctx_.sim.event_bus().Unsubscribe(handle);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
