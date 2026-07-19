#include "device_service_impl.h"

#include <chrono>
#include <mutex>
#include <thread>
#include <unordered_set>
#include <vector>

namespace boat::gateway {
namespace {

boat::v1::DeviceKind ToProtoKind(boat::core::DeviceKind k) {
  switch (k) {
    case boat::core::DeviceKind::PowerSupply:
      return boat::v1::DEVICE_KIND_POWER_SUPPLY;
    case boat::core::DeviceKind::Relay:
      return boat::v1::DEVICE_KIND_RELAY;
    case boat::core::DeviceKind::Generator:
      return boat::v1::DEVICE_KIND_GENERATOR;
    case boat::core::DeviceKind::GenericIo:
      return boat::v1::DEVICE_KIND_GENERIC_IO;
    case boat::core::DeviceKind::Unspecified:
      return boat::v1::DEVICE_KIND_UNSPECIFIED;
  }
  return boat::v1::DEVICE_KIND_UNSPECIFIED;
}

void FillDeviceInfo(const boat::core::DeviceDescriptor& d,
                    boat::v1::DeviceInfo* out) {
  out->set_device_id(d.device_id);
  out->set_kind(ToProtoKind(d.kind));
  for (const auto& c : d.channels) {
    auto* ch = out->add_channels();
    ch->set_name(c.name);
    ch->set_settable(c.settable);
    ch->set_readable(c.readable);
    ch->set_has_value(c.has_value);
    ch->set_value(c.value);
    ch->set_unit(c.unit);
  }
}

}  // namespace

DeviceServiceImpl::DeviceServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

boat::core::IDeviceManager* DeviceServiceImpl::GetManager() {
  return static_cast<boat::core::IDeviceManager*>(
      ctx_.plugin_manager.FindService("device_manager"));
}

grpc::Status DeviceServiceImpl::ListDevices(
    grpc::ServerContext*, const boat::v1::ListDevicesRequest*,
    boat::v1::ListDevicesResponse* response) {
  auto* mgr = GetManager();
  if (mgr == nullptr) {
    return grpc::Status(grpc::StatusCode::UNAVAILABLE,
                        "device_manager plugin is not loaded");
  }
  for (const auto& d : mgr->ListDevices()) {
    FillDeviceInfo(d, response->add_devices());
  }
  return grpc::Status::OK;
}

grpc::Status DeviceServiceImpl::SetControl(
    grpc::ServerContext*, const boat::v1::SetControlRequest* request,
    boat::v1::SetControlResponse* response) {
  auto* mgr = GetManager();
  if (mgr == nullptr) {
    return grpc::Status(grpc::StatusCode::UNAVAILABLE,
                        "device_manager plugin is not loaded");
  }
  std::string err;
  const bool ok = mgr->SetControl(request->device_id(), request->channel(),
                                  request->value(), err);
  response->set_accepted(ok);
  if (!ok) {
    response->mutable_error()->set_message(err);
  }
  return grpc::Status::OK;
}

grpc::Status DeviceServiceImpl::ReadState(
    grpc::ServerContext*, const boat::v1::ReadStateRequest* request,
    boat::v1::ReadStateResponse* response) {
  auto* mgr = GetManager();
  if (mgr == nullptr) {
    return grpc::Status(grpc::StatusCode::UNAVAILABLE,
                        "device_manager plugin is not loaded");
  }
  boat::core::DeviceDescriptor d;
  const bool found = mgr->GetDevice(request->device_id(), d);
  response->set_found(found);
  if (found) {
    FillDeviceInfo(d, response->mutable_device());
  }
  return grpc::Status::OK;
}

grpc::Status DeviceServiceImpl::StreamState(
    grpc::ServerContext* context, const boat::v1::StreamStateRequest* request,
    grpc::ServerWriter<boat::v1::DeviceStateUpdate>* writer) {
  auto* mgr = GetManager();
  if (mgr == nullptr) {
    return grpc::Status(grpc::StatusCode::UNAVAILABLE,
                        "device_manager plugin is not loaded");
  }

  std::unordered_set<std::string> allow(request->device_ids().begin(),
                                        request->device_ids().end());

  std::mutex queue_mutex;
  std::vector<boat::v1::DeviceStateUpdate> queue;

  const auto sub = mgr->SubscribeState(
      [&queue_mutex, &queue, allow](const std::string& device_id,
                                    const std::string& channel, double value,
                                    std::uint64_t ts_ns) {
        if (!allow.empty() && allow.find(device_id) == allow.end()) return;
        boat::v1::DeviceStateUpdate u;
        u.set_device_id(device_id);
        u.set_channel(channel);
        u.set_value(value);
        u.set_timestamp_ns(ts_ns);
        std::lock_guard<std::mutex> lock(queue_mutex);
        queue.push_back(std::move(u));
      });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::DeviceStateUpdate> pending;
    {
      std::lock_guard<std::mutex> lock(queue_mutex);
      pending.swap(queue);
    }
    bool broken = false;
    for (const auto& u : pending) {
      if (!writer->Write(u)) {
        broken = true;
        break;
      }
    }
    if (broken) break;
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  mgr->UnsubscribeState(sub);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
