#include "frame_sink.h"

#include <algorithm>
#include <cstddef>
#include <cstring>

#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"

namespace boat::gateway {

bool FrameSink::Publish(const boat::core::Frame& frame) {
  using BusType = boat::core::Frame::BusType;

  switch (frame.bus_type()) {
    case BusType::kCan:
    case BusType::kCanFd: {
      const auto& cm = frame.can_meta();
      boat::hil::CanFrame cf{};
      cf.can_id       = cm.can_id;
      cf.dlc          = cm.dlc;
      cf.flags        = cm.flags;
      cf.timestamp_ns = frame.timestamp_ns();
      const std::size_t copy_len =
          std::min(frame.payload().size(), static_cast<std::size_t>(64));
      if (copy_len > 0) {
        std::memcpy(cf.data, frame.payload().data(), copy_len);
      }
      if (!frame.iface().empty()) {
        return can_registry_.SendFrame(frame.iface(), cf);
      }
      can_registry_.SendFrameAll(cf);
      return true;
    }

    case BusType::kEthernet: {
      const auto& em = frame.eth_meta();
      boat::hil::EthernetFrame ef{};
      std::memcpy(ef.dst_mac, em.dst_mac, 6);
      std::memcpy(ef.src_mac, em.src_mac, 6);
      ef.ethertype    = em.ethertype;
      ef.vlan_id      = em.vlan_id;
      ef.flags        = em.flags;
      ef.timestamp_ns = frame.timestamp_ns();
      ef.payload      = frame.payload();
      if (!frame.iface().empty()) {
        return eth_registry_.SendFrame(frame.iface(), ef);
      }
      eth_registry_.SendFrameAll(ef);
      return true;
    }

    case BusType::kTcp:
    case BusType::kPdu:
    case BusType::kUnspecified:
    default:
      // Not wire buses — TCP/PDU are stateful conversations dispatched to
      // plugins via PluginManager::DispatchFrame, never transmitted here.
      return false;
  }
}

}  // namespace boat::gateway
