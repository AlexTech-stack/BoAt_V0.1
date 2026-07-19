#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "event_store/event_store.h"
#include "signal/signal_bus.h"

namespace boat::replay {

/* Records always-on signal-bus values into the event store so they can be
   replayed as named signals via ReplayController::StartFromEvents (the inverse
   of Phase 1.5's signal replay). This is what makes an end-to-end device-curve
   record -> replay loop possible: device plugins publish measurements onto the
   SignalBus, this recorder persists them as EventRecords under a simulation-id
   tag, and `replay from-events --sim-id <tag>` republishes them.

   Numeric/bool signals only (stored as a raw little-endian double blob, the
   codebase convention). Signal callbacks only enqueue; a background thread does
   the SQLite writes, so the hot signal-publish path is never blocked. */
class BusSignalRecorder {
 public:
  struct Config {
    std::string simulation_id = "devrec";
    // Record only signals whose name starts with one of these prefixes.
    // Empty = record every numeric/bool signal.
    std::vector<std::string> prefixes;
    std::chrono::nanoseconds tick_duration{std::chrono::milliseconds(1)};
  };

  BusSignalRecorder(boat::core::SignalBus& bus, boat::store::IEventStore& store,
                    Config config);
  ~BusSignalRecorder();

  BusSignalRecorder(const BusSignalRecorder&) = delete;
  BusSignalRecorder& operator=(const BusSignalRecorder&) = delete;

  void Start();
  void Stop();

  std::uint64_t RecordedCount() const { return recorded_.load(); }

 private:
  void OnSignal(const boat::core::BusSignal& signal);
  void WriterLoop();
  bool Matches(const std::string& name) const;

  boat::core::SignalBus& bus_;
  boat::store::IEventStore& store_;
  Config config_;

  boat::core::BusSubscriberId sub_id_{0};
  std::chrono::steady_clock::time_point epoch_;
  std::atomic<bool> running_{false};
  std::atomic<std::uint64_t> recorded_{0};
  std::uint64_t seq_{0};

  std::mutex queue_mutex_;
  std::condition_variable queue_cv_;
  std::deque<boat::store::EventRecord> queue_;
  std::thread writer_thread_;
};

}  // namespace boat::replay
