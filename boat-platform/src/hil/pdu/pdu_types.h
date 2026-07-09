#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace boat::hil {

enum class PduTransport { kUnspecified = 0, kCan = 1, kEthernet = 2 };

enum class SendType {
  kNone       = 0,
  kCyclic     = 1,
  kOnChange   = 2,
  kMixed      = 3,
};

struct PduSchedule {
  SendType send_type{SendType::kNone};
  uint32_t cycle_ms{0};        // base period in ms
  uint32_t fast_ms{0};         // fast period for n-times reps in ms
  uint32_t repetitions{0};     // number of fast reps per change event
};

// Routing rule: maps a PDU ID to a transport interface.
struct PduRoute {
  uint32_t     pdu_id{0};
  PduTransport transport{PduTransport::kUnspecified};
  std::string  iface;
  uint16_t     vlan_id{0};         // 0 = untagged (Ethernet)
  uint32_t     can_id{0};          // 0 = use pdu_id as CAN ID
  uint16_t     ethertype{0x88B5};  // used only when dst_ip is empty (sim-only path)

  // IP/UDP/IpduM path — active when dst_ip is non-empty.
  // EtherType is then set automatically: 0x0800 (IPv4) or 0x86DD (IPv6).
  std::vector<uint8_t> src_ip;    // 4 bytes = IPv4, 16 bytes = IPv6
  std::vector<uint8_t> dst_ip;    // 4 bytes = IPv4, 16 bytes = IPv6
  uint16_t             src_port{0};
  uint16_t             dst_port{0};
  uint8_t              ttl{64};   // IPv4 TTL / IPv6 Hop Limit

  PduSchedule          schedule;  // transmission schedule (optional)
};

// Groups several PDU IDs onto a shared Ethernet transport.
// When any member PDU is sent, the router flushes the entire container
// (all slots that have been written at least once) as one Ethernet frame.
struct PduContainerDef {
  uint32_t              container_id{0};
  std::string           iface;
  std::vector<uint8_t>  src_ip;    // 4=IPv4, 16=IPv6
  std::vector<uint8_t>  dst_ip;
  uint16_t              src_port{0};
  uint16_t              dst_port{0};
  uint8_t               ttl{64};
  uint16_t              vlan_id{0};
  std::vector<uint32_t> pdu_ids;   // member PDU IDs
};

// A PDU as received or about to be sent.
struct PduFrame {
  uint32_t             pdu_id{0};
  std::vector<uint8_t> payload;
  uint64_t             timestamp_ns{0};
  PduTransport         source{PduTransport::kUnspecified};
  std::string          iface;
};

// I-PDU Group — a set of PDUs that can be enabled/disabled at runtime.
struct PduGroup {
  uint32_t              group_id{0};
  std::string           name;
  std::vector<uint32_t> pdu_ids;
  bool                  enabled{true};
};

struct PduDeadlineConfig {
  uint32_t cycle_time_ms{0};     // expected receive interval
  uint32_t timeout_factor{3};    // deadline = cycle_time x timeout_factor
};

}  // namespace boat::hil
