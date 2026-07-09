#include "frame_service_impl.h"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

#include "can_bus_registry.h"
#include "core/frame.h"
#include "ethernet_bus_registry.h"
#include "frame_sink.h"

namespace boat::gateway {
namespace {

/* ── Proto → core::Frame conversion ─────────────────────────────────── */

static boat::core::Frame ProtoToFrame(const boat::v1::Frame& pf) {
  using boat::core::Frame;

  std::string iface = pf.iface();
  std::vector<uint8_t> payload(pf.payload().begin(), pf.payload().end());

  switch (pf.bus_type()) {
    case boat::v1::Frame::CAN:
    case boat::v1::Frame::CANFD: {
      const auto& cm = pf.can();
      return Frame::FromCan(std::move(iface), cm.can_id(),
                            static_cast<uint8_t>(cm.dlc()),
                            static_cast<uint8_t>(cm.flags()),
                            std::move(payload),
                            pf.bus_type() == boat::v1::Frame::CANFD);
    }
    case boat::v1::Frame::ETHERNET: {
      const auto& em = pf.eth();
      uint8_t dm[6], sm[6];
      std::memcpy(dm, em.dst_mac().data(), std::min(em.dst_mac().size(), 6UL));
      std::memcpy(sm, em.src_mac().data(), std::min(em.src_mac().size(), 6UL));
      const uint8_t* sip = em.src_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(em.src_ip().data());
      const uint8_t* dip = em.dst_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(em.dst_ip().data());
      return Frame::FromEthernet(std::move(iface), dm, sm,
                                 static_cast<uint16_t>(em.ethertype()),
                                 static_cast<uint16_t>(em.vlan_id()),
                                 sip, static_cast<uint8_t>(em.ip_version()), dip,
                                 std::move(payload));
    }
    case boat::v1::Frame::TCP: {
      const auto& tm = pf.tcp();
      const uint8_t* sip = tm.src_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(tm.src_ip().data());
      const uint8_t* dip = tm.dst_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(tm.dst_ip().data());
      return Frame::FromTcp(std::move(iface), sip,
                            static_cast<uint8_t>(tm.ip_version()), dip,
                            static_cast<uint16_t>(tm.src_port()),
                            static_cast<uint16_t>(tm.dst_port()),
                            tm.conn_id(), std::move(payload));
    }
    case boat::v1::Frame::PDU: {
      const auto& pm = pf.pdu();
      return Frame::FromPdu(std::move(iface), pm.pdu_id(), std::move(payload));
    }
    default:
      return Frame{};
  }
}

/* ── core::Frame → Proto conversion ─────────────────────────────────── */

static void FrameToProto(const boat::core::Frame& f, boat::v1::Frame* proto) {
  proto->set_bus_type(static_cast<boat::v1::Frame::BusType>(f.bus_type()));
  proto->set_iface(f.iface());
  proto->set_timestamp_ns(f.timestamp_ns());
  proto->set_payload(f.payload().data(), f.payload().size());

  switch (f.bus_type()) {
    case boat::core::Frame::BusType::kCan:
    case boat::core::Frame::BusType::kCanFd: {
      auto* cm = proto->mutable_can();
      cm->set_can_id(f.can_meta().can_id);
      cm->set_dlc(f.can_meta().dlc);
      cm->set_flags(f.can_meta().flags);
      break;
    }
    case boat::core::Frame::BusType::kEthernet: {
      auto* em = proto->mutable_eth();
      em->set_dst_mac(f.eth_meta().dst_mac, 6);
      em->set_src_mac(f.eth_meta().src_mac, 6);
      em->set_ethertype(f.eth_meta().ethertype);
      em->set_vlan_id(f.eth_meta().vlan_id);
      em->set_ip_version(f.eth_meta().ip_version);
      if (f.eth_meta().ip_version == 4) {
        em->set_src_ip(f.eth_meta().src_ip, 4);
        em->set_dst_ip(f.eth_meta().dst_ip, 4);
      } else if (f.eth_meta().ip_version == 6) {
        em->set_src_ip(f.eth_meta().src_ip, 16);
        em->set_dst_ip(f.eth_meta().dst_ip, 16);
      }
      break;
    }
    case boat::core::Frame::BusType::kTcp: {
      auto* tm = proto->mutable_tcp();
      tm->set_ip_version(f.tcp_meta().ip_version);
      tm->set_src_port(f.tcp_meta().src_port);
      tm->set_dst_port(f.tcp_meta().dst_port);
      tm->set_conn_id(f.tcp_meta().conn_id);
      if (f.tcp_meta().ip_version == 4) {
        tm->set_src_ip(f.tcp_meta().src_ip, 4);
        tm->set_dst_ip(f.tcp_meta().dst_ip, 4);
      } else if (f.tcp_meta().ip_version == 6) {
        tm->set_src_ip(f.tcp_meta().src_ip, 16);
        tm->set_dst_ip(f.tcp_meta().dst_ip, 16);
      }
      break;
    }
    case boat::core::Frame::BusType::kPdu: {
      auto* pm = proto->mutable_pdu();
      pm->set_pdu_id(f.pdu_meta().pdu_id);
      break;
    }
    default:
      break;
  }
}

}  // namespace

FrameServiceImpl::FrameServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status FrameServiceImpl::SendFrame(grpc::ServerContext*,
                                         const boat::v1::SendFrameRequest* request,
                                         boat::v1::SendFrameResponse* response) {
  auto frame = ProtoToFrame(request->frame());

  switch (frame.bus_type()) {
    case boat::core::Frame::BusType::kCan:
    case boat::core::Frame::BusType::kCanFd: {
      if (frame.iface().empty() || !ctx_.can_bus_registry.Has(frame.iface())) {
        return grpc::Status(grpc::NOT_FOUND, "CAN interface not found");
      }
      ctx_.frame_sink.Publish(frame);
      response->set_accepted(true);
      break;
    }
    case boat::core::Frame::BusType::kEthernet: {
      if (frame.iface().empty() || !ctx_.ethernet_bus_registry.Has(frame.iface())) {
        return grpc::Status(grpc::NOT_FOUND, "Ethernet interface not found");
      }
      ctx_.frame_sink.Publish(frame);
      response->set_accepted(true);
      break;
    }
    case boat::core::Frame::BusType::kPdu: {
      // PDU frames are not a wire bus — dispatch to the plugin frame bus so the
      // pdu_router plugin (if loaded) routes them onto their configured transport.
      BoatFrame abi{};
      frame.ToAbi(&abi);
      ctx_.plugin_manager.DispatchFrame(abi);
      response->set_accepted(true);
      break;
    }
    case boat::core::Frame::BusType::kTcp: {
      // TCP is a stateful conversation, not a fire-and-forget frame: it is driven
      // through the TCP plugin's own connection API, not raw FrameService.SendFrame.
      return grpc::Status(
          grpc::StatusCode::UNIMPLEMENTED,
          "TCP is connection-oriented; use the TCP plugin, not FrameService.SendFrame");
    }
    default:
      response->set_accepted(false);
      break;
  }

  return grpc::Status::OK;
}

grpc::Status FrameServiceImpl::SubscribeFrames(
    grpc::ServerContext* context,
    const boat::v1::SubscribeFramesRequest* request,
    grpc::ServerWriter<boat::v1::Frame>* writer) {

  // Determine which bus types to subscribe to
  bool subscribe_can = true;
  bool subscribe_eth = true;
  if (!request->bus_types().empty()) {
    subscribe_can  = false;
    subscribe_eth  = false;
    for (auto bt : request->bus_types()) {
      if (bt == boat::v1::Frame::CAN || bt == boat::v1::Frame::CANFD)
        subscribe_can = true;
      if (bt == boat::v1::Frame::ETHERNET)
        subscribe_eth = true;
    }
  }

  std::mutex write_mutex;
  using CanRxId  = boat::hil::CanBusRegistry::RxCallbackId;
  using EthRxId  = boat::hil::EthernetBusRegistry::RxCallbackId;
  std::vector<CanRxId> can_subs;
  std::vector<EthRxId> eth_subs;

  // Subscribe to CAN
  if (subscribe_can) {
    auto id = ctx_.can_bus_registry.SubscribeFrame(
        [&write_mutex, writer](const boat::core::Frame& f) {
          boat::v1::Frame proto;
          FrameToProto(f, &proto);
          std::lock_guard<std::mutex> lock(write_mutex);
          writer->Write(proto);
        });
    can_subs.push_back(id);
  }

  // Subscribe to Ethernet
  if (subscribe_eth) {
    auto id = ctx_.ethernet_bus_registry.SubscribeFrame(
        [&write_mutex, writer](const boat::core::Frame& f) {
          boat::v1::Frame proto;
          FrameToProto(f, &proto);
          std::lock_guard<std::mutex> lock(write_mutex);
          writer->Write(proto);
        });
    eth_subs.push_back(id);
  }

  // Wait for client disconnect
  while (!context->IsCancelled()) {
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  // Cleanup
  for (auto id : can_subs) ctx_.can_bus_registry.UnsubscribeFrame(id);
  for (auto id : eth_subs) ctx_.ethernet_bus_registry.UnsubscribeFrame(id);

  return grpc::Status::OK;
}

}  // namespace boat::gateway
