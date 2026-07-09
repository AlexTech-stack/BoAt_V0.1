#include "pdu/pdu_router.h"

#include <chrono>
#include <cstring>

#include "pdu/ipdumcontainer.h"

namespace boat::hil {

// ── Construction / destruction ────────────────────────────────────────────────

PduRouter::PduRouter()
    : tx_engine_(std::make_unique<TransmissionEngine>(
          [this](uint32_t pid, const std::vector<uint8_t>& pl) {
            return SendPdu(pid, pl);
          })) {}

PduRouter::PduRouter(CanBusRegistry& can, EthernetBusRegistry& eth)
    : can_(&can), eth_(&eth),
      tx_engine_(std::make_unique<TransmissionEngine>(
          [this](uint32_t pid, const std::vector<uint8_t>& pl) {
            return SendPdu(pid, pl);
          })) {
  // Subscribe to all frames on both registries; PDU matching is done internally.
  can_sub_id_ = can_->Subscribe(
      "",
      [this](const CanFrame& frame, const std::string& iface) {
        OnCanFrame(frame, iface);
      });

  eth_sub_id_ = eth_->Subscribe(
      "", 0,
      [this](const EthernetFrame& frame, const std::string& iface) {
        OnEthernetFrame(frame, iface);
      });

  subscribed_ = true;
}

void PduRouter::SetFramePublisher(std::function<void(const BoatFrame&)> fn) {
  frame_publisher_ = std::move(fn);
}

PduRouter::~PduRouter() { Stop(); }

// ── Route management ──────────────────────────────────────────────────────────

void PduRouter::AddRoute(const PduRoute& route) {
  std::lock_guard<std::mutex> lock(routes_mutex_);
  routes_[route.pdu_id] = route;
  // Default ethertype when caller leaves it as 0.
  if (routes_[route.pdu_id].ethertype == 0) {
    routes_[route.pdu_id].ethertype = 0x88B5;
  }

  // Configure or remove transmission schedule.
  if (route.schedule.send_type != SendType::kNone) {
    tx_engine_->ConfigureSchedule(route.pdu_id, route.schedule);
  } else {
    tx_engine_->RemoveSchedule(route.pdu_id);
  }
}

void PduRouter::RemoveRoute(uint32_t pdu_id) {
  {
    std::lock_guard<std::mutex> lock(routes_mutex_);
    routes_.erase(pdu_id);
  }
  tx_engine_->RemoveSchedule(pdu_id);
}

std::vector<PduRoute> PduRouter::ListRoutes() const {
  std::lock_guard<std::mutex> lock(routes_mutex_);
  std::vector<PduRoute> out;
  out.reserve(routes_.size());
  for (const auto& [id, r] : routes_) {
    (void)id;
    out.push_back(r);
  }
  return out;
}

// ── Container management ──────────────────────────────────────────────────────

void PduRouter::AddContainer(const PduContainerDef& def) {
  std::lock_guard<std::mutex> lock(containers_mutex_);
  // Remove old pdu_idcontainer mappings for this container_id if it existed.
  if (containers_.count(def.container_id)) {
    for (const auto& slot : containers_.at(def.container_id).slots) {
      pdu_to_container_.erase(slot.pdu_id);
    }
  }
  ContainerBuffer buf;
  buf.def = def;
  for (const uint32_t pid : def.pdu_ids) {
    buf.slots.push_back({pid, {}});
    pdu_to_container_[pid] = def.container_id;
  }
  containers_[def.container_id] = std::move(buf);
}

bool PduRouter::SendContainer(const PduContainerDef& def,
                               const std::vector<IpduMEntry>& entries) {
  if (entries.empty()) return false;
  const auto container = IpduMSerialize(entries);

  EthernetFrame frame;
  frame.vlan_id = def.vlan_id;

  if (def.dst_ip.size() == 4) {
    frame.ethertype = 0x0800;
    frame.payload   = BuildUdpIpv4(def.src_ip.data(), def.dst_ip.data(),
                                    def.src_port, def.dst_port,
                                    def.ttl, container);
  } else if (def.dst_ip.size() == 16) {
    frame.ethertype = 0x86DD;
    frame.payload   = BuildUdpIpv6(def.src_ip.data(), def.dst_ip.data(),
                                    def.src_port, def.dst_port,
                                    def.ttl, container);
  } else {
    return false;
  }
  frame.src_ip = def.src_ip;
  frame.dst_ip = def.dst_ip;
  return eth_->SendFrame(def.iface, frame);
}

// ── Send ──────────────────────────────────────────────────────────────────────

bool PduRouter::SendPdu(uint32_t pdu_id, const std::vector<uint8_t>& payload) {
  // I-PDU group gate: PDUs in disabled groups are silently dropped.
  if (IsPduGated(pdu_id)) return false;

  // Container path: multiplex with sibling PDUs into one Ethernet frame.
  {
    std::unique_lock<std::mutex> lock(containers_mutex_);
    const auto cit = pdu_to_container_.find(pdu_id);
    if (cit != pdu_to_container_.end()) {
      ContainerBuffer& buf = containers_.at(cit->second);
      for (auto& slot : buf.slots) {
        if (slot.pdu_id == pdu_id) { slot.payload = payload; break; }
      }
      std::vector<IpduMEntry> entries;
      for (const auto& slot : buf.slots) {
        if (!slot.payload.empty())
          entries.push_back({slot.pdu_id, slot.payload});
      }
      const PduContainerDef def = buf.def;
      lock.unlock();
      return SendContainer(def, entries);
    }
  }

  // Per-PDU route path (original behaviour).
  PduRoute route;
  {
    std::lock_guard<std::mutex> lock(routes_mutex_);
    const auto it = routes_.find(pdu_id);
    if (it == routes_.end()) return false;
    route = it->second;
  }

  bool sent = false;

  if (route.transport == PduTransport::kCan) {
    const uint32_t can_id = route.can_id != 0 ? route.can_id : pdu_id;
    const uint8_t dlc = static_cast<uint8_t>(std::min(payload.size(), std::size_t{64}));
    if (frame_publisher_) {
      BoatFrame bf{};
      bf.bus_type = BOAT_BUS_CAN;
      bf.meta.can.can_id = can_id;
      bf.meta.can.dlc = dlc;
      bf.meta.can.flags = 0;
      bf.iface = route.iface.c_str();
      bf.payload = const_cast<uint8_t*>(payload.data());
      bf.payload_len = dlc;
      frame_publisher_(bf);
      sent = true;
    } else if (can_) {
      CanFrame frame{};
      frame.can_id = can_id;
      frame.dlc    = dlc;
      std::memcpy(frame.data, payload.data(), frame.dlc);
      sent = can_->SendFrame(route.iface, frame);
    }
  } else if (route.transport == PduTransport::kEthernet) {
    EthernetFrame frame;
    frame.vlan_id = route.vlan_id;

    if (!route.dst_ip.empty()) {
      // IP/UDP/IpduM path
      const auto container = IpduMSerialize({{pdu_id, payload}});
      if (route.dst_ip.size() == 4) {
        frame.ethertype = 0x0800;
        frame.payload   = BuildUdpIpv4(route.src_ip.data(), route.dst_ip.data(),
                                        route.src_port, route.dst_port,
                                        route.ttl, container);
      } else if (route.dst_ip.size() == 16) {
        frame.ethertype = 0x86DD;
        frame.payload   = BuildUdpIpv6(route.src_ip.data(), route.dst_ip.data(),
                                        route.src_port, route.dst_port,
                                        route.ttl, container);
      } else {
        return false;
      }
      frame.src_ip = route.src_ip;
      frame.dst_ip = route.dst_ip;
    } else {
      // Simulation-only path: [4-byte PDU ID big-endian] + payload
      frame.ethertype = route.ethertype;
      frame.payload.resize(4 + payload.size());
      frame.payload[0] = static_cast<uint8_t>(pdu_id >> 24);
      frame.payload[1] = static_cast<uint8_t>(pdu_id >> 16);
      frame.payload[2] = static_cast<uint8_t>(pdu_id >>  8);
      frame.payload[3] = static_cast<uint8_t>(pdu_id & 0xFF);
      std::memcpy(frame.payload.data() + 4, payload.data(), payload.size());
    }
    if (frame_publisher_) {
      BoatFrame bf{};
      bf.bus_type = BOAT_BUS_ETHERNET;
      bf.meta.eth.ethertype = frame.ethertype;
      bf.meta.eth.vlan_id = route.vlan_id;
      bf.iface = route.iface.c_str();
      bf.payload = const_cast<uint8_t*>(frame.payload.data());
      bf.payload_len = frame.payload.size();
      frame_publisher_(bf);
      sent = true;
    } else {
      sent = eth_->SendFrame(route.iface, frame);
    }
  }

  // Notify transmission engine after a successful send so OnChange
  // detection can compare payloads.
  if (sent) {
    tx_engine_->UpdatePayload(pdu_id, payload);
  }

  return sent;
}

// ── Subscribe / Unsubscribe ───────────────────────────────────────────────────

PduRouter::SubId PduRouter::Subscribe(std::vector<uint32_t> pdu_ids,
                                       RxCallback cb) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  const SubId id = next_sub_id_++;
  subscriptions_[id] = Subscription{std::move(pdu_ids), std::move(cb)};
  return id;
}

void PduRouter::Unsubscribe(SubId id) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  subscriptions_.erase(id);
}

// ── Stop ──────────────────────────────────────────────────────────────────────

void PduRouter::Stop() {
  if (subscribed_) {
    if (can_) can_->Unsubscribe(can_sub_id_);
    if (eth_) eth_->Unsubscribe(eth_sub_id_);
    subscribed_ = false;
  }
}

// ── I-PDU group management ───────────────────────────────────────────────────

void PduRouter::AddGroup(const PduGroup& group) {
  std::lock_guard<std::mutex> lock(groups_mutex_);
  // Remove old reverse mappings for this group_id if it existed.
  if (groups_.count(group.group_id)) {
    for (auto pid : groups_[group.group_id].pdu_ids)
      pdu_to_group_.erase(pid);
  }
  groups_[group.group_id] = group;
  for (auto pid : group.pdu_ids)
    pdu_to_group_[pid] = group.group_id;
}

void PduRouter::EnableGroup(uint32_t group_id) {
  std::lock_guard<std::mutex> lock(groups_mutex_);
  auto it = groups_.find(group_id);
  if (it != groups_.end()) it->second.enabled = true;
}

void PduRouter::DisableGroup(uint32_t group_id) {
  std::lock_guard<std::mutex> lock(groups_mutex_);
  auto it = groups_.find(group_id);
  if (it != groups_.end()) it->second.enabled = false;
}

bool PduRouter::IsGroupEnabled(uint32_t group_id) const {
  std::lock_guard<std::mutex> lock(groups_mutex_);
  auto it = groups_.find(group_id);
  return it != groups_.end() && it->second.enabled;
}

std::vector<PduGroup> PduRouter::ListGroups() const {
  std::lock_guard<std::mutex> lock(groups_mutex_);
  std::vector<PduGroup> out;
  out.reserve(groups_.size());
  for (const auto& [id, g] : groups_) {
    (void)id;
    out.push_back(g);
  }
  return out;
}

bool PduRouter::IsPduGated(uint32_t pdu_id) const {
  std::lock_guard<std::mutex> lock(groups_mutex_);
  auto pit = pdu_to_group_.find(pdu_id);
  if (pit == pdu_to_group_.end()) return false;  // not in any group
  auto git = groups_.find(pit->second);
  return git != groups_.end() && !git->second.enabled;
}

// ── Transmission engine ──────────────────────────────────────────────────────

void PduRouter::OnTick(uint64_t tick_ms) {
  tx_engine_->OnTick(tick_ms);

  // Check deadline timers.
  std::lock_guard<std::mutex> lock(deadline_mutex_);
  for (auto& [pdu_id, ds] : deadlines_) {
    (void)pdu_id;
    if (ds.cfg.cycle_time_ms == 0) continue;
    const uint64_t deadline = ds.last_rx_tick_ms +
        ds.cfg.cycle_time_ms * ds.cfg.timeout_factor;
    if (tick_ms > deadline && !ds.timeout_fired) {
      ds.timeout_fired = true;
      // TODO: push timeout event to EventBus when available in scope
    }
  }
}

// ── Deadline monitoring ──────────────────────────────────────────────────────

void PduRouter::ConfigureDeadline(uint32_t pdu_id,
                                   const PduDeadlineConfig& cfg) {
  std::lock_guard<std::mutex> lock(deadline_mutex_);
  if (cfg.cycle_time_ms == 0) {
    deadlines_.erase(pdu_id);
  } else {
    deadlines_[pdu_id] = DeadlineState{cfg, 0, false};
  }
}

// ── Internal frame handlers ───────────────────────────────────────────────────

void PduRouter::OnCanFrame(const CanFrame& frame, const std::string& iface) {
  // Find a CAN route whose effective CAN ID matches.
  std::vector<PduRoute> matches;
  {
    std::lock_guard<std::mutex> lock(routes_mutex_);
    for (const auto& [id, r] : routes_) {
      (void)id;
      if (r.transport != PduTransport::kCan) continue;
      if (!r.iface.empty() && r.iface != iface) continue;
      const uint32_t eff_can_id = r.can_id != 0 ? r.can_id : r.pdu_id;
      if (eff_can_id == frame.can_id) matches.push_back(r);
    }
  }
  for (const auto& r : matches) {
    if (IsPduGated(r.pdu_id)) continue;
    PduFrame pdu;
    pdu.pdu_id       = r.pdu_id;
    pdu.payload.assign(frame.data, frame.data + frame.dlc);
    pdu.timestamp_ns = frame.timestamp_ns;
    pdu.source       = PduTransport::kCan;
    pdu.iface        = iface;
    DispatchPdu(pdu);
  }
}

void PduRouter::OnEthernetFrame(const EthernetFrame& frame,
                                const std::string& iface) {
  if (frame.ethertype == 0x0800 || frame.ethertype == 0x86DD) {
    // IP/UDP/IpduM path
    uint16_t src_port = 0, dst_port = 0;
    std::vector<IpduMEntry> entries;
    if (!ParseUdpIpPacket(frame.payload.data(), frame.payload.size(),
                           &src_port, &dst_port, entries)) return;

    for (const auto& entry : entries) {
      bool matched = false;
      {
        std::lock_guard<std::mutex> lock(routes_mutex_);
        const auto it = routes_.find(entry.pdu_id);
        if (it != routes_.end()) {
          const PduRoute& r = it->second;
          if (r.transport == PduTransport::kEthernet &&
              (r.iface.empty()     || r.iface    == iface)    &&
              (r.vlan_id == 0      || r.vlan_id  == frame.vlan_id) &&
              (r.dst_port == 0     || r.dst_port == dst_port)) {
            matched = true;
          }
        }
      }
      if (!matched) {
        std::lock_guard<std::mutex> lock(containers_mutex_);
        matched = pdu_to_container_.count(entry.pdu_id) > 0;
      }
      if (!matched) continue;
      if (IsPduGated(entry.pdu_id)) continue;
      PduFrame pdu;
      pdu.pdu_id       = entry.pdu_id;
      pdu.payload      = entry.payload;
      pdu.timestamp_ns = frame.timestamp_ns;
      pdu.source       = PduTransport::kEthernet;
      pdu.iface        = iface;
      DispatchPdu(pdu);
    }
    return;
  }

  // Simulation-only path: [4-byte PDU ID big-endian] + payload
  if (frame.payload.size() < 4) return;

  const uint32_t pdu_id =
      (static_cast<uint32_t>(frame.payload[0]) << 24) |
      (static_cast<uint32_t>(frame.payload[1]) << 16) |
      (static_cast<uint32_t>(frame.payload[2]) <<  8) |
       static_cast<uint32_t>(frame.payload[3]);

  bool matched = false;
  {
    std::lock_guard<std::mutex> lock(routes_mutex_);
    const auto it = routes_.find(pdu_id);
    if (it != routes_.end()) {
      const PduRoute& r = it->second;
      if (r.transport == PduTransport::kEthernet &&
          (r.iface.empty() || r.iface == iface) &&
          r.ethertype == frame.ethertype &&
          r.vlan_id   == frame.vlan_id) {
        matched = true;
      }
    }
  }
  if (!matched) return;
  if (IsPduGated(pdu_id)) return;

  PduFrame pdu;
  pdu.pdu_id       = pdu_id;
  pdu.payload.assign(frame.payload.begin() + 4, frame.payload.end());
  pdu.timestamp_ns = frame.timestamp_ns;
  pdu.source       = PduTransport::kEthernet;
  pdu.iface        = iface;
  DispatchPdu(pdu);
}

void PduRouter::DispatchPdu(const PduFrame& pdu) {
  // Update deadline monitoring for received PDUs.
  {
    std::lock_guard<std::mutex> lock(deadline_mutex_);
    auto dit = deadlines_.find(pdu.pdu_id);
    if (dit != deadlines_.end()) {
      dit->second.last_rx_tick_ms =
          static_cast<uint64_t>(pdu.timestamp_ns / 1000000);
      dit->second.timeout_fired = false;
    }
  }

  std::vector<RxCallback> to_call;
  {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    for (const auto& [id, sub] : subscriptions_) {
      (void)id;
      if (sub.pdu_ids.empty()) {
        to_call.push_back(sub.cb);
      } else {
        for (uint32_t fid : sub.pdu_ids) {
          if (fid == pdu.pdu_id) { to_call.push_back(sub.cb); break; }
        }
      }
    }
  }
  for (const auto& cb : to_call) cb(pdu);
}

}  // namespace boat::hil
