#pragma once

#include <arpa/inet.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

namespace boat {
namespace tcp {

// ── Internet checksum ─────────────────────────────────────────────────────

inline uint16_t Checksum(const uint8_t* data, size_t len) {
  uint32_t sum = 0;
  for (size_t i = 0; i < len; i += 2) {
    uint16_t word = (static_cast<uint16_t>(data[i]) << 8);
    if (i + 1 < len) word |= data[i + 1];
    sum += word;
  }
  while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
  return ~static_cast<uint16_t>(sum) & 0xFFFF;
}

inline uint16_t PseudoChecksum(uint32_t sum, const uint8_t* src_ip,
                                const uint8_t* dst_ip, int ip_len_bytes,
                                uint8_t protocol, uint16_t tcp_len) {
  // Pseudo-header sum
  for (int i = 0; i < ip_len_bytes; ++i) {
    uint16_t word = (static_cast<uint16_t>(src_ip[i]) << 8);
    if (i + 1 < ip_len_bytes) word |= src_ip[++i];
    sum += word;
  }
  for (int i = 0; i < ip_len_bytes; ++i) {
    uint16_t word = (static_cast<uint16_t>(dst_ip[i]) << 8);
    if (i + 1 < ip_len_bytes) word |= dst_ip[++i];
    sum += word;
  }
  sum += protocol;
  sum += tcp_len;
  while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
  return ~static_cast<uint16_t>(sum) & 0xFFFF;
}

// ── TCP header ────────────────────────────────────────────────────────────

struct TcpHeader {
  uint16_t src_port;
  uint16_t dst_port;
  uint32_t seq;
  uint32_t ack;
  uint16_t offset_flags;  // top 4 bits = data offset (words), bottom 12 = flags
  uint16_t window;
  uint16_t checksum;
  uint16_t urgent_ptr;
};

constexpr uint16_t TCP_FLAG_FIN  = 0x0001;
constexpr uint16_t TCP_FLAG_SYN  = 0x0002;
constexpr uint16_t TCP_FLAG_RST  = 0x0004;
constexpr uint16_t TCP_FLAG_PSH  = 0x0008;
constexpr uint16_t TCP_FLAG_ACK  = 0x0010;

// ── Segment builders ──────────────────────────────────────────────────────

inline std::vector<uint8_t> BuildIp4TcpSegment(
    const uint8_t* src_ip, const uint8_t* dst_ip,
    uint16_t src_port, uint16_t dst_port,
    uint32_t seq, uint32_t ack,
    const uint8_t* data, uint32_t data_len,
    uint16_t flags, uint16_t window,
    const uint8_t* options = nullptr, uint32_t opt_len = 0) {

  uint32_t tcp_hdr_words = 5 + ((opt_len + 3) / 4);
  uint32_t tcp_hdr_bytes = tcp_hdr_words * 4;
  uint32_t tcp_seg_len   = tcp_hdr_bytes + data_len;
  uint32_t ip_total_len  = 20 + tcp_seg_len;

  std::vector<uint8_t> seg(ip_total_len);

  // IPv4 header (20 bytes, no options)
  seg[0]  = 0x45;
  seg[1]  = 0;
  seg[2]  = static_cast<uint8_t>(ip_total_len >> 8);
  seg[3]  = static_cast<uint8_t>(ip_total_len & 0xFF);
  seg[4]  = 0; seg[5] = 0;  // identification
  seg[6]  = 0x40; seg[7] = 0; // DF flag
  seg[8]  = 64;                // TTL
  seg[9]  = 6;                 // TCP protocol
  seg[10] = 0; seg[11] = 0;   // checksum placeholder
  std::memcpy(&seg[12], src_ip, 4);
  std::memcpy(&seg[16], dst_ip, 4);

  // IP checksum
  uint16_t ip_csum = Checksum(seg.data(), 20);
  seg[10] = static_cast<uint8_t>(ip_csum >> 8);
  seg[11] = static_cast<uint8_t>(ip_csum & 0xFF);

  // TCP header
  uint8_t* tcp = seg.data() + 20;
  tcp[0] = static_cast<uint8_t>(src_port >> 8);
  tcp[1] = static_cast<uint8_t>(src_port & 0xFF);
  tcp[2] = static_cast<uint8_t>(dst_port >> 8);
  tcp[3] = static_cast<uint8_t>(dst_port & 0xFF);
  uint32_t seq_be = htonl(seq);
  uint32_t ack_be = htonl(ack);
  std::memcpy(tcp + 4, &seq_be, 4);
  std::memcpy(tcp + 8, &ack_be, 4);
  uint16_t offs_flags = (tcp_hdr_words << 12) | (flags & 0x0FFF);
  tcp[12] = static_cast<uint8_t>(offs_flags >> 8);
  tcp[13] = static_cast<uint8_t>(offs_flags & 0xFF);
  tcp[14] = static_cast<uint8_t>(window >> 8);
  tcp[15] = static_cast<uint8_t>(window & 0xFF);
  tcp[16] = 0; tcp[17] = 0;  // checksum placeholder
  tcp[18] = 0; tcp[19] = 0;  // urgent pointer

  if (options && opt_len > 0)
    std::memcpy(tcp + 20, options, opt_len);

  if (data_len > 0)
    std::memcpy(tcp + tcp_hdr_bytes, data, data_len);

  // TCP checksum with IPv4 pseudo-header
  uint32_t sum = 0;
  for (uint32_t i = 0; i < tcp_seg_len; i += 2) {
    uint16_t w = (static_cast<uint16_t>(tcp[i]) << 8);
    if (i + 1 < tcp_seg_len) w |= tcp[i + 1];
    sum += w;
  }
  sum = PseudoChecksum(sum, src_ip, dst_ip, 4, 6, static_cast<uint16_t>(tcp_seg_len));
  tcp[16] = static_cast<uint8_t>(sum >> 8);
  tcp[17] = static_cast<uint8_t>(sum & 0xFF);

  return seg;
}

inline std::vector<uint8_t> BuildIp6TcpSegment(
    const uint8_t* src_ip, const uint8_t* dst_ip,
    uint16_t src_port, uint16_t dst_port,
    uint32_t seq, uint32_t ack,
    const uint8_t* data, uint32_t data_len,
    uint16_t flags, uint16_t window,
    const uint8_t* options = nullptr, uint32_t opt_len = 0) {

  uint32_t tcp_hdr_words = 5 + ((opt_len + 3) / 4);
  uint32_t tcp_hdr_bytes = tcp_hdr_words * 4;
  uint32_t tcp_seg_len   = tcp_hdr_bytes + data_len;

  std::vector<uint8_t> seg(40 + tcp_seg_len);

  // IPv6 fixed header (40 bytes)
  seg[0] = 0x60; seg[1] = 0; seg[2] = 0; seg[3] = 0;  // version + traffic class + flow label
  seg[4] = static_cast<uint8_t>(tcp_seg_len >> 8);
  seg[5] = static_cast<uint8_t>(tcp_seg_len & 0xFF);
  seg[6] = 6;                  // next header = TCP
  seg[7] = 64;                 // hop limit
  std::memcpy(&seg[8], src_ip, 16);
  std::memcpy(&seg[24], dst_ip, 16);

  // TCP header
  uint8_t* tcp = seg.data() + 40;
  tcp[0] = static_cast<uint8_t>(src_port >> 8);
  tcp[1] = static_cast<uint8_t>(src_port & 0xFF);
  tcp[2] = static_cast<uint8_t>(dst_port >> 8);
  tcp[3] = static_cast<uint8_t>(dst_port & 0xFF);
  uint32_t seq_be = htonl(seq);
  uint32_t ack_be = htonl(ack);
  std::memcpy(tcp + 4, &seq_be, 4);
  std::memcpy(tcp + 8, &ack_be, 4);
  uint16_t offs_flags = (tcp_hdr_words << 12) | (flags & 0x0FFF);
  tcp[12] = static_cast<uint8_t>(offs_flags >> 8);
  tcp[13] = static_cast<uint8_t>(offs_flags & 0xFF);
  tcp[14] = static_cast<uint8_t>(window >> 8);
  tcp[15] = static_cast<uint8_t>(window & 0xFF);
  tcp[16] = 0; tcp[17] = 0;
  tcp[18] = 0; tcp[19] = 0;

  if (options && opt_len > 0)
    std::memcpy(tcp + 20, options, opt_len);

  if (data_len > 0)
    std::memcpy(tcp + tcp_hdr_bytes, data, data_len);

  // TCP checksum with IPv6 pseudo-header (mandatory)
  uint32_t sum = 0;
  for (uint32_t i = 0; i < tcp_seg_len; i += 2) {
    uint16_t w = (static_cast<uint16_t>(tcp[i]) << 8);
    if (i + 1 < tcp_seg_len) w |= tcp[i + 1];
    sum += w;
  }
  sum = PseudoChecksum(sum, src_ip, dst_ip, 16, 6, static_cast<uint16_t>(tcp_seg_len));
  tcp[16] = static_cast<uint8_t>(sum >> 8);
  tcp[17] = static_cast<uint8_t>(sum & 0xFF);

  return seg;
}

inline std::vector<uint8_t> BuildMssOption(uint16_t mss) {
  return {0x02, 0x04,
          static_cast<uint8_t>(mss >> 8),
          static_cast<uint8_t>(mss & 0xFF)};
}

}  // namespace tcp
}  // namespace boat
