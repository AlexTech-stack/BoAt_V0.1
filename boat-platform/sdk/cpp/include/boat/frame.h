#pragma once

#include <stddef.h>
#include <stdint.h>

/* Bus-type discriminator for the unified BoatFrame.
   Values match the proto enum in boat/v1/frame.proto. */
typedef enum BoatBusType {
  BOAT_BUS_UNSPECIFIED = 0,
  BOAT_BUS_CAN         = 1,
  BOAT_BUS_CANFD       = 2,
  BOAT_BUS_ETHERNET    = 3,
  BOAT_BUS_TCP         = 4,
  BOAT_BUS_PDU         = 5,
} BoatBusType;

/* CAN metadata — valid when bus_type is BOAT_BUS_CAN or BOAT_BUS_CANFD.
   flags bits: CANFD_BRS=0x01, CANFD_ESI=0x02, CANFD_FDF=0x04.
   BOAT_BUS_CANFD implicitly sets CANFD_FDF. */
typedef struct BoatCanMeta {
  uint32_t can_id;
  uint8_t  dlc;   /* actual byte count: 0-8 classic, 0-64 FD */
  uint8_t  flags;
} BoatCanMeta;

/* Ethernet metadata — valid when bus_type is BOAT_BUS_ETHERNET. */
typedef struct BoatEthMeta {
  uint8_t  dst_mac[6];
  uint8_t  src_mac[6];
  uint16_t ethertype;
  uint16_t vlan_id;
  uint8_t  ip_version;   /* 4, 6, or 0 = no IP info */
  uint8_t  flags;        /* BOAT_ETH_FLAG_SELF_SENT = 0x01 for loopback prevention */
  uint8_t  src_ip[16];   /* 4 bytes (v4) or 16 bytes (v6) */
  uint8_t  dst_ip[16];   /* same */
} BoatEthMeta;

/* TCP metadata — valid when bus_type is BOAT_BUS_TCP.
   conn_id = -1 means "new connection request" (create or reuse).
   conn_id = -2 means "close this connection".
   conn_id >= 0 addresses an existing connection. */
typedef struct BoatTcpMeta {
  uint8_t  src_ip[16];    /* 4 bytes (v4) or 16 bytes (v6) */
  uint8_t  dst_ip[16];    /* same */
  uint8_t  ip_version;    /* 4 or 6 */
  uint8_t  _pad;          /* alignment */
  uint16_t src_port;
  uint16_t dst_port;
  int32_t  conn_id;       /* -1 = new, -2 = close, >= 0 = existing */
} BoatTcpMeta;

/* PDU metadata — valid when bus_type is BOAT_BUS_PDU.
   Carries the I-PDU identifier for routing. */
typedef struct BoatPduMeta {
  uint32_t pdu_id;
} BoatPduMeta;

/* Unified frame type.
   Ownership: the caller owns the payload buffer and iface string for the
   duration of the callback.  The struct itself can be stack-allocated.
   Metadata union member is selected by bus_type. */
typedef struct BoatFrame {
  BoatBusType  bus_type;
  const char*  iface;          /* interface name; NULL or "" = auto */
  uint64_t     timestamp_ns;
  uint8_t*     payload;
  size_t       payload_len;
  union {
    BoatCanMeta   can;
    BoatEthMeta   eth;
    BoatTcpMeta   tcp;
    BoatPduMeta   pdu;
  } meta;
} BoatFrame;

#ifdef __cplusplus
extern "C" {
#endif

/* Callback: host delivers a frame to a plugin. */
typedef void (*BoatFrameReceiveFn)(void* ctx, const BoatFrame* frame);

/* Callback: plugin publishes a frame back to the bus. */
typedef void (*BoatFramePublishFn)(void* plugin_ctx, const BoatFrame* frame);

/* Optional callback: plugin returns a JSON array of bus-type names it wants
   to receive.  Example: "[\"can\",\"eth\"]".  An empty string \"\" means
   "accept all".  NULL means "use v7 fallback" (backward compat). */
typedef const char* (*BoatDeclaredBusesFn)(void* ctx);

#ifdef __cplusplus
}

#include <cstring>
#include <string>
#include <vector>

/* ── C++ inline init helpers (fill a stack-allocated BoatFrame) ───── */

inline void BoatFrameInitCan(BoatFrame* f, const char* iface,
                              uint32_t can_id, uint8_t dlc, uint8_t flags,
                              const uint8_t* payload, size_t payload_len,
                              bool is_fd = false) noexcept {
  std::memset(f, 0, sizeof(*f));
  f->bus_type = is_fd ? BOAT_BUS_CANFD : BOAT_BUS_CAN;
  f->iface = iface;
  f->meta.can.can_id = can_id;
  f->meta.can.dlc = dlc;
  f->meta.can.flags = flags;
  f->payload = const_cast<uint8_t*>(payload);
  f->payload_len = payload_len;
}

inline void BoatFrameInitEthernet(BoatFrame* f, const char* iface,
                                   const uint8_t dst_mac[6],
                                   const uint8_t src_mac[6],
                                   uint16_t ethertype, uint16_t vlan_id,
                                   const uint8_t* payload,
                                   size_t payload_len) noexcept {
  std::memset(f, 0, sizeof(*f));
  f->bus_type = BOAT_BUS_ETHERNET;
  f->iface = iface;
  std::memcpy(f->meta.eth.dst_mac, dst_mac, 6);
  std::memcpy(f->meta.eth.src_mac, src_mac, 6);
  f->meta.eth.ethertype = ethertype;
  f->meta.eth.vlan_id = vlan_id;
  f->payload = const_cast<uint8_t*>(payload);
  f->payload_len = payload_len;
}

inline void BoatFrameInitPdu(BoatFrame* f, const char* iface,
                              uint32_t pdu_id, const uint8_t* payload,
                              size_t payload_len) noexcept {
  std::memset(f, 0, sizeof(*f));
  f->bus_type = BOAT_BUS_PDU;
  f->iface = iface;
  f->meta.pdu.pdu_id = pdu_id;
  f->payload = const_cast<uint8_t*>(payload);
  f->payload_len = payload_len;
}

/* ── C++ owning BoatFrame wrapper (RAII) ───────────────────────────── */

class BoatFrameOwner {
 public:
  static BoatFrameOwner Can(std::string iface, uint32_t can_id, uint8_t dlc,
                             uint8_t flags, std::vector<uint8_t> payload,
                             bool is_fd = false) noexcept {
    BoatFrameOwner owner;
    owner.iface_ = std::move(iface);
    owner.payload_ = std::move(payload);
    BoatFrameInitCan(&owner.frame_, owner.iface_.c_str(), can_id, dlc, flags,
                     owner.payload_.data(), owner.payload_.size(), is_fd);
    return owner;
  }

  static BoatFrameOwner CanFd(std::string iface, uint32_t can_id, uint8_t dlc,
                               uint8_t flags,
                               std::vector<uint8_t> payload) noexcept {
    return Can(std::move(iface), can_id, dlc, flags, std::move(payload), true);
  }

  static BoatFrameOwner Ethernet(std::string iface,
                                  const uint8_t dst_mac[6],
                                  const uint8_t src_mac[6],
                                  uint16_t ethertype, uint16_t vlan_id,
                                  std::vector<uint8_t> payload) noexcept {
    BoatFrameOwner owner;
    owner.iface_ = std::move(iface);
    owner.payload_ = std::move(payload);
    BoatFrameInitEthernet(&owner.frame_, owner.iface_.c_str(),
                          dst_mac, src_mac, ethertype, vlan_id,
                          owner.payload_.data(), owner.payload_.size());
    return owner;
  }

  static BoatFrameOwner Pdu(std::string iface, uint32_t pdu_id,
                             std::vector<uint8_t> payload) noexcept {
    BoatFrameOwner owner;
    owner.iface_ = std::move(iface);
    owner.payload_ = std::move(payload);
    BoatFrameInitPdu(&owner.frame_, owner.iface_.c_str(), pdu_id,
                     owner.payload_.data(), owner.payload_.size());
    return owner;
  }

  const BoatFrame* operator->() const noexcept { return &frame_; }
  BoatFrame* operator->() noexcept { return &frame_; }
  const BoatFrame* get() const noexcept { return &frame_; }
  BoatFrame* get() noexcept { return &frame_; }

 private:
  BoatFrameOwner() = default;
  std::string iface_;
  std::vector<uint8_t> payload_;
  BoatFrame frame_{};
};

#endif
