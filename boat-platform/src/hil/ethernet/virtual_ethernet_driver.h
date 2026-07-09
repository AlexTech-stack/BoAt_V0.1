#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>

#include "ethernet/ethernet_frame.h"

namespace boat::hil {

/* Virtual Ethernet interface backed by UDP multicast.
 *
 * Each named interface maps to a multicast group and port so that multiple
 * veth interfaces on the same machine stay isolated.  Default layout:
 *
 *   veth0  →  239.255.0.1 : 51000
 *   veth1  →  239.255.0.2 : 51001
 *   ...
 *
 * IP_MULTICAST_LOOP is enabled so that frames sent on this machine are also
 * delivered to subscribers on the same machine.
 *
 * Frame serialisation (16-byte header + payload):
 *   [6]  src_mac
 *   [6]  dst_mac
 *   [2]  ethertype  (big-endian)
 *   [2]  payload_len (big-endian)
 *   [N]  payload
 */
class VirtualEthernetDriver : public IEthernetDriver {
 public:
  /* Construct with explicit multicast parameters. */
  VirtualEthernetDriver(std::string     iface,
                        std::string     mcast_addr,
                        std::uint16_t   port);

  /* Convenience: derive mcast_addr and port from iface index (0-based).
   *   index N  →  239.255.0.(N+1) : (51000+N)
   */
  static std::unique_ptr<VirtualEthernetDriver> FromIndex(
      const std::string& iface, std::size_t index);

  bool Open()  override;
  void Close() override;
  bool ReadFrame(EthernetFrame& out) override;
  bool WriteFrame(const EthernetFrame& frame) override;

  const std::string& iface()     const { return iface_; }
  const std::string& mcast_addr()const { return mcast_addr_; }
  std::uint16_t      port()      const { return port_; }

 private:
  std::string     iface_;
  std::string     mcast_addr_;
  std::uint16_t   port_;

  int             sock_{-1};   // single socket for both TX and RX
  std::atomic<bool> open_{false};
};

}  // namespace boat::hil
