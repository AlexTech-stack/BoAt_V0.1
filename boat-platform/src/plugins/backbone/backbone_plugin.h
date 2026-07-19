#pragma once

#include <atomic>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <vector>

#include <grpcpp/grpcpp.h>
#include <grpcpp/server.h>

#include "boat/frame.h"
#include "boat/plugin.h"
#include "boat/v1/backbone.pb.h"
#include "boat/v1/backbone.grpc.pb.h"

namespace boat {
namespace backbone {

/* ── Forward declarations ────────────────────────────────────────────── */

struct PeerConnection;

/* ── Route: decides which frames to forward to which peer ────────────── */

struct Route {
  std::string peer_id;
  std::vector<BoatBusType> bus_types;
  std::vector<std::string> ifaces;

  bool Matches(const BoatFrame* frame) const;
};

/* ── Peer info from config ───────────────────────────────────────────── */

struct PeerInfo {
  std::string id;
  std::string host;
  int port = 50052;
};

/* ── Plugin state ────────────────────────────────────────────────────── */

struct BackbonePlugin {
  // config
  std::string gateway_id;
  int backbone_port = 50052;
  int max_hops = 5;
  std::vector<PeerInfo> peers;
  std::vector<Route> routes;

  // wiring
  BoatFramePublishFn frame_publish_fn = nullptr;
  void* frame_publisher_ctx = nullptr;
  BoatBusPublishFn bus_publish_fn = nullptr;
  void* bus_publisher_ctx = nullptr;

  // gRPC
  std::unique_ptr<grpc::Server> grpc_server;
  std::unique_ptr<grpc::ServerCompletionQueue> cq;

  // peer connections
  std::mutex peers_mu;
  std::vector<std::shared_ptr<PeerConnection>> connections;

  // state
  std::atomic<uint64_t> next_seq{1};
  std::atomic<bool> shutting_down{false};
  std::thread server_thread;

  void StartServer();
  void StopServer();
  void ConnectToPeers();
  void AddConnection(std::shared_ptr<PeerConnection> conn);
  void RemoveConnection(std::shared_ptr<PeerConnection> conn);
  void ForwardToPeers(const BoatFrame* frame);
};

/* ── PeerConnection — bidirectional gRPC stream wrapper ─────────────── */

struct PeerConnection : std::enable_shared_from_this<PeerConnection> {
  std::string peer_id;
  bool is_server_side = false;

  // Server-side stream (non-owning — owned by gRPC runtime)
  grpc::ServerReaderWriter<boat::v1::BackboneFrame,
                           boat::v1::BackboneFrame>* server_stream = nullptr;
  grpc::ServerContext* server_ctx = nullptr;

  // Client-side stream
  std::shared_ptr<grpc::Channel> channel;
  std::unique_ptr<boat::v1::BackboneService::Stub> stub;
  grpc::ClientContext client_ctx;
  std::unique_ptr<grpc::ClientReaderWriter<boat::v1::BackboneFrame,
                                           boat::v1::BackboneFrame>> client_stream;

  // Threading
  std::thread reader;
  std::thread writer;
  std::atomic<bool> stopped{false};

  // Send queue
  std::mutex send_mu;
  std::condition_variable send_cv;
  std::queue<boat::v1::BackboneFrame> send_queue;

  // Backreference to plugin (raw pointer, plugin outlives connections)
  BackbonePlugin* plugin = nullptr;

  // Callback for received frames
  void (*on_receive)(BackbonePlugin* plugin,
                     const boat::v1::BackboneFrame& frame) = nullptr;

  // Send a frame to the peer (thread-safe)
  void Send(boat::v1::BackboneFrame frame);

  // Stop all threads
  void Stop();

  // Writer loop — shared between client and server
  template <typename Stream>
  void WriterLoop(Stream* stream);

  // Client-side reader
  void ClientReaderLoop();

  // Server-side reader
  void ServerReaderLoop();

  // Attempt client connection
  bool ConnectClient();
};

/* ── BackboneServiceImpl ─────────────────────────────────────────────── */

class BackboneServiceImpl final
    : public boat::v1::BackboneService::Service {
 public:
  explicit BackboneServiceImpl(BackbonePlugin* plugin) : plugin_(plugin) {}

  grpc::Status Connect(
      grpc::ServerContext* ctx,
      grpc::ServerReaderWriter<boat::v1::BackboneFrame,
                               boat::v1::BackboneFrame>* stream) override;

 private:
  BackbonePlugin* plugin_;
};

/* ── Helpers ─────────────────────────────────────────────────────────── */

void BoatFrameToProto(const BoatFrame& src, boat::v1::Frame* dst);
BoatFrameOwner ProtoToBoatFrame(const boat::v1::Frame& src);

}  // namespace backbone
}  // namespace boat
