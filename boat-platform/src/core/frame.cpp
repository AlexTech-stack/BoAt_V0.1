#include "core/frame.h"

#include <algorithm>
#include <cstring>
#include <stdexcept>

namespace boat::core {

/* ── Meta struct ToAbi helpers ──────────────────────────────────────── */

void CanMeta::ToAbi(BoatCanMeta* out) const {
  out->can_id = can_id;
  out->dlc    = dlc;
  out->flags  = flags;
}

EthMeta::EthMeta(const BoatEthMeta& m)
    : ethertype(m.ethertype), vlan_id(m.vlan_id), ip_version(m.ip_version), flags(m.flags) {
  std::memcpy(dst_mac, m.dst_mac, 6);
  std::memcpy(src_mac, m.src_mac, 6);
  std::memcpy(src_ip, m.src_ip, sizeof(src_ip));
  std::memcpy(dst_ip, m.dst_ip, sizeof(dst_ip));
}

void EthMeta::ToAbi(BoatEthMeta* out) const {
  std::memcpy(out->dst_mac, dst_mac, 6);
  std::memcpy(out->src_mac, src_mac, 6);
  out->ethertype  = ethertype;
  out->vlan_id    = vlan_id;
  out->ip_version = ip_version;
  out->flags      = flags;
  std::memcpy(out->src_ip, src_ip, sizeof(src_ip));
  std::memcpy(out->dst_ip, dst_ip, sizeof(dst_ip));
}

TcpMeta::TcpMeta(const BoatTcpMeta& m)
    : ip_version(m.ip_version), src_port(m.src_port),
      dst_port(m.dst_port), conn_id(m.conn_id) {
  std::memcpy(src_ip, m.src_ip, sizeof(src_ip));
  std::memcpy(dst_ip, m.dst_ip, sizeof(dst_ip));
}

void TcpMeta::ToAbi(BoatTcpMeta* out) const {
  std::memcpy(out->src_ip, src_ip, sizeof(src_ip));
  std::memcpy(out->dst_ip, dst_ip, sizeof(dst_ip));
  out->ip_version = ip_version;
  out->_pad       = 0;
  out->src_port   = src_port;
  out->dst_port   = dst_port;
  out->conn_id    = conn_id;
}

void PduMeta::ToAbi(BoatPduMeta* out) const {
  out->pdu_id = pdu_id;
}


/* ── Frame private constructor ──────────────────────────────────────── */

Frame::Frame(BusType bus_type, std::string iface, FrameMeta meta,
             std::vector<uint8_t> payload, uint64_t timestamp_ns)
    : bus_type_(bus_type), iface_(std::move(iface)),
      timestamp_ns_(timestamp_ns), meta_(std::move(meta)),
      payload_(std::move(payload)) {}


/* ── Metadata accessors ─────────────────────────────────────────────── */

const CanMeta& Frame::can_meta() const {
  return std::get<CanMeta>(meta_);
}

const EthMeta& Frame::eth_meta() const {
  return std::get<EthMeta>(meta_);
}

const TcpMeta& Frame::tcp_meta() const {
  return std::get<TcpMeta>(meta_);
}

const PduMeta& Frame::pdu_meta() const {
  return std::get<PduMeta>(meta_);
}


/* ── Factory methods ────────────────────────────────────────────────── */

Frame Frame::FromCan(std::string iface, uint32_t can_id, uint8_t dlc,
                     uint8_t flags, std::vector<uint8_t> payload, bool is_fd) {
  CanMeta m;
  m.can_id = can_id;
  m.dlc    = dlc;
  m.flags  = flags;
  return Frame(is_fd ? BusType::kCanFd : BusType::kCan,
               std::move(iface), std::move(m), std::move(payload));
}

Frame Frame::FromEthernet(std::string iface,
                          uint8_t dst_mac[6], uint8_t src_mac[6],
                          uint16_t ethertype, uint16_t vlan_id,
                          const uint8_t* src_ip, uint8_t ip_version,
                          const uint8_t* dst_ip,
                          std::vector<uint8_t> payload,
                          uint8_t flags) {
  EthMeta m;
  std::memcpy(m.dst_mac, dst_mac, 6);
  std::memcpy(m.src_mac, src_mac, 6);
  m.ethertype  = ethertype;
  m.vlan_id    = vlan_id;
  m.ip_version = ip_version;
  m.flags      = flags;
  const size_t ip_len = (ip_version == 4) ? 4U : 16U;
  if (src_ip) std::memcpy(m.src_ip, src_ip, ip_len);
  if (dst_ip) std::memcpy(m.dst_ip, dst_ip, ip_len);
  return Frame(BusType::kEthernet, std::move(iface), std::move(m),
               std::move(payload));
}

Frame Frame::FromTcp(std::string iface,
                     const uint8_t* src_ip, uint8_t ip_version,
                     const uint8_t* dst_ip,
                     uint16_t src_port, uint16_t dst_port,
                     int32_t conn_id, std::vector<uint8_t> payload) {
  TcpMeta m;
  m.ip_version = ip_version;
  m.src_port   = src_port;
  m.dst_port   = dst_port;
  m.conn_id    = conn_id;
  const size_t ip_len = (ip_version == 4) ? 4U : 16U;
  if (src_ip) std::memcpy(m.src_ip, src_ip, ip_len);
  if (dst_ip) std::memcpy(m.dst_ip, dst_ip, ip_len);
  return Frame(BusType::kTcp, std::move(iface), std::move(m),
               std::move(payload));
}

Frame Frame::FromPdu(std::string iface, uint32_t pdu_id,
                     std::vector<uint8_t> payload) {
  PduMeta m(pdu_id);
  return Frame(BusType::kPdu, std::move(iface), std::move(m),
               std::move(payload));
}


/* ── ABI conversion ─────────────────────────────────────────────────── */

void Frame::ToAbi(BoatFrame* out) const {
  if (!out) return;
  out->bus_type     = static_cast<BoatBusType>(bus_type_);
  out->iface        = iface_.empty() ? nullptr : iface_.c_str();
  out->timestamp_ns = timestamp_ns_;
  out->payload      = payload_.empty() ? nullptr
                                       : const_cast<uint8_t*>(payload_.data());
  out->payload_len  = payload_.size();

  switch (bus_type_) {
    case BusType::kCan:
    case BusType::kCanFd:
      can_meta().ToAbi(&out->meta.can);
      break;
    case BusType::kEthernet:
      eth_meta().ToAbi(&out->meta.eth);
      break;
    case BusType::kTcp:
      tcp_meta().ToAbi(&out->meta.tcp);
      break;
    case BusType::kPdu:
      pdu_meta().ToAbi(&out->meta.pdu);
      break;
    default:
      std::memset(&out->meta, 0, sizeof(out->meta));
      break;
  }
}

Frame Frame::FromAbi(const BoatFrame& abi) {
  std::vector<uint8_t> payload;
  if (abi.payload && abi.payload_len > 0) {
    payload.assign(abi.payload, abi.payload + abi.payload_len);
  }
  std::string iface = (abi.iface && abi.iface[0]) ? abi.iface : "";

  switch (abi.bus_type) {
    case BOAT_BUS_CAN:
    case BOAT_BUS_CANFD: {
      CanMeta m;
      m.can_id = abi.meta.can.can_id;
      m.dlc    = abi.meta.can.dlc;
      m.flags  = abi.meta.can.flags;
      return Frame(static_cast<BusType>(abi.bus_type),
                   std::move(iface), std::move(m), std::move(payload),
                   abi.timestamp_ns);
    }
    case BOAT_BUS_ETHERNET:
      return Frame(BusType::kEthernet, std::move(iface),
                   EthMeta(abi.meta.eth), std::move(payload),
                   abi.timestamp_ns);
    case BOAT_BUS_TCP:
      return Frame(BusType::kTcp, std::move(iface),
                   TcpMeta(abi.meta.tcp), std::move(payload),
                   abi.timestamp_ns);
    case BOAT_BUS_PDU: {
      PduMeta m(abi.meta.pdu.pdu_id);
      return Frame(BusType::kPdu, std::move(iface),
                   std::move(m), std::move(payload), abi.timestamp_ns);
    }
    default:
      return Frame{};
  }
}

}  // namespace boat::core
