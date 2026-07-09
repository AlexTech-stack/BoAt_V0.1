#pragma once

#include <boat/plugin.h>
#include <boat/can_tp.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

/* ISO 15765-2 N-SDU connection state.
   A connection is keyed by source_addr and handles both TX and RX
   directions within one session (source_addr ↔ target_addr). */
struct NsduConnection {
  uint32_t nsdu_id;
  uint32_t source_addr;
  uint32_t target_addr;
  CanTpConfig config;

  // RX reassembly state
  enum RxState { RX_IDLE, RX_WAIT_CF } rx_state{RX_IDLE};
  std::vector<uint8_t> rx_buffer;
  uint32_t rx_expected_len{0};
  uint8_t  rx_next_seq{0};

  // TX state machine
  enum TxState {
    TX_IDLE,      // no TX in progress
    TX_WAIT_FC,   // FF sent, awaiting Flow Control from peer
    TX_SEND_CF,   // FC received, sending Consecutive Frames
    TX_COMPLETE   // all CFs sent (transitions to TX_IDLE on next check)
  } tx_state{TX_IDLE};

  std::vector<uint8_t> tx_buffer;
  uint32_t tx_offset{0};
  uint8_t  tx_seq{0};
  uint8_t  tx_bs_remaining{0};    // BS remaining before needing next FC
  uint8_t  tx_bs_original{0};     // BS from the received FC (0 = unlimited)
  uint32_t tx_stmin_us{0};        // STmin from peer in microseconds
  std::chrono::steady_clock::time_point tx_next_send_time;

  // RX CF tracking for re-FC (BS > 0)
  uint32_t rx_cf_count{0};
};

/* CanTp plugin state. */
struct CanTpPlugin {
  BoatFramePublishFn  frame_publish_fn{nullptr};
  void*               frame_publisher_ctx{nullptr};
  BoatPduPublishFn    pdu_publish_fn{nullptr};
  void*               pdu_publisher_ctx{nullptr};
  std::unordered_map<uint32_t, NsduConnection> connections;  // keyed by source_addr
  std::string         iface;

  // TX pacing thread + synchronization
  std::thread         tx_thread;
  std::mutex          tx_mutex;
  std::condition_variable tx_cv;
  std::atomic<bool>   tx_stop{false};
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

// Standalone CanTp API
extern "C" int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                               const uint8_t* data, uint32_t len);
extern "C" int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config);
