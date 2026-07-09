#pragma once

#include <cstdint>
#include <functional>
#include <mutex>
#include <unordered_map>
#include <vector>

#include "pdu/pdu_types.h"

namespace boat::hil {

/* Manages automatic PDU transmission schedules.
 *
 * Supports three modes (plus "none"):
 *   kCyclic   — send at a fixed period
 *   kOnChange — send only when the payload changes, with optional n-times
 *               fast repetitions
 *   kMixed    — cyclic background with OnChange trigger and fast reps
 *
 * OnTick() must be called by the external scheduler at a fixed interval
 * (typically 1-100 ms).  UpdatePayload() must be called after every
 * successful manual SendPdu() so the engine can detect OnChange events.
 */
class TransmissionEngine {
 public:
  using SendCallback = std::function<bool(uint32_t pdu_id,
                                          const std::vector<uint8_t>& payload)>;

  explicit TransmissionEngine(SendCallback send_cb);

  void ConfigureSchedule(uint32_t pdu_id, const PduSchedule& schedule);
  void RemoveSchedule(uint32_t pdu_id);
  void UpdatePayload(uint32_t pdu_id, const std::vector<uint8_t>& payload);
  void OnTick(uint64_t tick_ms);

 private:
  static constexpr uint64_t kTickNotScheduled = ~0ULL;

  struct ScheduleState {
    PduSchedule           schedule;
    std::vector<uint8_t>  last_payload;   // for OnChange detection
    uint64_t              next_tick_ms{kTickNotScheduled};
    uint32_t              remaining_reps{0};
    uint64_t              next_rep_tick_ms{0};
  };

  SendCallback send_cb_;
  std::mutex   mutex_;
  std::unordered_map<uint32_t, ScheduleState> states_;
};

}  // namespace boat::hil
