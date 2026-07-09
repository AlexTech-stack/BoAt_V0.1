#include "ethernet_hil_bridge.h"

#include <cstring>
#include <memory>
#include <utility>

#include "ethernet/raw_socket_ethernet_driver.h"

namespace boat::hil {
namespace {

std::vector<std::uint8_t> EncodeEthFrame(const EthernetFrame& frame) {
  std::vector<std::uint8_t> data;
  // Layout: src_mac(6) + dst_mac(6) + ethertype(2) + vlan_id(2) + vlan_pcp(1)
  //         + src_ip_len(1) + src_ip + dst_ip_len(1) + dst_ip
  //         + payload_len(4) + payload
  data.resize(6 + 6 + 2 + 2 + 1 + 1 + frame.src_ip.size() + 1 + frame.dst_ip.size() + 4 + frame.payload.size());
  std::size_t off = 0;
  std::memcpy(data.data() + off, frame.src_mac, 6); off += 6;
  std::memcpy(data.data() + off, frame.dst_mac, 6); off += 6;
  data[off++] = static_cast<uint8_t>(frame.ethertype >> 8);
  data[off++] = static_cast<uint8_t>(frame.ethertype);
  data[off++] = static_cast<uint8_t>(frame.vlan_id >> 8);
  data[off++] = static_cast<uint8_t>(frame.vlan_id);
  data[off++] = frame.vlan_pcp;
  data[off++] = static_cast<uint8_t>(frame.src_ip.size());
  if (!frame.src_ip.empty()) {
    std::memcpy(data.data() + off, frame.src_ip.data(), frame.src_ip.size());
    off += frame.src_ip.size();
  }
  data[off++] = static_cast<uint8_t>(frame.dst_ip.size());
  if (!frame.dst_ip.empty()) {
    std::memcpy(data.data() + off, frame.dst_ip.data(), frame.dst_ip.size());
    off += frame.dst_ip.size();
  }
  const auto plen = static_cast<std::uint32_t>(frame.payload.size());
  data[off++] = static_cast<uint8_t>(plen >> 24);
  data[off++] = static_cast<uint8_t>(plen >> 16);
  data[off++] = static_cast<uint8_t>(plen >> 8);
  data[off++] = static_cast<uint8_t>(plen);
  if (!frame.payload.empty()) {
    std::memcpy(data.data() + off, frame.payload.data(), frame.payload.size());
  }
  return data;
}

bool DecodeEthFrame(const std::vector<std::uint8_t>& data, EthernetFrame& frame) {
  // Minimum valid size: macs(12) + ethertype(2) + vlan(3) + ip_lens(2) + payload_len(4) = 23
  if (data.size() < 23) return false;
  std::size_t off = 0;
  std::memcpy(frame.src_mac, data.data() + off, 6); off += 6;
  std::memcpy(frame.dst_mac, data.data() + off, 6); off += 6;
  frame.ethertype = static_cast<uint16_t>((static_cast<uint16_t>(data[off]) << 8) | data[off + 1]); off += 2;
  frame.vlan_id   = static_cast<uint16_t>((static_cast<uint16_t>(data[off]) << 8) | data[off + 1]); off += 2;
  frame.vlan_pcp  = data[off++];
  const auto src_ip_len = data[off++];
  if (off + src_ip_len > data.size()) return false;
  frame.src_ip.assign(data.data() + off, data.data() + off + src_ip_len); off += src_ip_len;
  if (off >= data.size()) return false;
  const auto dst_ip_len = data[off++];
  if (off + dst_ip_len > data.size()) return false;
  frame.dst_ip.assign(data.data() + off, data.data() + off + dst_ip_len); off += dst_ip_len;
  if (off + 4 > data.size()) return false;
  std::uint32_t plen = (static_cast<std::uint32_t>(data[off]) << 24) |
                       (static_cast<std::uint32_t>(data[off + 1]) << 16) |
                       (static_cast<std::uint32_t>(data[off + 2]) << 8) |
                       data[off + 3];
  off += 4;
  if (off + plen > data.size()) return false;
  frame.payload.assign(data.data() + off, data.data() + off + plen);
  return true;
}

}  // namespace

EthernetHilBridge::EthernetHilBridge(EthernetBusRegistry& registry,
                                     boat::core::EventBus& bus)
    : registry_(registry), bus_(bus) {
  // Forward every received frame (any interface) into the EventBus.
  rx_sub_id_ = registry_.Subscribe(
      "", 0,
      [this](const EthernetFrame& frame, const std::string& /*iface*/) {
        bus_.Publish(boat::core::BusEvent{
            kEventTypeEthRx,
            boat::core::UnknownPayload{kEventTypeEthRx, EncodeEthFrame(frame)},
            0});
      });
  subscribed_ = true;

  // Forward EventBus TX events onto all registered physical interfaces.
  tx_sub_ = bus_.Subscribe(
      kEventTypeEthTx,
      [this](const boat::core::BusEvent& event) {
        const auto* unknown = std::get_if<boat::core::UnknownPayload>(&event.payload);
        if (unknown == nullptr) return;
        EthernetFrame frame{};
        if (!DecodeEthFrame(unknown->data, frame)) return;
        registry_.SendFrameAll(frame);
      });
}

EthernetHilBridge::~EthernetHilBridge() { Stop(); }

bool EthernetHilBridge::AddPhysicalInterface(const std::string& iface) {
  return registry_.Add(iface, std::make_unique<RawSocketEthernetDriver>(iface));
}

void EthernetHilBridge::Stop() {
  if (subscribed_) {
    registry_.Unsubscribe(rx_sub_id_);
    subscribed_ = false;
  }
  if (tx_sub_.has_value()) {
    bus_.Unsubscribe(*tx_sub_);
    tx_sub_.reset();
  }
}

}  // namespace boat::hil
