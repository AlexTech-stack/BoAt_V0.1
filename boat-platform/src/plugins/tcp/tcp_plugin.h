#pragma once

#include <boat/plugin.h>

#include <arpa/inet.h>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace boat {
namespace tcp {

// ── Callback types (C-compatible) ─────────────────────────────────────────

using TcpOnData  = void (*)(void* user_ctx, int conn_id,
                             const uint8_t* data, uint32_t len);
using TcpOnEvent = void (*)(void* user_ctx, int conn_id, int event);

constexpr int TCP_EVENT_CONNECTED = 0;
constexpr int TCP_EVENT_CLOSED    = 1;
constexpr int TCP_EVENT_RST       = 2;
constexpr int TCP_EVENT_ACCEPTED  = 3;
constexpr int TCP_EVENT_ERROR     = 4;

// ── TCP state machine ─────────────────────────────────────────────────────

enum TcpState : uint8_t {
  TCP_CLOSED,
  TCP_LISTEN,
  TCP_SYN_SENT,
  TCP_SYN_RCVD,
  TCP_ESTABLISHED,
  TCP_CLOSE_WAIT,
  TCP_CLOSING,
  TCP_LAST_ACK,
  TCP_FIN_WAIT_1,
  TCP_FIN_WAIT_2,
  TCP_TIME_WAIT,
};

// ── Per-connection state ──────────────────────────────────────────────────

struct TcpConnection {
  int       conn_id{-1};
  int       listener_id{-1};

  std::array<uint8_t, 16> src_ip{};
  std::array<uint8_t, 16> dst_ip{};
  int       af{AF_INET};
  uint16_t  src_port{0};
  uint16_t  dst_port{0};

  TcpState  state{TCP_CLOSED};

  uint32_t  my_seq{0};
  uint32_t  my_ack{0};
  uint32_t  their_seq{0};
  uint32_t  their_ack{0};

  // Pending outgoing data
  std::vector<uint8_t> send_buffer;

  // Retransmit
  std::vector<uint8_t> unacked_segment;
  std::chrono::steady_clock::time_point retransmit_at;
  int       retry_count{0};

  // Callbacks
  TcpOnData  on_data{nullptr};
  TcpOnEvent on_event{nullptr};
  void*      user_ctx{nullptr};

  int       mss{1460};

  // Peer-advertised receive window
  uint32_t  peer_window{65535};

  // Keepalive
  std::chrono::steady_clock::time_point last_activity;
  int keepalive_probes_sent{0};

  // Zero-window probe (persist timer)
  bool persist_active{false};
  std::chrono::steady_clock::time_point persist_at;
  int persist_count{0};

  // Out-of-order receive buffer: seq_start → {seq_start, data}
  std::map<uint32_t, std::vector<uint8_t>> receive_buffer;

  // TIME_WAIT expiry
  std::chrono::steady_clock::time_point time_wait_until{};
};

// ── Listener state ────────────────────────────────────────────────────────

struct TcpListener {
  int       listener_id{-1};
  std::array<uint8_t, 16> bind_ip{};
  int       af{AF_INET};
  uint16_t  bind_port{0};
  TcpOnData  on_data{nullptr};
  TcpOnEvent on_event{nullptr};
  void*      user_ctx{nullptr};
};

// ── Plugin state ──────────────────────────────────────────────────────────

struct TcpPlugin {
  BoatFramePublishFn frame_publish_fn{nullptr};
  void*              frame_publisher_ctx{nullptr};
  int               raw_sock{-1};
  int               raw_ifindex{-1};
  std::string       raw_iface;
  std::thread       raw_rx_thread;
  std::atomic<bool> raw_rx_running{false};
  std::mutex        arp_mutex;
  // Cache: dst_ip_bytes + af → mac_bytes
  std::unordered_map<std::string, std::array<uint8_t, 6>> arp_cache;

  std::unordered_map<int, TcpConnection> connections;
  std::unordered_map<int, TcpListener>  listeners;
  int next_id{1};
  std::recursive_mutex  mutex;
  std::thread tx_thread;
  std::condition_variable_any tx_cv;
  std::atomic<bool> running{false};

  uint32_t retry_ms{1000};
  uint32_t max_retries{5};
  int      default_mss{1460};
  uint16_t rx_window{65535};  // advertised receive window
  bool     nagle_enabled{true};
  uint32_t keepalive_idle_ms{7200000};
  uint32_t keepalive_interval_ms{75000};
  uint32_t keepalive_retry_count{9};
  uint32_t time_wait_ms{120000};  // 2*MSL
};

// ── Helpers ────────────────────────────────────────────────────────────────

// Parse MSS option from TCP options. Returns the MSS value or 536 (RFC 1122
// default) if no MSS option is present.
inline uint16_t ParseMssOption(const uint8_t* options, uint32_t opt_len) {
  uint32_t offset = 0;
  while (offset < opt_len) {
    uint8_t kind = options[offset];
    if (kind == 0) break;
    if (kind == 1) { ++offset; continue; }
    if (offset + 1 >= opt_len) break;
    uint8_t len = options[offset + 1];
    if (kind == 2 && len == 4 && offset + 4 <= opt_len) {
      return static_cast<uint16_t>((options[offset + 2] << 8) | options[offset + 3]);
    }
    offset += len;
  }
  return 536;
}

// ── Extern "C" ABI (implemented in tcp_plugin.cpp) ────────────────────────

extern "C" {

BoatPlugin* boat_plugin_create();
void        boat_plugin_destroy(BoatPlugin* plugin);
uint32_t    boat_plugin_abi_version();

extern "C" uint32_t boat_plugin_abi_version();

}  // extern "C"

}  // namespace tcp
}  // namespace boat
