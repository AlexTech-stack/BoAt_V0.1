#include "pdu/transmission_engine.h"

#include <vector>

namespace boat::hil {

TransmissionEngine::TransmissionEngine(SendCallback send_cb)
    : send_cb_(std::move(send_cb)) {}

void TransmissionEngine::ConfigureSchedule(uint32_t pdu_id,
                                            const PduSchedule& schedule) {
  std::lock_guard<std::mutex> lock(mutex_);
  ScheduleState state;
  state.schedule      = schedule;
  state.next_tick_ms  = kTickNotScheduled;
  state.remaining_reps = 0;
  states_[pdu_id]     = std::move(state);
}

void TransmissionEngine::RemoveSchedule(uint32_t pdu_id) {
  std::lock_guard<std::mutex> lock(mutex_);
  states_.erase(pdu_id);
}

void TransmissionEngine::UpdatePayload(
    uint32_t pdu_id, const std::vector<uint8_t>& payload) {
  // Collect what to send while holding the lock, then send without it to
  // avoid deadlock when send_cb_ re-enters (e.g. via PduRouter::SendPdu
  // which calls UpdatePayload again).
  bool should_send = false;
  std::vector<uint8_t> send_payload;
  uint32_t fast_ms = 0;
  uint32_t repetitions = 0;

  {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = states_.find(pdu_id);
    if (it == states_.end()) return;

    const bool changed = (it->second.last_payload != payload);
    it->second.last_payload = payload;

    if (!changed) return;

    const auto st = it->second.schedule.send_type;
    if (st != SendType::kOnChange && st != SendType::kMixed) return;

    should_send = true;
    send_payload = payload;
    if (it->second.schedule.repetitions > 0) {
      it->second.remaining_reps = it->second.schedule.repetitions;
      it->second.next_rep_tick_ms = 0;
      fast_ms = it->second.schedule.fast_ms;
      repetitions = it->second.schedule.repetitions;
    }
  }  // mutex released

  if (should_send) {
    send_cb_(pdu_id, send_payload);
  }
}

void TransmissionEngine::OnTick(uint64_t tick_ms) {
  // Collect send operations under the lock, then invoke callbacks without it.
  struct SendOp { uint32_t pdu_id; std::vector<uint8_t> payload; };
  std::vector<SendOp> to_send;

  {
    std::lock_guard<std::mutex> lock(mutex_);
    for (auto& [pdu_id, state] : states_) {
      if (state.schedule.send_type == SendType::kNone) continue;

      bool should_send = false;

      if (state.schedule.send_type == SendType::kCyclic ||
          state.schedule.send_type == SendType::kMixed) {
        // First tick: initialize the schedule without sending.
        // This ensures the first send waits one full cycle.
        if (state.next_tick_ms == kTickNotScheduled) {
          state.next_tick_ms = tick_ms + state.schedule.cycle_ms;
        } else if (tick_ms >= state.next_tick_ms) {
          should_send = true;
          state.next_tick_ms = tick_ms + state.schedule.cycle_ms;
        }
      }

      if (state.schedule.send_type == SendType::kOnChange ||
          state.schedule.send_type == SendType::kMixed) {
        if (state.remaining_reps > 0 && tick_ms >= state.next_rep_tick_ms) {
          should_send = true;
          state.remaining_reps--;
          if (state.remaining_reps > 0) {
            state.next_rep_tick_ms = tick_ms + state.schedule.fast_ms;
          }
        }
      }

      if (should_send) {
        to_send.push_back({pdu_id, state.last_payload});
      }
    }
  }  // mutex released before calling callbacks

  for (const auto& op : to_send) {
    send_cb_(op.pdu_id, op.payload);
  }
}

}  // namespace boat::hil
