// backbone_plugin — gateway-to-gateway backbone forwarding (v9 ABI).
//
// Connects multiple gateways into a mesh topology.  Frames arriving on local
// CAN/ETH interfaces are forwarded to peer gateways via bidirectional gRPC
// streams, where they are published onto the peer's local buses.
//
// Config JSON (appended to the .so path as ?{...}):
//   {
//     "gateway_id": "bench-1",
//     "backbone_port": 50052,
//     "max_hops": 5,
//     "peers": [
//       {"id": "bench-2", "host": "10.0.0.2", "port": 50052}
//     ],
//     "routes": [
//       {"peer": "bench-2", "bus_types": ["can","canfd"], "ifaces": ["vcan0"]}
//     ]
//   }

#include "backbone_plugin.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <string>

namespace boat::backbone {
namespace {

/* ── Tiny config-JSON readers ───────────────────────────────────────── */

std::string CfgStr(const char* cfg, const char* key, std::string def) {
  if (cfg == nullptr) return def;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return def;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return def;
  ++p;
  while (*p == ' ' || *p == '\t') ++p;
  if (*p != '"') return def;
  ++p;
  const char* end = std::strchr(p, '"');
  if (end == nullptr) return def;
  return std::string(p, static_cast<std::size_t>(end - p));
}

long CfgInt(const char* cfg, const char* key, long def) {
  if (cfg == nullptr) return def;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return def;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return def;
  ++p;
  while (*p == ' ' || *p == '\t' || *p == '"') ++p;
  return std::strtol(p, nullptr, 0);
}

bool CfgHas(const char* cfg, const char* token) {
  return cfg != nullptr && std::strstr(cfg, token) != nullptr;
}

/* Parse a JSON array of strings like ["can","canfd"].
   Returns the list of values for a given key (e.g. "bus_types"). */
std::vector<std::string> CfgStrArray(const char* cfg, const char* key) {
  std::vector<std::string> result;
  if (cfg == nullptr) return result;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return result;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return result;
  ++p;
  while (*p == ' ' || *p == '\t') ++p;
  if (*p != '[') return result;
  ++p;
  while (*p) {
    while (*p == ' ' || *p == '\t' || *p == ',' || *p == ']') {
      if (*p == ']') return result;
      ++p;
    }
    if (*p == '"') {
      ++p;
      const char* end = std::strchr(p, '"');
      if (end == nullptr) return result;
      result.emplace_back(p, static_cast<std::size_t>(end - p));
      p = end + 1;
    } else {
      break;
    }
  }
  return result;
}

/* Parse a JSON object for a peer entry like {"id":"...","host":"...","port":...}.
   This is called repeatedly for each peer in the "peers" array. */
std::vector<PeerInfo> ParsePeers(const char* cfg) {
  std::vector<PeerInfo> result;
  if (cfg == nullptr) return result;
  // Find the "peers" array
  const std::string needle = "\"peers\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return result;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return result;
  ++p;
  while (*p == ' ' || *p == '\t') ++p;
  if (*p != '[') return result;
  ++p;

  while (*p) {
    while (*p == ' ' || *p == '\t' || *p == ',' || *p == '\n') ++p;
    if (*p == ']') break;
    if (*p != '{') break;
    ++p;
    // Extract fields from this peer object by scanning for "key":"value" pairs
    PeerInfo pi;
    // We need to scan within this object until '}'
    const char* obj_end = std::strchr(p, '}');
    if (obj_end == nullptr) break;
    std::string obj(p, static_cast<std::size_t>(obj_end - p + 1));
    // Now parse individual fields from obj
    pi.id = CfgStr(obj.c_str(), "id", "");
    pi.host = CfgStr(obj.c_str(), "host", "");
    pi.port = static_cast<int>(CfgInt(obj.c_str(), "port", 50052));
    if (!pi.id.empty() && !pi.host.empty()) {
      result.push_back(std::move(pi));
    }
    p = obj_end + 1;
  }
  return result;
}

/* Parse routes from config. */
std::vector<Route> ParseRoutes(const char* cfg) {
  std::vector<Route> result;
  if (cfg == nullptr) return result;
  const std::string needle = "\"routes\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return result;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return result;
  ++p;
  while (*p == ' ' || *p == '\t') ++p;
  if (*p != '[') return result;
  ++p;

  while (*p) {
    while (*p == ' ' || *p == '\t' || *p == ',' || *p == '\n') ++p;
    if (*p == ']') break;
    if (*p != '{') break;
    ++p;
    const char* obj_end = std::strchr(p, '}');
    if (obj_end == nullptr) break;
    std::string obj(p, static_cast<std::size_t>(obj_end - p + 1));

    Route r;
    r.peer_id = CfgStr(obj.c_str(), "peer", "");
    auto bus_strs = CfgStrArray(obj.c_str(), "bus_types");
    for (const auto& s : bus_strs) {
      if (s == "can")    r.bus_types.push_back(BOAT_BUS_CAN);
      if (s == "canfd")  r.bus_types.push_back(BOAT_BUS_CANFD);
      if (s == "eth")    r.bus_types.push_back(BOAT_BUS_ETHERNET);
      if (s == "tcp")    r.bus_types.push_back(BOAT_BUS_TCP);
      if (s == "pdu")    r.bus_types.push_back(BOAT_BUS_PDU);
    }
    r.ifaces = CfgStrArray(obj.c_str(), "ifaces");
    if (!r.peer_id.empty()) {
      result.push_back(std::move(r));
    }
    p = obj_end + 1;
  }
  return result;
}

}  // namespace

/* ── Route::Matches ──────────────────────────────────────────────────── */

bool Route::Matches(const BoatFrame* frame) const {
  if (frame == nullptr) return false;

  // Check bus type
  if (!bus_types.empty()) {
    bool bus_match = false;
    for (auto bt : bus_types) {
      if (bt == frame->bus_type ||
          (bt == BOAT_BUS_CAN && frame->bus_type == BOAT_BUS_CANFD) ||
          (bt == BOAT_BUS_CANFD && frame->bus_type == BOAT_BUS_CAN)) {
        bus_match = true;
        break;
      }
    }
    if (!bus_match) return false;
  }

  // Check interface (if specified)
  if (!ifaces.empty() && frame->iface != nullptr && frame->iface[0] != '\0') {
    bool iface_match = false;
    for (const auto& iface : ifaces) {
      if (iface == frame->iface) {
        iface_match = true;
        break;
      }
    }
    if (!iface_match) return false;
  }

  return true;
}

/* ── Frame conversion helpers ────────────────────────────────────────── */

void BoatFrameToProto(const BoatFrame& src, boat::v1::Frame* dst) {
  dst->set_bus_type(static_cast<boat::v1::Frame::BusType>(src.bus_type));
  dst->set_iface(src.iface ? src.iface : "");
  dst->set_timestamp_ns(src.timestamp_ns);
  if (src.payload != nullptr && src.payload_len > 0) {
    dst->set_payload(src.payload, src.payload_len);
  }

  switch (src.bus_type) {
    case BOAT_BUS_CAN:
    case BOAT_BUS_CANFD: {
      auto* cm = dst->mutable_can();
      cm->set_can_id(src.meta.can.can_id);
      cm->set_dlc(src.meta.can.dlc);
      cm->set_flags(src.meta.can.flags);
      break;
    }
    case BOAT_BUS_ETHERNET: {
      auto* em = dst->mutable_eth();
      em->set_dst_mac(src.meta.eth.dst_mac, 6);
      em->set_src_mac(src.meta.eth.src_mac, 6);
      em->set_ethertype(src.meta.eth.ethertype);
      em->set_vlan_id(src.meta.eth.vlan_id);
      em->set_ip_version(src.meta.eth.ip_version);
      if (src.meta.eth.ip_version == 4) {
        em->set_src_ip(src.meta.eth.src_ip, 4);
        em->set_dst_ip(src.meta.eth.dst_ip, 4);
      } else if (src.meta.eth.ip_version == 6) {
        em->set_src_ip(src.meta.eth.src_ip, 16);
        em->set_dst_ip(src.meta.eth.dst_ip, 16);
      }
      break;
    }
    case BOAT_BUS_TCP: {
      auto* tm = dst->mutable_tcp();
      tm->set_ip_version(src.meta.tcp.ip_version);
      tm->set_src_port(src.meta.tcp.src_port);
      tm->set_dst_port(src.meta.tcp.dst_port);
      tm->set_conn_id(src.meta.tcp.conn_id);
      if (src.meta.tcp.ip_version == 4) {
        tm->set_src_ip(src.meta.tcp.src_ip, 4);
        tm->set_dst_ip(src.meta.tcp.dst_ip, 4);
      } else if (src.meta.tcp.ip_version == 6) {
        tm->set_src_ip(src.meta.tcp.src_ip, 16);
        tm->set_dst_ip(src.meta.tcp.dst_ip, 16);
      }
      break;
    }
    case BOAT_BUS_PDU: {
      auto* pm = dst->mutable_pdu();
      pm->set_pdu_id(src.meta.pdu.pdu_id);
      break;
    }
    default:
      break;
  }
}

BoatFrameOwner ProtoToBoatFrame(const boat::v1::Frame& src) {
  std::vector<uint8_t> payload(src.payload().begin(), src.payload().end());
  std::string iface = src.iface();

  switch (src.bus_type()) {
    case boat::v1::Frame::CAN:
    case boat::v1::Frame::CANFD: {
      const auto& cm = src.can();
      return BoatFrameOwner::Can(
          iface, cm.can_id(),
          static_cast<uint8_t>(cm.dlc()),
          static_cast<uint8_t>(cm.flags()),
          std::move(payload),
          src.bus_type() == boat::v1::Frame::CANFD);
    }
    case boat::v1::Frame::ETHERNET: {
      const auto& em = src.eth();
      uint8_t dm[6] = {}, sm[6] = {};
      std::memcpy(dm, em.dst_mac().data(),
                  std::min(em.dst_mac().size(), 6UL));
      std::memcpy(sm, em.src_mac().data(),
                  std::min(em.src_mac().size(), 6UL));
      return BoatFrameOwner::Ethernet(
          iface, dm, sm,
          static_cast<uint16_t>(em.ethertype()),
          static_cast<uint16_t>(em.vlan_id()),
          std::move(payload));
    }
    case boat::v1::Frame::PDU: {
      const auto& pm = src.pdu();
      return BoatFrameOwner::Pdu(
          iface, pm.pdu_id(), std::move(payload));
    }
    default:
      break;
  }
  return BoatFrameOwner::Can("", 0, 0, 0, {});
}

/* ── PeerConnection ──────────────────────────────────────────────────── */

void PeerConnection::Send(boat::v1::BackboneFrame frame) {
  {
    std::lock_guard<std::mutex> lk(send_mu);
    send_queue.push(std::move(frame));
  }
  send_cv.notify_one();
}

void PeerConnection::Stop() {
  stopped = true;
  send_cv.notify_all();
}

template <typename Stream>
void PeerConnection::WriterLoop(Stream* stream) {
  while (!stopped) {
    boat::v1::BackboneFrame frame;
    {
      std::unique_lock<std::mutex> lk(send_mu);
      send_cv.wait(lk, [this] {
        return !send_queue.empty() || stopped;
      });
      if (stopped) break;
      frame = std::move(send_queue.front());
      send_queue.pop();
    }

    if (!stream->Write(frame)) {
      std::fprintf(stderr, "[backbone] Write failed to peer '%s'\n",
                   peer_id.c_str());
      break;
    }
  }
}

// Explicit instantiation for the two stream types we use
template void PeerConnection::WriterLoop(
    grpc::ClientReaderWriter<boat::v1::BackboneFrame,
                             boat::v1::BackboneFrame>*);
template void PeerConnection::WriterLoop(
    grpc::ServerReaderWriter<boat::v1::BackboneFrame,
                             boat::v1::BackboneFrame>*);

void PeerConnection::ClientReaderLoop() {
  boat::v1::BackboneFrame incoming;
  while (client_stream && client_stream->Read(&incoming)) {
    if (on_receive && plugin) {
      on_receive(plugin, incoming);
    }
  }
  std::fprintf(stderr, "[backbone] Client read loop ended for peer '%s'\n",
               peer_id.c_str());
  Stop();
}

void PeerConnection::ServerReaderLoop() {
  boat::v1::BackboneFrame incoming;
  while (server_stream && server_stream->Read(&incoming)) {
    if (on_receive && plugin) {
      on_receive(plugin, incoming);
    }
  }
  std::fprintf(stderr, "[backbone] Server read loop ended for peer '%s'\n",
               peer_id.c_str());
  Stop();
}

bool PeerConnection::ConnectClient() {
  if (!channel) {
    std::fprintf(stderr, "[backbone] No channel for peer '%s'\n",
                 peer_id.c_str());
    return false;
  }
  if (!stub) {
    stub = boat::v1::BackboneService::NewStub(channel);
  }
  client_stream = stub->Connect(&client_ctx);
  if (!client_stream) {
    std::fprintf(stderr, "[backbone] Failed to connect to peer '%s'\n",
                 peer_id.c_str());
    return false;
  }
  std::fprintf(stderr, "[backbone] Connected to peer '%s'\n",
               peer_id.c_str());
  return true;
}

/* ── Handle incoming frames from a peer ──────────────────────────────── */

static void HandleIncomingFrame(BackbonePlugin* plugin,
                                 const boat::v1::BackboneFrame& bf) {
  if (plugin == nullptr) return;

  // Loop prevention: drop if we originated this frame
  if (bf.origin_gateway_id() == plugin->gateway_id) {
    return;
  }

  // Decrement hop count; drop if expired
  if (bf.hop_count() <= 1) return;
  uint32_t new_hop = bf.hop_count() - 1;

  // Convert and publish locally
  if (plugin->frame_publish_fn != nullptr && bf.has_frame()) {
    auto owner = ProtoToBoatFrame(bf.frame());
    plugin->frame_publish_fn(plugin->frame_publisher_ctx, owner.get());
  }
}

/* ── BackboneServiceImpl::Connect ────────────────────────────────────── */

grpc::Status BackboneServiceImpl::Connect(
    grpc::ServerContext* ctx,
    grpc::ServerReaderWriter<boat::v1::BackboneFrame,
                             boat::v1::BackboneFrame>* stream) {
  // Identify the peer from the client's address (best-effort)
  std::string peer_id = "unknown-server";
  auto peer = ctx->peer();
  if (!peer.empty()) {
    // Extract IP:port from something like "ipv4:10.0.0.2:50052"
    auto colon = peer.rfind(':');
    if (colon != std::string::npos) {
      peer_id = peer.substr(colon + 1);
    }
  }

  auto conn = std::make_shared<PeerConnection>();
  conn->peer_id = peer_id;
  conn->is_server_side = true;
  conn->server_ctx = ctx;
  conn->server_stream = stream;
  conn->plugin = plugin_;
  conn->on_receive = HandleIncomingFrame;

  plugin_->AddConnection(conn);

  // Reader thread
  conn->reader = std::thread([conn, stream]() {
    boat::v1::BackboneFrame incoming;
    while (stream->Read(&incoming)) {
      if (conn->on_receive && conn->plugin) {
        conn->on_receive(conn->plugin, incoming);
      }
    }
    conn->Stop();
  });

  // Writer thread
  conn->writer = std::thread([conn, stream]() {
    while (!conn->stopped) {
      boat::v1::BackboneFrame frame;
      {
        std::unique_lock<std::mutex> lk(conn->send_mu);
        conn->send_cv.wait(lk, [conn] {
          return !conn->send_queue.empty() || conn->stopped;
        });
        if (conn->stopped) break;
        frame = std::move(conn->send_queue.front());
        conn->send_queue.pop();
      }
      if (!stream->Write(frame)) break;
    }
  });

  // Wait for the client to disconnect
  conn->reader.join();
  conn->writer.join();

  plugin_->RemoveConnection(conn);
  std::fprintf(stderr, "[backbone] Server connection closed for peer '%s'\n",
               peer_id.c_str());
  return grpc::Status::OK;
}

/* ── BackbonePlugin implementation ───────────────────────────────────── */

void BackbonePlugin::StartServer() {
  if (backbone_port <= 0) return;

  std::string addr = "0.0.0.0:" + std::to_string(backbone_port);
  auto service = std::make_unique<BackboneServiceImpl>(this);

  grpc::ServerBuilder builder;
  builder.AddListeningPort(addr, grpc::InsecureServerCredentials());
  builder.RegisterService(service.get());
  grpc_server = builder.BuildAndStart();

  std::fprintf(stderr, "[backbone] gRPC server listening on %s\n",
               addr.c_str());

  // Keep the service alive — it's owned by the plugin
  (void)service.release();

  // Block forever (server runs in its own thread)
  grpc_server->Wait();
}

void BackbonePlugin::StopServer() {
  if (grpc_server) {
    shutting_down = true;
    grpc_server->Shutdown();
    grpc_server.reset();
  }
}

void BackbonePlugin::AddConnection(std::shared_ptr<PeerConnection> conn) {
  std::lock_guard<std::mutex> lk(peers_mu);
  connections.push_back(std::move(conn));
}

void BackbonePlugin::RemoveConnection(std::shared_ptr<PeerConnection> conn) {
  std::lock_guard<std::mutex> lk(peers_mu);
  auto it = std::remove(connections.begin(), connections.end(), conn);
  if (it != connections.end()) {
    connections.erase(it, connections.end());
  }
}

void BackbonePlugin::ConnectToPeers() {
  for (const auto& peer_info : peers) {
    std::string target = peer_info.host + ":" + std::to_string(peer_info.port);
    auto conn = std::make_shared<PeerConnection>();
    conn->peer_id = peer_info.id;
    conn->is_server_side = false;
    conn->channel = grpc::CreateChannel(target,
                                        grpc::InsecureChannelCredentials());
    conn->plugin = this;
    conn->on_receive = HandleIncomingFrame;

    if (!conn->ConnectClient()) {
      std::fprintf(stderr, "[backbone] Initial connect to peer '%s' at %s "
                   "failed — will retry on tick\n",
                   peer_info.id.c_str(), target.c_str());
      continue;
    }

    AddConnection(conn);

    // Reader thread
    conn->reader = std::thread([conn]() {
      boat::v1::BackboneFrame incoming;
      while (conn->client_stream &&
             conn->client_stream->Read(&incoming)) {
        if (conn->on_receive && conn->plugin) {
          conn->on_receive(conn->plugin, incoming);
        }
      }
      conn->Stop();
    });

    // Writer thread
    conn->writer = std::thread([conn]() {
      while (!conn->stopped) {
        boat::v1::BackboneFrame frame;
        {
          std::unique_lock<std::mutex> lk(conn->send_mu);
          conn->send_cv.wait(lk, [conn] {
            return !conn->send_queue.empty() || conn->stopped;
          });
          if (conn->stopped) break;
          frame = std::move(conn->send_queue.front());
          conn->send_queue.pop();
        }
        if (!conn->client_stream->Write(frame)) break;
      }
    });
  }
}

void BackbonePlugin::ForwardToPeers(const BoatFrame* frame) {
  if (frame == nullptr) return;

  // Don't forward self-sent frames (loopback prevention)
  bool self_sent = false;
  if (frame->bus_type == BOAT_BUS_CAN || frame->bus_type == BOAT_BUS_CANFD) {
    self_sent = (frame->meta.can.flags & BOAT_CAN_FLAG_SELF_SENT) != 0;
  } else if (frame->bus_type == BOAT_BUS_ETHERNET) {
    self_sent = (frame->meta.eth.flags & BOAT_ETH_FLAG_SELF_SENT) != 0;
  }
  if (self_sent) return;

  // Determine which peers to forward to
  std::vector<std::string> targets;
  for (const auto& route : routes) {
    if (route.Matches(frame)) {
      targets.push_back(route.peer_id);
    }
  }
  if (targets.empty()) return;

  // Serialize frame once
  boat::v1::BackboneFrame bf;
  bf.set_origin_gateway_id(gateway_id);
  bf.set_hop_count(static_cast<uint32_t>(max_hops));
  bf.set_sequence_number(next_seq++);
  BoatFrameToProto(*frame, bf.mutable_frame());

  // Send to matching peers
  std::lock_guard<std::mutex> lk(peers_mu);
  for (const auto& conn : connections) {
    bool match = false;
    for (const auto& t : targets) {
      if (t == conn->peer_id) {
        match = true;
        break;
      }
    }
    if (match && !conn->stopped) {
      conn->Send(bf);
    }
  }
}

/* ── Plugin C ABI entry points ───────────────────────────────────────── */

}  // namespace boat::backbone

namespace {

using namespace boat::backbone;

/* ── Plugin state holder (opaque to host) ────────────────────────────── */

BackbonePlugin* g_plugin = nullptr;

int backbone_initialize(void* ctx, const char* config_json) {
  auto* p = static_cast<BackbonePlugin*>(ctx);
  const char* cfg = config_json;
  g_plugin = p;

  p->gateway_id = CfgStr(cfg, "gateway_id", "default");
  p->backbone_port = static_cast<int>(CfgInt(cfg, "backbone_port", 50052));
  p->max_hops = static_cast<int>(CfgInt(cfg, "max_hops", 5));
  p->peers = ParsePeers(cfg);
  p->routes = ParseRoutes(cfg);

  // Start gRPC server in background thread
  p->server_thread = std::thread([p]() { p->StartServer(); });

  // Connect to peers
  p->ConnectToPeers();

  std::fprintf(stderr,
               "[backbone] init id=%s port=%d hops=%d peers=%zu routes=%zu\n",
               p->gateway_id.c_str(), p->backbone_port, p->max_hops,
               p->peers.size(), p->routes.size());
  return 0;
}

void backbone_on_frame(void* ctx, const BoatFrame* frame) {
  auto* p = static_cast<BackbonePlugin*>(ctx);
  p->ForwardToPeers(frame);
}

void backbone_on_tick(void* ctx, uint64_t tick) {
  auto* p = static_cast<BackbonePlugin*>(ctx);
  (void)tick;

  // Reconnect to peers that disconnected
  std::lock_guard<std::mutex> lk(p->peers_mu);
  for (size_t i = 0; i < p->connections.size(); ) {
    auto& conn = p->connections[i];
    if (conn->stopped) {
      // Clean up finished threads
      if (conn->reader.joinable()) conn->reader.join();
      if (conn->writer.joinable()) conn->writer.join();

      // For client connections, attempt reconnect
      if (!conn->is_server_side) {
        std::string target = "<unknown>";
        for (const auto& pi : p->peers) {
          if (pi.id == conn->peer_id) {
            target = pi.host + ":" + std::to_string(pi.port);
            break;
          }
        }
        std::fprintf(stderr, "[backbone] Reconnecting to peer '%s' at %s\n",
                     conn->peer_id.c_str(), target.c_str());

        auto new_conn = std::make_shared<PeerConnection>();
        new_conn->peer_id = conn->peer_id;
        new_conn->is_server_side = false;
        new_conn->channel = grpc::CreateChannel(
            target, grpc::InsecureChannelCredentials());
        new_conn->plugin = p;
        new_conn->on_receive = HandleIncomingFrame;

        if (new_conn->ConnectClient()) {
          new_conn->reader = std::thread([new_conn]() {
            new_conn->ClientReaderLoop();
          });
          new_conn->writer = std::thread([new_conn]() {
            while (!new_conn->stopped) {
              boat::v1::BackboneFrame frame;
              {
                std::unique_lock<std::mutex> lk(new_conn->send_mu);
                new_conn->send_cv.wait(lk, [new_conn] {
                  return !new_conn->send_queue.empty() || new_conn->stopped;
                });
                if (new_conn->stopped) break;
                frame = std::move(new_conn->send_queue.front());
                new_conn->send_queue.pop();
              }
              if (!new_conn->client_stream->Write(frame)) break;
            }
          });
          p->connections[i] = std::move(new_conn);
          ++i;
        } else {
          // Remove the dead connection
          p->connections.erase(p->connections.begin() + i);
        }
      } else {
        // Server-side connections just get removed (client disconnected)
        p->connections.erase(p->connections.begin() + i);
      }
    } else {
      ++i;
    }
  }
}

void backbone_set_frame_publisher(void* ctx, BoatFramePublishFn fn,
                                   void* publisher_ctx) {
  auto* p = static_cast<BackbonePlugin*>(ctx);
  p->frame_publish_fn = fn;
  p->frame_publisher_ctx = publisher_ctx;
}

void backbone_set_bus_publisher(void* ctx, BoatBusPublishFn fn,
                                 void* publisher_ctx) {
  auto* p = static_cast<BackbonePlugin*>(ctx);
  p->bus_publish_fn = fn;
  p->bus_publisher_ctx = publisher_ctx;
}

const char* backbone_declared_buses(void* ctx) {
  // Build declared buses string from routes
  static std::string cached;
  auto* p = static_cast<BackbonePlugin*>(ctx);
  bool has_can = false, has_canfd = false, has_eth = false;
  bool has_tcp = false, has_pdu = false;

  for (const auto& route : p->routes) {
    for (auto bt : route.bus_types) {
      if (bt == BOAT_BUS_CAN) has_can = true;
      if (bt == BOAT_BUS_CANFD) has_canfd = true;
      if (bt == BOAT_BUS_ETHERNET) has_eth = true;
      if (bt == BOAT_BUS_TCP) has_tcp = true;
      if (bt == BOAT_BUS_PDU) has_pdu = true;
    }
  }

  std::string s = "[";
  bool first = true;
  const auto add = [&](const char* n) {
    if (!first) s += ",";
    s += "\""; s += n; s += "\"";
    first = false;
  };
  if (has_can) add("can");
  if (has_canfd) add("canfd");
  if (has_eth) add("eth");
  if (has_tcp) add("tcp");
  if (has_pdu) add("pdu");
  if (first) {
    // If no routes, accept all by default
    return "";  // empty string = accept all
  }
  s += "]";
  cached = s;
  return cached.c_str();
}

void backbone_shutdown(void* ctx) {
  auto* p = static_cast<BackbonePlugin*>(ctx);
  p->shutting_down = true;

  // Stop all peer connections
  {
    std::lock_guard<std::mutex> lk(p->peers_mu);
    for (auto& conn : p->connections) {
      conn->Stop();
    }
  }

  // Stop gRPC server
  p->StopServer();

  // Join all connection threads
  {
    std::lock_guard<std::mutex> lk(p->peers_mu);
    for (auto& conn : p->connections) {
      if (conn->reader.joinable()) conn->reader.join();
      if (conn->writer.joinable()) conn->writer.join();
    }
    p->connections.clear();
  }

  // Join server thread
  if (p->server_thread.joinable()) {
    p->server_thread.join();
  }

  std::fprintf(stderr, "[backbone] shutdown complete\n");
}

}  // namespace

/* ── Exported C ABI ──────────────────────────────────────────────────── */

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &backbone_initialize;
    vt.on_tick             = &backbone_on_tick;
    vt.shutdown            = &backbone_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = &backbone_set_bus_publisher;
    vt.set_pdu_publisher   = nullptr;
    vt.on_frame            = &backbone_on_frame;
    vt.set_frame_publisher = &backbone_set_frame_publisher;
    vt.declared_buses      = &backbone_declared_buses;
    vt.on_signal           = nullptr;
    return vt;
  }();

  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new boat::backbone::BackbonePlugin{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<boat::backbone::BackbonePlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() {
  return BOAT_PLUGIN_ABI_VERSION;
}
