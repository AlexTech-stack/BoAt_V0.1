#include "signal_service_impl.h"

#include <algorithm>
#include <chrono>
#include <mutex>
#include <optional>
#include <thread>
#include <vector>

namespace boat::gateway {
namespace {

std::size_t ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  return static_cast<std::size_t>(std::stoull(token));
}

boat::v1::SignalValue ToProtoSignal(const boat::store::EventRecord& record) {
  boat::v1::SignalValue value;
  value.set_name(record.signal_id);
  value.set_tick(record.tick);
  value.set_string_value(std::string(record.value_blob.begin(), record.value_blob.end()));
  return value;
}

}  // namespace

SignalServiceImpl::SignalServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status SignalServiceImpl::InjectSignal(grpc::ServerContext*, const boat::v1::InjectSignalRequest* request,
                                             boat::v1::InjectSignalResponse* response) {
  boat::core::SignalEvent event{};
  event.signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}(request->signal().name()));
  event.tick = request->signal().tick();
  switch (request->signal().value_case()) {
    case boat::v1::SignalValue::kNumberValue:
      event.value = request->signal().number_value();
      break;
    case boat::v1::SignalValue::kIntValue:
      event.value = request->signal().int_value();
      break;
    case boat::v1::SignalValue::kBoolValue:
      event.value = request->signal().bool_value();
      break;
    case boat::v1::SignalValue::kStringValue:
      event.value = request->signal().string_value();
      break;
    case boat::v1::SignalValue::VALUE_NOT_SET:
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "signal value is required");
  }
  ctx_.sim.signal_router().Publish(event);
  response->set_accepted(true);
  return grpc::Status::OK;
}

grpc::Status SignalServiceImpl::SubscribeSignals(grpc::ServerContext* context,
                                                 const boat::v1::SubscribeSignalsRequest* request,
                                                 grpc::ServerWriter<boat::v1::SignalValue>* writer) {
  std::mutex queue_mutex;
  std::vector<boat::v1::SignalValue> queue;
  std::vector<boat::core::SignalRouter::SubscriptionHandle> handles;
  handles.reserve(request->names_size());

  for (const auto& name : request->names()) {
    const std::uint64_t signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}(name));
    boat::core::FilterPredicate predicate{
        .signal_id = signal_id,
        .tick_min = std::nullopt,
        .tick_max = std::nullopt,
        .comparator = [name](const boat::core::SignalEvent&) { return !name.empty(); },
    };
    auto handle = ctx_.sim.signal_router().Subscribe(
        signal_id, std::move(predicate),
        [&queue_mutex, &queue, name](const boat::core::SignalEvent& event) {
          boat::v1::SignalValue value;
          value.set_name(name);
          value.set_tick(event.tick);
          if (const auto* number = std::get_if<double>(&event.value)) {
            value.set_number_value(*number);
          } else if (const auto* integer = std::get_if<std::int64_t>(&event.value)) {
            value.set_int_value(*integer);
          } else if (const auto* boolean = std::get_if<bool>(&event.value)) {
            value.set_bool_value(*boolean);
          } else if (const auto* text = std::get_if<std::string>(&event.value)) {
            value.set_string_value(*text);
          }
          std::lock_guard<std::mutex> lock(queue_mutex);
          queue.push_back(std::move(value));
        });
    handles.push_back(handle);
  }

  while (!context->IsCancelled()) {
    std::vector<boat::v1::SignalValue> pending;
    {
      std::lock_guard<std::mutex> lock(queue_mutex);
      pending.swap(queue);
    }
    for (const auto& value : pending) {
      if (!writer->Write(value)) {
        break;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  for (const auto handle : handles) {
    ctx_.sim.signal_router().Unsubscribe(handle);
  }
  return grpc::Status::OK;
}

grpc::Status SignalServiceImpl::GetSignalHistory(grpc::ServerContext*, const boat::v1::GetSignalHistoryRequest* request,
                                                 boat::v1::SignalHistoryResponse* response) {
  boat::store::EventFilter filter;
  filter.simulation_id = request->simulation_id();
  filter.signal_id = request->name();
  const auto events = ctx_.event_store.Query(filter);
  const std::size_t offset = ParseToken(request->page().page_token());
  const std::size_t page_size = request->page().page_size() == 0 ? events.size() : request->page().page_size();
  const std::size_t end = std::min(events.size(), offset + page_size);
  for (std::size_t i = offset; i < end; ++i) {
    *response->add_values() = ToProtoSignal(events[i]);
  }
  response->mutable_page()->set_total_size(static_cast<std::uint32_t>(events.size()));
  if (end < events.size()) {
    response->mutable_page()->set_next_page_token(std::to_string(end));
  }
  return grpc::Status::OK;
}

}  // namespace boat::gateway
