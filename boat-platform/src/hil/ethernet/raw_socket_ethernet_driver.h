#pragma once

#include <atomic>
#include <string>

#include "ethernet/ethernet_frame.h"

namespace boat::hil {

/* Physical Ethernet driver using AF_PACKET / SOCK_RAW (Linux only).
 *
 * Binds to a named network interface (e.g. "eth0") and captures all frames
 * regardless of ethertype.  Outgoing frames are suppressed via
 * PACKET_IGNORE_OUTGOING so the registry's direct DispatchRx path handles
 * loopback to gRPC subscribers — matching the same design used by
 * VirtualEthernetDriver and SocketCanDriver.
 *
 * Requires CAP_NET_RAW or running as root.
 */
class RawSocketEthernetDriver : public IEthernetDriver {
 public:
  explicit RawSocketEthernetDriver(std::string iface);

  bool Open()  override;
  void Close() override;
  bool ReadFrame(EthernetFrame& out)          override;
  bool WriteFrame(const EthernetFrame& frame) override;

  const std::string& iface() const { return iface_; }

 private:
  std::string       iface_;
  int               sock_{-1};
  int               if_index_{-1};
  std::atomic<bool> open_{false};
};

}  // namespace boat::hil
