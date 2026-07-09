#include "debug_service_impl.h"

#include <chrono>
#include <mutex>
#include <thread>
#include <vector>

namespace boat::gateway {

grpc::Status DebugServiceImpl::StreamEvents(
    grpc::ServerContext*                        context,
    const boat::v1::StreamRpcEventsRequest*     request,
    grpc::ServerWriter<boat::v1::RpcEvent>*     writer) {

  const std::string filter = request->method_filter();

  std::mutex                    queue_mutex;
  std::vector<boat::v1::RpcEvent> queue;

  const auto sub_id = log_.Subscribe(
      [&queue_mutex, &queue, &filter](const RpcEvent& ev) {
        if (!filter.empty() &&
            ev.method.find(filter) == std::string::npos) {
          return;
        }
        boat::v1::RpcEvent proto;
        proto.set_timestamp_ns(ev.timestamp_ns);
        proto.set_method(ev.method);
        proto.set_peer(ev.peer);
        proto.set_event_type(ev.event_type);
        proto.set_call_type(ev.call_type);
        proto.set_msg_bytes(ev.msg_bytes);
        proto.set_duration_us(ev.duration_us);
        proto.set_status_code(ev.status_code);
        proto.set_status_message(ev.status_message);
        proto.set_summary(ev.summary);
        std::lock_guard<std::mutex> lock(queue_mutex);
        queue.push_back(std::move(proto));
      });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::RpcEvent> pending;
    {
      std::lock_guard<std::mutex> lock(queue_mutex);
      pending.swap(queue);
    }
    for (const auto& ev : pending) {
      if (!writer->Write(ev)) {
        log_.Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  log_.Unsubscribe(sub_id);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
