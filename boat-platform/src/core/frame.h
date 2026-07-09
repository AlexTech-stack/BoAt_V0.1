#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <variant>
#include <vector>

#include "boat/frame.h"

namespace boat::core {

/* ── Internal metadata types (owning, RAII) ─────────────────────────── */

struct CanMeta {
  uint32_t can_id = 0;
  uint8_t  dlc    = 0;
  uint8_t  flags  = 0;

  CanMeta() = default;
  explicit CanMeta(const BoatCanMeta& m) : can_id(m.can_id), dlc(m.dlc), flags(m.flags) {}
  void ToAbi(BoatCanMeta* out) const;
};

struct EthMeta {
  uint8_t  dst_mac[6]  = {};
  uint8_t  src_mac[6]  = {};
  uint16_t ethertype   = 0;
  uint16_t vlan_id     = 0;
  uint8_t  ip_version  = 0;  // 4 or 6; 0 = none
  uint8_t  flags       = 0;  // BOAT_ETH_FLAG_SELF_SENT etc.
  uint8_t  src_ip[16]  = {}; // 4 bytes (v4) or 16 (v6)
  uint8_t  dst_ip[16]  = {};

  EthMeta() = default;
  explicit EthMeta(const BoatEthMeta& m);
  void ToAbi(BoatEthMeta* out) const;
};

struct TcpMeta {
  uint8_t  src_ip[16] = {};   // 4 bytes (v4) or 16 (v6)
  uint8_t  dst_ip[16] = {};
  uint8_t  ip_version = 0;   // 4 or 6
  uint8_t  _pad       = 0;
  uint16_t src_port   = 0;
  uint16_t dst_port   = 0;
  int32_t  conn_id    = -1;  // -1 = new, -2 = close, >= 0 = existing

  TcpMeta() = default;
  explicit TcpMeta(const BoatTcpMeta& m);
  void ToAbi(BoatTcpMeta* out) const;
};

struct PduMeta {
  uint32_t pdu_id = 0;

  PduMeta() = default;
  explicit PduMeta(uint32_t id) : pdu_id(id) {}
  explicit PduMeta(const BoatPduMeta& m) : pdu_id(m.pdu_id) {}
  void ToAbi(BoatPduMeta* out) const;
};

using FrameMeta = std::variant<std::monostate, CanMeta, EthMeta, TcpMeta, PduMeta>;


/* ── Frame — owning, move-only C++ type ─────────────────────────────── */

class Frame {
 public:
  enum class BusType : uint8_t {
    kUnspecified = BOAT_BUS_UNSPECIFIED,
    kCan      = BOAT_BUS_CAN,
    kCanFd    = BOAT_BUS_CANFD,
    kEthernet = BOAT_BUS_ETHERNET,
    kTcp      = BOAT_BUS_TCP,
    kPdu      = BOAT_BUS_PDU,
  };

  Frame() = default;
  ~Frame() = default;

  Frame(Frame&&) noexcept = default;
  Frame& operator=(Frame&&) noexcept = default;

  Frame(const Frame&) = delete;
  Frame& operator=(const Frame&) = delete;

  /* Accessors */
  BusType bus_type() const noexcept { return bus_type_; }
  const std::string& iface() const noexcept { return iface_; }
  uint64_t timestamp_ns() const noexcept { return timestamp_ns_; }
  void set_timestamp_ns(uint64_t ts) noexcept { timestamp_ns_ = ts; }
  const std::vector<uint8_t>& payload() const noexcept { return payload_; }

  /* Metadata accessors — UB if bus_type doesn't match */
  const CanMeta&  can_meta() const;
  const EthMeta&  eth_meta() const;
  const TcpMeta&  tcp_meta() const;
  const PduMeta&  pdu_meta() const;

  /* ── Factory methods ───────────────────────────────────────────────── */

  static Frame FromCan(std::string iface, uint32_t can_id, uint8_t dlc,
                       uint8_t flags, std::vector<uint8_t> payload,
                       bool is_fd = false);

  static Frame FromEthernet(std::string iface,
                            uint8_t dst_mac[6], uint8_t src_mac[6],
                            uint16_t ethertype, uint16_t vlan_id,
                            const uint8_t* src_ip, uint8_t ip_version,
                            const uint8_t* dst_ip,
                            std::vector<uint8_t> payload,
                            uint8_t flags = 0);

  static Frame FromTcp(std::string iface,
                       const uint8_t* src_ip, uint8_t ip_version,
                       const uint8_t* dst_ip,
                       uint16_t src_port, uint16_t dst_port,
                       int32_t conn_id, std::vector<uint8_t> payload);

  static Frame FromPdu(std::string iface, uint32_t pdu_id,
                       std::vector<uint8_t> payload);

  /* ── ABI conversion ────────────────────────────────────────────────── */

  /* Fill a stack-allocated BoatFrame with pointers into this Frame.
     The returned BoatFrame is only valid as long as *this is alive. */
  void ToAbi(BoatFrame* out) const;

  /* Deep-copy an ABI frame. */
  static Frame FromAbi(const BoatFrame& abi);

 private:
  Frame(BusType bus_type, std::string iface, FrameMeta meta,
        std::vector<uint8_t> payload, uint64_t timestamp_ns = 0);

  BusType bus_type_ = BusType::kUnspecified;
  std::string iface_;
  uint64_t timestamp_ns_ = 0;
  FrameMeta meta_;
  std::vector<uint8_t> payload_;
};

}  // namespace boat::core
