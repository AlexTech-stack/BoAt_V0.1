#include "can_bus_registry.h"

#include <boat/plugin.h>

#include <utility>

namespace boat::hil {

bool CanBusRegistry::Add(const std::string& iface, std::shared_ptr<IHalDriver> driver,
                         boat::core::EventBus& bus) {
  if (!driver->Open()) {
    return false;
  }

  // Capture interface metadata before the driver is moved into the bridge.
  auto info = driver->GetInfo();

  auto bridge = std::make_unique<HilBridge>(std::move(driver), bus);

  // Capture iface by value so the lambda stays valid after Add() returns.
  bridge->SetOnReceive([this, iface](const CanFrame& frame) {
    DispatchRx(frame, iface);
  });

  bridge->Start();

  std::lock_guard<std::mutex> lock(bridges_mutex_);
  bridges_[iface] = BridgeEntry{iface, std::move(bridge), std::move(info)};
  return true;
}

bool CanBusRegistry::SendFrame(const std::string& iface, const CanFrame& frame) {
  {
    std::lock_guard<std::mutex> lock(bridges_mutex_);
    const auto it = bridges_.find(iface);
    if (it == bridges_.end()) {
      return false;
    }
    it->second.bridge->SendFrame(frame);
  }
  // Tag as self-sent so subscribers (especially plugins) can distinguish
  // internally-dispatched loopback from frames received from the wire.
  CanFrame self = frame;
  self.flags |= BOAT_CAN_FLAG_SELF_SENT;
  DispatchRx(self, iface);
  return true;
}

void CanBusRegistry::SendFrameAll(const CanFrame& frame) {
  // Collect iface names while holding the lock, then dispatch without it.
  std::vector<std::string> dispatched_ifaces;
  {
    std::lock_guard<std::mutex> lock(bridges_mutex_);
    for (auto& [name, entry] : bridges_) {
      entry.bridge->SendFrame(frame);
      dispatched_ifaces.push_back(name);
    }
  }
  CanFrame self = frame;
  self.flags |= BOAT_CAN_FLAG_SELF_SENT;
  for (const auto& iface : dispatched_ifaces) {
    DispatchRx(self, iface);
  }
}

CanBusRegistry::RxCallbackId CanBusRegistry::Subscribe(const std::string& iface_filter,
                                                       RxCallback cb) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  const RxCallbackId id = next_id_++;
  subscriptions_[id] = Subscription{iface_filter, std::move(cb)};
  return id;
}

void CanBusRegistry::Unsubscribe(RxCallbackId id) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  subscriptions_.erase(id);
}

CanBusRegistry::RxCallbackId CanBusRegistry::SubscribeFrame(FrameRxCallback cb) {
  std::lock_guard<std::mutex> lock(frame_subs_mutex_);
  const RxCallbackId id = next_frame_id_++;
  frame_subscriptions_[id] = std::move(cb);
  return id;
}

void CanBusRegistry::UnsubscribeFrame(RxCallbackId id) {
  std::lock_guard<std::mutex> lock(frame_subs_mutex_);
  frame_subscriptions_.erase(id);
}

std::vector<std::string> CanBusRegistry::Interfaces() const {
  std::lock_guard<std::mutex> lock(bridges_mutex_);
  std::vector<std::string> names;
  names.reserve(bridges_.size());
  for (const auto& [name, _] : bridges_) {
    names.push_back(name);
  }
  return names;
}

bool CanBusRegistry::Has(const std::string& iface) const {
  std::lock_guard<std::mutex> lock(bridges_mutex_);
  return bridges_.find(iface) != bridges_.end();
}

void CanBusRegistry::StopAll() {
  std::lock_guard<std::mutex> lock(bridges_mutex_);
  for (auto& [name, entry] : bridges_) {
    (void)name;
    entry.bridge->Stop();
  }
}

CanInterfaceInfo CanBusRegistry::GetInterfaceInfo(const std::string& iface) const {
  std::lock_guard<std::mutex> lock(bridges_mutex_);
  const auto it = bridges_.find(iface);
  if (it == bridges_.end()) {
    return {};
  }
  return it->second.info;
}

void CanBusRegistry::DispatchRx(const CanFrame& frame, const std::string& iface) {
  // Snapshot subscriptions to avoid holding the lock during callbacks.
  std::vector<RxCallback> to_call;
  {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    for (const auto& [id, sub] : subscriptions_) {
      (void)id;
      if (sub.iface_filter.empty() || sub.iface_filter == iface) {
        to_call.push_back(sub.cb);
      }
    }
  }
  for (const auto& cb : to_call) {
    cb(frame, iface);
  }

  // Deliver to unified-frame subscribers.
  std::vector<FrameRxCallback> frame_cbs;
  {
    std::lock_guard<std::mutex> lock(frame_subs_mutex_);
    frame_cbs.reserve(frame_subscriptions_.size());
    for (const auto& [id, cb] : frame_subscriptions_) {
      (void)id;
      frame_cbs.push_back(cb);
    }
  }
  if (!frame_cbs.empty()) {
    std::vector<uint8_t> payload(frame.data, frame.data + frame.dlc);
    const bool is_fd = (frame.flags & kCanFdFlagFdf) != 0;
    auto core_frame = boat::core::Frame::FromCan(iface, frame.can_id, frame.dlc,
                                                  frame.flags, std::move(payload), is_fd);
    core_frame.set_timestamp_ns(frame.timestamp_ns);
    for (const auto& cb : frame_cbs) {
      cb(core_frame);
    }
  }
}

}  // namespace boat::hil
