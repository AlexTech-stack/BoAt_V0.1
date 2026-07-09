#pragma once

#include <cstdint>
#include <vector>

namespace boat::hil {

struct EthernetFrame {
  uint8_t              src_mac[6]{};   // source MAC address
  uint8_t              dst_mac[6]{};   // destination MAC address
  uint16_t             ethertype{0};   // inner ethertype (after VLAN strip); e.g. 0x0800=IPv4
  std::vector<uint8_t> payload;        // frame payload (≤1500 bytes typical)
  uint64_t             timestamp_ns{0};
  uint16_t             vlan_id{0};     // 802.1Q VID: 0 = untagged, 1-4094 = tagged
  uint8_t              vlan_pcp{0};    // 802.1Q Priority Code Point (0-7)
  uint8_t              flags{0};       // BOAT_ETH_FLAG_SELF_SENT = 0x01 for loopback prevention
  // Populated on receive when ethertype is 0x0800 (IPv4) or 0x86DD (IPv6).
  // Informational on send — callers are responsible for correct IP headers in payload.
  std::vector<uint8_t> src_ip;         // 4 bytes (IPv4) or 16 bytes (IPv6); empty if non-IP
  std::vector<uint8_t> dst_ip;         // 4 bytes (IPv4) or 16 bytes (IPv6); empty if non-IP
};

class IEthernetDriver {
 public:
  virtual ~IEthernetDriver() = default;

  virtual bool Open()  = 0;
  virtual void Close() = 0;

  /* Block until a frame is received.  Returns false on error or close. */
  virtual bool ReadFrame(EthernetFrame& out) = 0;

  /* Send a frame.  Returns false on error. */
  virtual bool WriteFrame(const EthernetFrame& frame) = 0;
};

}  // namespace boat::hil
