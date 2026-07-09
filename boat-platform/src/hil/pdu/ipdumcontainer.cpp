#include "pdu/ipdumcontainer.h"

#include <atomic>
#include <cstring>

namespace boat::hil {

// ── IpduM serialization ───────────────────────────────────────────────────────

std::vector<uint8_t> IpduMSerialize(const std::vector<IpduMEntry>& entries) {
  std::vector<uint8_t> out;
  for (const auto& e : entries) {
    const uint32_t id  = e.pdu_id;
    const uint32_t dlc = static_cast<uint32_t>(e.payload.size());
    out.push_back(static_cast<uint8_t>(id  >> 24));
    out.push_back(static_cast<uint8_t>(id  >> 16));
    out.push_back(static_cast<uint8_t>(id  >>  8));
    out.push_back(static_cast<uint8_t>(id  & 0xFF));
    out.push_back(static_cast<uint8_t>(dlc >> 24));
    out.push_back(static_cast<uint8_t>(dlc >> 16));
    out.push_back(static_cast<uint8_t>(dlc >>  8));
    out.push_back(static_cast<uint8_t>(dlc & 0xFF));
    out.insert(out.end(), e.payload.begin(), e.payload.end());
  }
  return out;
}

bool IpduMDeserialize(const uint8_t* data, std::size_t len,
                      std::vector<IpduMEntry>& out) {
  std::size_t pos = 0;
  while (pos < len) {
    if (pos + 8 > len) return false;
    const uint32_t pdu_id =
        (static_cast<uint32_t>(data[pos    ]) << 24) |
        (static_cast<uint32_t>(data[pos + 1]) << 16) |
        (static_cast<uint32_t>(data[pos + 2]) <<  8) |
         static_cast<uint32_t>(data[pos + 3]);
    const uint32_t dlc =
        (static_cast<uint32_t>(data[pos + 4]) << 24) |
        (static_cast<uint32_t>(data[pos + 5]) << 16) |
        (static_cast<uint32_t>(data[pos + 6]) <<  8) |
         static_cast<uint32_t>(data[pos + 7]);
    pos += 8;
    if (pos + dlc > len) return false;
    IpduMEntry entry;
    entry.pdu_id = pdu_id;
    entry.payload.assign(data + pos, data + pos + dlc);
    out.push_back(std::move(entry));
    pos += dlc;
  }
  return true;
}

// ── Checksum helpers ──────────────────────────────────────────────────────────

// Accumulate one's complement 16-bit word sum; odd trailing byte is zero-padded.
static uint32_t OnesComplementAccumulate(const uint8_t* data, std::size_t len) {
  uint32_t sum = 0;
  while (len > 1) {
    sum += (static_cast<uint32_t>(data[0]) << 8) | data[1];
    data += 2;
    len  -= 2;
  }
  if (len == 1) {
    sum += static_cast<uint32_t>(data[0]) << 8;
  }
  return sum;
}

static uint16_t FoldAndInvert(uint32_t sum) {
  while (sum >> 16) {
    sum = (sum & 0xFFFF) + (sum >> 16);
  }
  const uint16_t result = static_cast<uint16_t>(~sum);
  // RFC 768: if checksum computes to zero, use 0xFFFF instead.
  return result == 0 ? 0xFFFF : result;
}

static uint16_t InternetChecksum(const uint8_t* data, std::size_t len) {
  return FoldAndInvert(OnesComplementAccumulate(data, len));
}

// UDP checksum over IPv4 pseudo-header + UDP header + data.
static uint16_t UdpChecksumIPv4(const uint8_t src_ip[4], const uint8_t dst_ip[4],
                                  const uint8_t* udp, uint16_t udp_len) {
  // 12-byte pseudo-header: src_ip(4) dst_ip(4) zero(1) proto=17(1) udp_len(2)
  uint8_t ph[12]{};
  std::memcpy(ph,     src_ip, 4);
  std::memcpy(ph + 4, dst_ip, 4);
  ph[8]  = 0;
  ph[9]  = 17;
  ph[10] = static_cast<uint8_t>(udp_len >> 8);
  ph[11] = static_cast<uint8_t>(udp_len & 0xFF);

  uint32_t sum = OnesComplementAccumulate(ph, 12);
  sum += OnesComplementAccumulate(udp, udp_len);
  return FoldAndInvert(sum);
}

// UDP checksum over IPv6 pseudo-header + UDP header + data (RFC 2460).
static uint16_t UdpChecksumIPv6(const uint8_t src_ip[16], const uint8_t dst_ip[16],
                                  const uint8_t* udp, uint16_t udp_len) {
  // 40-byte pseudo-header: src_ip(16) dst_ip(16) udp_len(4 BE) zero(3) next_hdr=17(1)
  uint8_t ph[40]{};
  std::memcpy(ph,      src_ip, 16);
  std::memcpy(ph + 16, dst_ip, 16);
  ph[32] = 0;
  ph[33] = 0;
  ph[34] = static_cast<uint8_t>(udp_len >> 8);
  ph[35] = static_cast<uint8_t>(udp_len & 0xFF);
  ph[36] = 0; ph[37] = 0; ph[38] = 0;
  ph[39] = 17;

  uint32_t sum = OnesComplementAccumulate(ph, 40);
  sum += OnesComplementAccumulate(udp, udp_len);
  return FoldAndInvert(sum);
}

// ── Build functions ───────────────────────────────────────────────────────────

static std::atomic<uint16_t> g_ipv4_id{1};

std::vector<uint8_t> BuildUdpIpv4(const uint8_t src_ip[4],
                                   const uint8_t dst_ip[4],
                                   uint16_t src_port, uint16_t dst_port,
                                   uint8_t ttl,
                                   const std::vector<uint8_t>& container) {
  const uint16_t udp_len   = static_cast<uint16_t>(8 + container.size());
  const uint16_t total_len = static_cast<uint16_t>(20 + udp_len);
  const uint16_t ip_id     = g_ipv4_id.fetch_add(1);

  std::vector<uint8_t> pkt(total_len, 0);

  // IPv4 header (20 bytes, no options)
  pkt[0]  = 0x45;  // Version=4, IHL=5 (20 bytes)
  pkt[1]  = 0x00;  // DSCP/ECN
  pkt[2]  = static_cast<uint8_t>(total_len >> 8);
  pkt[3]  = static_cast<uint8_t>(total_len & 0xFF);
  pkt[4]  = static_cast<uint8_t>(ip_id >> 8);
  pkt[5]  = static_cast<uint8_t>(ip_id & 0xFF);
  pkt[6]  = 0x40;  // Flags: DF=1, MF=0; fragment offset = 0
  pkt[7]  = 0x00;
  pkt[8]  = ttl;
  pkt[9]  = 17;    // Protocol: UDP
  // pkt[10-11]: header checksum — filled below
  std::memcpy(&pkt[12], src_ip, 4);
  std::memcpy(&pkt[16], dst_ip, 4);

  const uint16_t ip_csum = InternetChecksum(pkt.data(), 20);
  pkt[10] = static_cast<uint8_t>(ip_csum >> 8);
  pkt[11] = static_cast<uint8_t>(ip_csum & 0xFF);

  // UDP header (8 bytes)
  pkt[20] = static_cast<uint8_t>(src_port >> 8);
  pkt[21] = static_cast<uint8_t>(src_port & 0xFF);
  pkt[22] = static_cast<uint8_t>(dst_port >> 8);
  pkt[23] = static_cast<uint8_t>(dst_port & 0xFF);
  pkt[24] = static_cast<uint8_t>(udp_len >> 8);
  pkt[25] = static_cast<uint8_t>(udp_len & 0xFF);
  // pkt[26-27]: UDP checksum — filled below

  // IpduM container payload
  std::memcpy(&pkt[28], container.data(), container.size());

  // UDP checksum over pseudo-header + UDP header + container
  const uint16_t udp_csum = UdpChecksumIPv4(src_ip, dst_ip, &pkt[20], udp_len);
  pkt[26] = static_cast<uint8_t>(udp_csum >> 8);
  pkt[27] = static_cast<uint8_t>(udp_csum & 0xFF);

  return pkt;
}

std::vector<uint8_t> BuildUdpIpv6(const uint8_t src_ip[16],
                                   const uint8_t dst_ip[16],
                                   uint16_t src_port, uint16_t dst_port,
                                   uint8_t hop_limit,
                                   const std::vector<uint8_t>& container) {
  const uint16_t udp_len     = static_cast<uint16_t>(8 + container.size());
  const std::size_t total    = 40 + udp_len;

  std::vector<uint8_t> pkt(total, 0);

  // IPv6 header (40 bytes)
  pkt[0]  = 0x60;  // Version=6, Traffic Class=0, Flow Label=0
  // pkt[1-3]: Traffic Class (low bits) + Flow Label — all zero
  pkt[4]  = static_cast<uint8_t>(udp_len >> 8);   // Payload Length
  pkt[5]  = static_cast<uint8_t>(udp_len & 0xFF);
  pkt[6]  = 17;         // Next Header: UDP
  pkt[7]  = hop_limit;
  std::memcpy(&pkt[8],  src_ip, 16);
  std::memcpy(&pkt[24], dst_ip, 16);

  // UDP header (8 bytes)
  pkt[40] = static_cast<uint8_t>(src_port >> 8);
  pkt[41] = static_cast<uint8_t>(src_port & 0xFF);
  pkt[42] = static_cast<uint8_t>(dst_port >> 8);
  pkt[43] = static_cast<uint8_t>(dst_port & 0xFF);
  pkt[44] = static_cast<uint8_t>(udp_len >> 8);
  pkt[45] = static_cast<uint8_t>(udp_len & 0xFF);
  // pkt[46-47]: UDP checksum — filled below

  // IpduM container payload
  std::memcpy(&pkt[48], container.data(), container.size());

  // UDP checksum (mandatory in IPv6)
  const uint16_t udp_csum = UdpChecksumIPv6(src_ip, dst_ip, &pkt[40], udp_len);
  pkt[46] = static_cast<uint8_t>(udp_csum >> 8);
  pkt[47] = static_cast<uint8_t>(udp_csum & 0xFF);

  return pkt;
}

// ── Parse ─────────────────────────────────────────────────────────────────────

bool ParseUdpIpPacket(const uint8_t* data, std::size_t len,
                      uint16_t* src_port_out, uint16_t* dst_port_out,
                      std::vector<IpduMEntry>& out) {
  if (len < 1) return false;
  const uint8_t version = data[0] >> 4;
  std::size_t   udp_off = 0;

  if (version == 4) {
    if (len < 20) return false;
    const std::size_t ihl = static_cast<std::size_t>(data[0] & 0x0F) * 4;
    if (data[9] != 17) return false;  // not UDP
    udp_off = ihl;
  } else if (version == 6) {
    if (len < 40) return false;
    if (data[6] != 17) return false;  // Next Header must be UDP (no extension headers)
    udp_off = 40;
  } else {
    return false;
  }

  if (len < udp_off + 8) return false;

  const uint16_t src_port = (static_cast<uint16_t>(data[udp_off    ]) << 8) |
                              static_cast<uint16_t>(data[udp_off + 1]);
  const uint16_t dst_port = (static_cast<uint16_t>(data[udp_off + 2]) << 8) |
                              static_cast<uint16_t>(data[udp_off + 3]);
  const uint16_t udp_len  = (static_cast<uint16_t>(data[udp_off + 4]) << 8) |
                              static_cast<uint16_t>(data[udp_off + 5]);

  if (udp_len < 8 || udp_off + udp_len > len) return false;

  if (src_port_out) *src_port_out = src_port;
  if (dst_port_out) *dst_port_out = dst_port;

  return IpduMDeserialize(data + udp_off + 8,
                           static_cast<std::size_t>(udp_len) - 8, out);
}

}  // namespace boat::hil
