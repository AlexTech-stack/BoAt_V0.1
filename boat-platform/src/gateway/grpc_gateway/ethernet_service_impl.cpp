#include "ethernet_service_impl.h"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <sstream>
#include <vector>

#include "ethernet_bus_registry.h"
#include "rpc_audit_log.h"

namespace boat::gateway {

EthernetServiceImpl::EthernetServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

// ── helpers ───────────────────────────────────────────────────────────────────

static uint64_t NowNsEth() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
}

static std::string MacHex(const std::string& mac) {
  if (mac.size() != 6) return "??:??:??:??:??:??";
  std::ostringstream ss;
  for (std::size_t i = 0; i < 6; ++i) {
    if (i) ss << ':';
    const auto b = static_cast<unsigned>(static_cast<uint8_t>(mac[i]));
    ss << std::hex << std::uppercase;
    if (b < 16) ss << '0';
    ss << b;
  }
  return ss.str();
}

static std::string EthFrameSummary(const boat::hil::EthernetFrame& f,
                                   const std::string& iface) {
  std::ostringstream ss;
  ss << iface
     << "  0x" << std::hex << std::uppercase << f.ethertype
     << std::dec << "  len=" << f.payload.size()
     << "  " << MacHex(std::string(reinterpret_cast<const char*>(f.src_mac), 6))
     << " → " << MacHex(std::string(reinterpret_cast<const char*>(f.dst_mac), 6));
  return ss.str();
}

// ── handlers ──────────────────────────────────────────────────────────────────

grpc::Status EthernetServiceImpl::SendFrame(
    grpc::ServerContext* context,
    const boat::v1::SendEthernetFrameRequest* request,
    boat::v1::SendEthernetFrameResponse* response) {

  const auto& pf = request->frame();
  const std::string& iface = pf.iface();
  if (iface.empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "iface is required; specify an Ethernet interface name");
  }

  boat::hil::EthernetFrame frame;
  if (pf.src_mac().size() == 6) {
    std::memcpy(frame.src_mac, pf.src_mac().data(), 6);
  }
  if (pf.dst_mac().size() == 6) {
    std::memcpy(frame.dst_mac, pf.dst_mac().data(), 6);
  }
  frame.ethertype    = static_cast<uint16_t>(pf.ethertype() & 0xFFFF);
  frame.payload.assign(pf.payload().begin(), pf.payload().end());
  frame.timestamp_ns = pf.timestamp_ns();
  frame.vlan_id      = static_cast<uint16_t>(pf.vlan_id()  & 0x0FFF);
  frame.vlan_pcp     = static_cast<uint8_t> (pf.vlan_pcp() & 0x07);
  frame.src_ip.assign(pf.src_ip().begin(), pf.src_ip().end());
  frame.dst_ip.assign(pf.dst_ip().begin(), pf.dst_ip().end());

  const bool accepted = ctx_.ethernet_bus_registry.SendFrame(iface, frame);
  if (!accepted) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND,
                        "Ethernet interface not registered: " + iface);
  }
  // Audit: log what was sent.
  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsEth();
    ev.method     = "EthernetService/SendFrame";
    ev.peer       = context->peer();
    ev.event_type = "DATA";
    ev.call_type  = "UNARY";
    ev.summary    = EthFrameSummary(frame, iface);
    ctx_.audit_log.Push(std::move(ev));
  }

  response->set_accepted(accepted);
  return grpc::Status::OK;
}

grpc::Status EthernetServiceImpl::SubscribeFrames(
    grpc::ServerContext* context,
    const boat::v1::SubscribeEthernetFramesRequest* request,
    grpc::ServerWriter<boat::v1::EthernetFrame>* writer) {

  const std::string iface_filter     = request->iface();
  const uint32_t    ethertype_filter = request->ethertype();

  if (!iface_filter.empty() &&
      !ctx_.ethernet_bus_registry.Has(iface_filter)) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND,
                        "Ethernet interface not registered: " + iface_filter);
  }

  const std::string peer = context->peer();

  // Audit: subscription opened.
  {
    std::ostringstream ss;
    ss << "iface=" << (iface_filter.empty() ? "(all)" : iface_filter);
    if (ethertype_filter)
      ss << "  ethertype=0x" << std::hex << std::uppercase << ethertype_filter;
    RpcEvent ev;
    ev.timestamp_ns = NowNsEth();
    ev.method     = "EthernetService/SubscribeFrames";
    ev.peer       = peer;
    ev.event_type = "SUBSCRIBE_OPEN";
    ev.call_type  = "SERVER_STREAM";
    ev.summary    = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  std::mutex                           queue_mutex;
  std::condition_variable              queue_cv;
  std::vector<boat::v1::EthernetFrame> queue;

  const auto sub_id = ctx_.ethernet_bus_registry.Subscribe(
      iface_filter, ethertype_filter,
      [&queue_mutex, &queue_cv, &queue](const boat::hil::EthernetFrame& f,
                                        const std::string& iface) {
        boat::v1::EthernetFrame proto;
        proto.set_iface(iface);
        proto.set_src_mac(f.src_mac, 6);
        proto.set_dst_mac(f.dst_mac, 6);
        proto.set_ethertype(f.ethertype);
        proto.set_payload(f.payload.data(), f.payload.size());
        proto.set_timestamp_ns(f.timestamp_ns);
        proto.set_vlan_id(f.vlan_id);
        proto.set_vlan_pcp(f.vlan_pcp);
        proto.set_src_ip(f.src_ip.data(), f.src_ip.size());
        proto.set_dst_ip(f.dst_ip.data(), f.dst_ip.size());
        {
          std::lock_guard<std::mutex> lock(queue_mutex);
          queue.push_back(std::move(proto));
        }
        queue_cv.notify_one();
      });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::EthernetFrame> pending;
    {
      std::unique_lock<std::mutex> lock(queue_mutex);
      queue_cv.wait_for(lock, std::chrono::milliseconds(50),
                        [&queue] { return !queue.empty(); });
      pending.swap(queue);
    }
    for (const auto& proto : pending) {
      if (!writer->Write(proto)) {
        ctx_.ethernet_bus_registry.Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
      // Audit: frame delivered to subscriber.
      boat::hil::EthernetFrame raw;
      raw.ethertype = static_cast<uint16_t>(proto.ethertype());
      if (proto.src_mac().size() == 6)
        std::memcpy(raw.src_mac, proto.src_mac().data(), 6);
      if (proto.dst_mac().size() == 6)
        std::memcpy(raw.dst_mac, proto.dst_mac().data(), 6);
      raw.payload.assign(proto.payload().begin(), proto.payload().end());
      RpcEvent ev;
      ev.timestamp_ns = NowNsEth();
      ev.method     = "EthernetService/SubscribeFrames";
      ev.peer       = peer;
      ev.event_type = "DATA";
      ev.call_type  = "SERVER_STREAM";
      ev.summary    = EthFrameSummary(raw, proto.iface());
      ctx_.audit_log.Push(std::move(ev));
    }
  }

  ctx_.ethernet_bus_registry.Unsubscribe(sub_id);
  return grpc::Status::OK;
}

grpc::Status EthernetServiceImpl::ListInterfaces(
    grpc::ServerContext*,
    const boat::v1::ListEthernetInterfacesRequest*,
    boat::v1::ListEthernetInterfacesResponse* response) {
  for (const auto& iface : ctx_.ethernet_bus_registry.Interfaces()) {
    response->add_ifaces(iface);
  }
  return grpc::Status::OK;
}

}  // namespace boat::gateway
