#pragma once

#include <atomic>
#include <cstddef>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

#include "event/event_bus.h"
#include "scheduler/sim_clock.h"

namespace boat::core {

class DeterminismEngine;

class TickScheduler {
 public:
  TickScheduler(SimClock& clock, EventBus& event_bus, DeterminismEngine& determinism,
                std::size_t thread_count = std::thread::hardware_concurrency());
  ~TickScheduler();

  void Start();
  void Pause();
  void Resume();
  void Stop();
  void Step(std::uint64_t n = 1);

  /* Optional hook called after every tick (including synchronous steps).
     Set before Start(); safe to call from any thread. */
  void SetOnTickHook(std::function<void(std::uint64_t)> hook);

 private:
  void WorkerLoop(std::size_t worker_index);
  void CoordinatorLoop();
  bool EnqueueTask(std::function<void()> task);
  void ExecuteTick(std::uint64_t tick);
  void ExecuteTickSynchronously(std::uint64_t tick);

  SimClock& clock_;
  EventBus& event_bus_;
  DeterminismEngine& determinism_;
  std::size_t thread_count_;

  std::vector<std::thread> workers_;
  std::thread coordinator_;
  std::atomic<bool> running_{false};
  std::atomic<bool> paused_{false};
  std::condition_variable pause_cv_;
  std::mutex pause_mutex_;

  std::deque<std::function<void()>> shared_tasks_;
  std::mutex shared_tasks_mutex_;

  std::vector<std::deque<std::function<void()>>> local_queues_;
  std::vector<std::mutex> local_queue_mutexes_;
  std::atomic<std::size_t> next_worker_{0};

  std::condition_variable work_cv_;
  std::mutex work_mutex_;

  std::atomic<std::size_t> pending_tasks_{0};
  std::condition_variable pending_cv_;
  std::mutex pending_mutex_;

  std::function<void(std::uint64_t)> on_tick_hook_;
  std::mutex on_tick_hook_mutex_;
};

}  // namespace boat::core
