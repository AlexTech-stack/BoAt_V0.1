#pragma once

#include <cstdint>
#include <vector>

namespace boat::hil {

// One PDU entry inside an IpduM dynamic container.
struct IpduMEntry {
  uint32_t             pdu_id;
  std::vector<uint8_t> payload;
};

// Serialize entries into an IpduM dynamic container byte stream.
// Format per entry (AUTOSAR LONG header):
//   [PDU ID: uint32 big-endian][DLC: uint32 big-endian][payload: DLC bytes]
std::vector<uint8_t> IpduMSerialize(const std::vector<IpduMEntry>& entries);

// Deserialize an IpduM dynamic container byte stream.
// Returns false if the buffer is malformed (truncated header or payload).
bool IpduMDeserialize(const uint8_t* data, std::size_t len,
                      std::vector<IpduMEntry>& out);

// Build a complete IP/UDP datagram carrying an IpduM container.
// The returned buffer starts at the IP header (i.e. is the Ethernet payload).
// src_ip / dst_ip must point to exactly 4 bytes each (IPv4).
std::vector<uint8_t> BuildUdpIpv4(const uint8_t src_ip[4],
                                   const uint8_t dst_ip[4],
                                   uint16_t src_port, uint16_t dst_port,
                                   uint8_t ttl,
                                   const std::vector<uint8_t>& container);

// src_ip / dst_ip must point to exactly 16 bytes each (IPv6).
std::vector<uint8_t> BuildUdpIpv6(const uint8_t src_ip[16],
                                   const uint8_t dst_ip[16],
                                   uint16_t src_port, uint16_t dst_port,
                                   uint8_t hop_limit,
                                   const std::vector<uint8_t>& container);

// Parse an IP/UDP/IpduM packet starting at the IP header.
// Supports IPv4 (version 4) and IPv6 (version 6) with no extension headers.
// Protocol must be 17 (UDP). Returns false on any parse error.
bool ParseUdpIpPacket(const uint8_t* data, std::size_t len,
                      uint16_t* src_port_out, uint16_t* dst_port_out,
                      std::vector<IpduMEntry>& out);

}  // namespace boat::hil
