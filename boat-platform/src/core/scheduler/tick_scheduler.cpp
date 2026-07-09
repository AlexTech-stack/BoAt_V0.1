#include "scheduler/tick_scheduler.h"

#include <algorithm>
#include <chrono>

#include "determinism/determinism_engine.h"

namespace boat::core {

void TickScheduler::SetOnTickHook(std::function<void(std::uint64_t)> hook) {
  std::lock_guard<std::mutex> lock(on_tick_hook_mutex_);
  on_tick_hook_ = std::move(hook);
}

TickScheduler::TickScheduler(SimClock& clock, EventBus& event_bus, DeterminismEngine& determinism,
                             std::size_t thread_count)
    : clock_(clock),
      event_bus_(event_bus),
      determinism_(determinism),
      thread_count_(std::max<std::size_t>(1, thread_count)),
      local_queues_(thread_count_),
      local_queue_mutexes_(thread_count_) {}

TickScheduler::~TickScheduler() { Stop(); }

void TickScheduler::Start() {
  if (running_.exchange(true, std::memory_order_acq_rel)) {
    return;
  }
  paused_.store(false, std::memory_order_release);
  for (std::size_t i = 0; i < thread_count_; ++i) {
    workers_.emplace_back(&TickScheduler::WorkerLoop, this, i);
  }
  coordinator_ = std::thread(&TickScheduler::CoordinatorLoop, this);
}

void TickScheduler::Pause() {
  paused_.store(true, std::memory_order_release);
  // Wake any ExecuteTick waiting for task completion so it can exit cleanly.
  pending_cv_.notify_all();
  // Wake workers waiting on work_cv_ so they see the paused flag.
  work_cv_.notify_all();
}

void TickScheduler::Resume() {
  paused_.store(false, std::memory_order_release);
  pause_cv_.notify_all();
}

void TickScheduler::Stop() {
  if (!running_.exchange(false, std::memory_order_acq_rel)) {
    return;
  }
  paused_.store(false, std::memory_order_release);

  for (std::size_t i = 0; i < thread_count_; ++i) {
    std::lock_guard<std::mutex> lock(local_queue_mutexes_[i]);
    local_queues_[i].clear();
  }
  {
    std::lock_guard<std::mutex> lock(shared_tasks_mutex_);
    shared_tasks_.clear();
  }

  pause_cv_.notify_all();
  work_cv_.notify_all();
  pending_cv_.notify_all();
  if (coordinator_.joinable()) {
    coordinator_.join();
  }
  for (auto& worker : workers_) {
    if (worker.joinable()) {
      worker.join();
    }
  }
  workers_.clear();
  pending_tasks_.store(0, std::memory_order_release);
}

void TickScheduler::Step(std::uint64_t n) {
  if (n == 0) {
    return;
  }

  if (!running_.load(std::memory_order_acquire)) {
    for (std::uint64_t i = 0; i < n; ++i) {
      ExecuteTickSynchronously(clock_.tick() + 1);
    }
    return;
  }

  if (paused_.load(std::memory_order_acquire)) {
    for (std::uint64_t i = 0; i < n; ++i) {
      ExecuteTickSynchronously(clock_.tick() + 1);
    }
    return;
  }

  for (std::uint64_t i = 0; i < n; ++i) {
    const std::uint64_t tick = clock_.tick() + 1;
    ExecuteTick(tick);
  }
}

bool TickScheduler::EnqueueTask(std::function<void()> task) {
  if (!running_.load(std::memory_order_acquire)) {
    return false;
  }
  pending_tasks_.fetch_add(1, std::memory_order_relaxed);
  const std::size_t worker = next_worker_.fetch_add(1, std::memory_order_relaxed) % thread_count_;
  {
    std::lock_guard<std::mutex> lock(local_queue_mutexes_[worker]);
    local_queues_[worker].push_back(std::move(task));
  }
  work_cv_.notify_one();
  return true;
}

void TickScheduler::ExecuteTick(std::uint64_t tick) {
  if (!running_.load(std::memory_order_acquire)) {
    return;
  }
  if (!EnqueueTask([]() {})) {
    return;
  }
  std::unique_lock<std::mutex> lock(pending_mutex_);
  pending_cv_.wait(lock, [this] {
    return pending_tasks_.load(std::memory_order_acquire) == 0 ||
           !running_.load(std::memory_order_acquire) ||
           paused_.load(std::memory_order_acquire);
  });
  if (pending_tasks_.load(std::memory_order_acquire) != 0) {
    // Task not completed (paused or stopped) — abandon without advancing state.
    return;
  }
  // ── Deterministic Tick Pipeline ──────────────────────────────────────
  //
  // Every simulation tick executes these steps in strict order.  New
  // features MUST fit into one of these steps rather than introducing
  // new execution phases outside the pipeline.
  //
  //   1. determinism_.BeforeTick(tick)
  //      Seed the RNG with seed ⊕ tick so that every (seed, tick) pair
  //      produces an identical random sequence regardless of host timing.
  //
  //   2. event_bus_.Dispatch()
  //      Drain all events queued since the last tick (published by replay,
  //      HIL bridges, IPC, or plugin callbacks).  Events are dispatched
  //      in FIFO order to handlers that subscribed to each event type.
  //
  //      ⚠ Handlers run synchronously on this thread.  A blocking handler
  //        stalls the entire pipeline.
  //
  //   3. PluginManager::TickAll(tick)  (via on_tick_hook_)
  //      Invoke every loaded plugin's on_tick callback in deterministic
  //      order (plugins_ is a std::map sorted by .so path).  Each plugin
  //      may publish signals, CAN frames, ETH frames, PDU frames, or
  //      bus-signal values.  Those outputs are queued for the next tick's
  //      Dispatch() — they do NOT take effect immediately.
  //
  //   4. clock_.Step()
  //      Advance the simulation tick counter.  This is the only place
  //      tick is incremented.  clock_.tick() is stable for the entire
  //      duration of steps 1-3.
  //
  // Steps 1-4 are identical for coordinator-driven ticks (ExecuteTick)
  // and for manual stepping (ExecuteTickSynchronously).  The only
  // difference is that ExecuteTick inserts a worker-pool barrier between
  // enqueue and step 1 so that any prior tick's background work has
  // completed before we start the next tick.
  // ──────────────────────────────────────────────────────────────────────
  determinism_.BeforeTick(tick);
  event_bus_.Dispatch();
  clock_.Step();
  {
    std::lock_guard<std::mutex> lock(on_tick_hook_mutex_);
    if (on_tick_hook_) on_tick_hook_(tick);
  }
}

void TickScheduler::ExecuteTickSynchronously(std::uint64_t tick) {
  determinism_.BeforeTick(tick);
  // Keep manual stepping order aligned with coordinator-driven ticks.
  event_bus_.Dispatch();
  clock_.Step();
  {
    std::lock_guard<std::mutex> lock(on_tick_hook_mutex_);
    if (on_tick_hook_) on_tick_hook_(tick);
  }
}

void TickScheduler::CoordinatorLoop() {
  // Each tick represents 10 ms of simulation time; sleep to match real time.
  constexpr auto kTickInterval = std::chrono::milliseconds(1);
  while (running_.load(std::memory_order_acquire)) {
    {
      std::unique_lock<std::mutex> pause_lock(pause_mutex_);
      pause_cv_.wait(pause_lock, [this] {
        return !running_.load(std::memory_order_acquire) || !paused_.load(std::memory_order_acquire);
      });
    }
    if (!running_.load(std::memory_order_acquire)) {
      break;
    }
    const auto tick_start = std::chrono::steady_clock::now();
    Step(1);
    const auto elapsed = std::chrono::steady_clock::now() - tick_start;
    if (elapsed < kTickInterval) {
      std::this_thread::sleep_for(kTickInterval - elapsed);
    }
  }
}

void TickScheduler::WorkerLoop(std::size_t worker_index) {
  while (running_.load(std::memory_order_acquire)) {
    if (paused_.load(std::memory_order_acquire)) {
      std::unique_lock<std::mutex> pause_lock(pause_mutex_);
      pause_cv_.wait(pause_lock, [this] {
        return !running_.load(std::memory_order_acquire) || !paused_.load(std::memory_order_acquire);
      });
      continue;
    }

    std::function<void()> task;
    bool got_task = false;
    {
      std::lock_guard<std::mutex> lock(local_queue_mutexes_[worker_index]);
      if (!local_queues_[worker_index].empty()) {
        task = std::move(local_queues_[worker_index].front());
        local_queues_[worker_index].pop_front();
        got_task = true;
      }
    }

    if (!got_task) {
      for (std::size_t i = 0; i < thread_count_; ++i) {
        if (i == worker_index) {
          continue;
        }
        std::lock_guard<std::mutex> lock(local_queue_mutexes_[i]);
        if (!local_queues_[i].empty()) {
          task = std::move(local_queues_[i].back());
          local_queues_[i].pop_back();
          got_task = true;
          break;
        }
      }
    }

    if (!got_task) {
      {
        std::lock_guard<std::mutex> lock(shared_tasks_mutex_);
        if (!shared_tasks_.empty()) {
          task = std::move(shared_tasks_.front());
          shared_tasks_.pop_front();
          got_task = true;
        }
      }
    }

    if (!got_task) {
      {
        std::unique_lock<std::mutex> lock(work_mutex_);
        work_cv_.wait(lock, [this] {
          return !running_.load(std::memory_order_acquire) ||
                 paused_.load(std::memory_order_acquire) ||
                 pending_tasks_.load(std::memory_order_acquire) > 0;
        });
      }
      continue;
    }

    task();
    if (pending_tasks_.fetch_sub(1, std::memory_order_acq_rel) == 1) {
      std::lock_guard<std::mutex> lock(pending_mutex_);
      pending_cv_.notify_all();
    }
  }
}

}  // namespace boat::core
