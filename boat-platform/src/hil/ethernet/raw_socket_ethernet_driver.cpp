#include "ethernet/raw_socket_ethernet_driver.h"

#ifdef __linux__

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <utility>

#include <arpa/inet.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

// Added in Linux 4.20; define here for older kernel headers.
#ifndef PACKET_IGNORE_OUTGOING
#define PACKET_IGNORE_OUTGOING 23
#endif

namespace boat::hil {

static constexpr std::size_t kEthHdrSize     = 14;   // dst(6)+src(6)+type(2)
static constexpr std::size_t kVlanHdrSize    = 18;   // + 4-byte 802.1Q tag
static constexpr std::size_t kMaxPayload     = 1500;
static constexpr std::size_t kMaxEthFrame    = kVlanHdrSize + kMaxPayload;

static void ExtractIpAddresses(EthernetFrame& f) {
  if (f.ethertype == 0x0800 && f.payload.size() >= 20) {
    f.src_ip.assign(f.payload.begin() + 12, f.payload.begin() + 16);
    f.dst_ip.assign(f.payload.begin() + 16, f.payload.begin() + 20);
  } else if (f.ethertype == 0x86DD && f.payload.size() >= 40) {
    f.src_ip.assign(f.payload.begin() +  8, f.payload.begin() + 24);
    f.dst_ip.assign(f.payload.begin() + 24, f.payload.begin() + 40);
  } else {
    f.src_ip.clear();
    f.dst_ip.clear();
  }
}

RawSocketEthernetDriver::RawSocketEthernetDriver(std::string iface)
    : iface_(std::move(iface)) {}

bool RawSocketEthernetDriver::Open() {
  if (open_.load()) return true;

  sock_ = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
  if (sock_ < 0) {
    std::fprintf(stderr, "[RawSocket] socket() failed for %s: %s\n",
                 iface_.c_str(), std::strerror(errno));
    return false;
  }

  // Resolve interface index.
  struct ifreq ifr{};
  std::strncpy(ifr.ifr_name, iface_.c_str(), IFNAMSIZ - 1);
  if (ioctl(sock_, SIOCGIFINDEX, &ifr) < 0) {
    std::fprintf(stderr, "[RawSocket] SIOCGIFINDEX failed for %s: %s\n",
                 iface_.c_str(), std::strerror(errno));
    ::close(sock_); sock_ = -1;
    return false;
  }
  if_index_ = ifr.ifr_ifindex;

  // Bind to this interface only so we don't capture frames from other NICs.
  struct sockaddr_ll sll{};
  sll.sll_family   = AF_PACKET;
  sll.sll_protocol = htons(ETH_P_ALL);
  sll.sll_ifindex  = if_index_;
  if (bind(sock_, reinterpret_cast<struct sockaddr*>(&sll), sizeof(sll)) < 0) {
    std::fprintf(stderr, "[RawSocket] bind() failed for %s: %s\n",
                 iface_.c_str(), std::strerror(errno));
    ::close(sock_); sock_ = -1;
    return false;
  }

  // Suppress our own transmitted frames — the registry's SendFrame() already
  // calls DispatchRx() directly, so socket loopback would double-deliver them.
  const int ignore_out = 1;
  (void)setsockopt(sock_, SOL_PACKET, PACKET_IGNORE_OUTGOING,
                   &ignore_out, sizeof(ignore_out));

  // Receive timeout so the RX thread can check its running_ flag periodically.
  struct timeval tv{};
  tv.tv_usec = 100000;  // 100 ms
  setsockopt(sock_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

  open_.store(true);
  return true;
}

void RawSocketEthernetDriver::Close() {
  if (open_.exchange(false)) {
    if (sock_ >= 0) { ::close(sock_); sock_ = -1; }
  }
}

bool RawSocketEthernetDriver::ReadFrame(EthernetFrame& out) {
  if (!open_.load() || sock_ < 0) return false;

  unsigned char buf[kMaxEthFrame];
  const ssize_t n = recvfrom(sock_, buf, sizeof(buf), 0, nullptr, nullptr);
  if (n < static_cast<ssize_t>(kEthHdrSize)) return false;

  std::memcpy(out.dst_mac, buf,     6);
  std::memcpy(out.src_mac, buf + 6, 6);

  const uint16_t etype = (static_cast<uint16_t>(buf[12]) << 8) | buf[13];
  if (etype == 0x8100 && n >= static_cast<ssize_t>(kVlanHdrSize)) {
    const uint16_t tci = (static_cast<uint16_t>(buf[14]) << 8) | buf[15];
    out.vlan_id   = tci & 0x0FFFu;
    out.vlan_pcp  = static_cast<uint8_t>((tci >> 13) & 0x07u);
    out.ethertype = (static_cast<uint16_t>(buf[16]) << 8) | buf[17];
    out.payload.assign(buf + kVlanHdrSize, buf + n);
  } else {
    out.vlan_id   = 0;
    out.vlan_pcp  = 0;
    out.ethertype = etype;
    out.payload.assign(buf + kEthHdrSize, buf + n);
  }

  struct timespec ts{};
  out.timestamp_ns = (clock_gettime(CLOCK_REALTIME, &ts) == 0)
      ? static_cast<uint64_t>(ts.tv_sec) * 1'000'000'000ULL + ts.tv_nsec
      : 0;
  ExtractIpAddresses(out);
  return true;
}

bool RawSocketEthernetDriver::WriteFrame(const EthernetFrame& frame) {
  if (!open_.load() || sock_ < 0 || if_index_ < 0) return false;

  const std::size_t payload_len = std::min(frame.payload.size(), kMaxPayload);
  const bool        has_vlan    = frame.vlan_id != 0;
  const std::size_t hdr_size    = has_vlan ? kVlanHdrSize : kEthHdrSize;
  // Ethernet minimum payload is 46 bytes; pad to 60-byte minimum frame.
  const std::size_t eth_payload = std::max(payload_len, std::size_t{46});
  const std::size_t total       = hdr_size + eth_payload;

  unsigned char buf[kMaxEthFrame]{};

  // Use broadcast MAC when caller leaves dst_mac as all-zeros (ARP not resolved).
  const unsigned char* dst = frame.dst_mac;
  static constexpr unsigned char kBroadcast[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
  bool all_zero = true;
  for (int i = 0; i < 6; ++i) { if (frame.dst_mac[i]) { all_zero = false; break; } }
  if (all_zero) dst = kBroadcast;

  std::memcpy(buf,     dst,            6);
  std::memcpy(buf + 6, frame.src_mac,  6);

  if (has_vlan) {
    buf[12] = 0x81; buf[13] = 0x00;
    const uint16_t tci = static_cast<uint16_t>(
        (static_cast<uint16_t>(frame.vlan_pcp & 0x07u) << 13) |
        (frame.vlan_id & 0x0FFFu));
    buf[14] = static_cast<unsigned char>(tci >> 8);
    buf[15] = static_cast<unsigned char>(tci & 0xFF);
    buf[16] = static_cast<unsigned char>(frame.ethertype >> 8);
    buf[17] = static_cast<unsigned char>(frame.ethertype & 0xFF);
  } else {
    buf[12] = static_cast<unsigned char>(frame.ethertype >> 8);
    buf[13] = static_cast<unsigned char>(frame.ethertype & 0xFF);
  }

  if (payload_len > 0) {
    std::memcpy(buf + hdr_size, frame.payload.data(), payload_len);
  }
  // Zero-pad to Ethernet minimum (already zero-initialised, just extends total).

  struct sockaddr_ll dest{};
  dest.sll_family  = AF_PACKET;
  dest.sll_ifindex = if_index_;
  dest.sll_halen   = 6;
  std::memcpy(dest.sll_addr, dst, 6);

  // Retry on ENOBUFS — USB NICs have small TX rings; a brief pause drains them.
  ssize_t sent = -1;
  for (int attempt = 0; attempt < 3; ++attempt) {
    sent = sendto(sock_, buf, total, 0,
                  reinterpret_cast<struct sockaddr*>(&dest),
                  sizeof(dest));
    if (sent >= 0) break;
    if (errno == ENOBUFS || errno == EAGAIN) {
      struct timespec ts{0, 2000000};  // 2 ms
      nanosleep(&ts, nullptr);
    } else {
      break;
    }
  }
  if (sent != static_cast<ssize_t>(total)) {
    std::fprintf(stderr, "[RawSocket] sendto failed on %s (errno %d: %s) frame_len=%zu\n",
                 iface_.c_str(), errno, std::strerror(errno), total);
    return false;
  }
  return true;
}

}  // namespace boat::hil

#endif  // __linux__
