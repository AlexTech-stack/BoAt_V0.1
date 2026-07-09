#pragma once

#include <chrono>
#include <cstdint>
#include <memory>

namespace boat::hil {

/* Abstract tick timer: blocks until the next tick boundary.
 *
 * Sole backend on Linux:
 *   TimerfdTickTimer — Linux timerfd (μs–ms range, drift-free)
 * The portable SleepTickTimer is provided as a non-Linux fallback but is
 * never selected by the factory — this codebase is Linux-only.
 */
class TickTimer {
 public:
  virtual ~TickTimer() = default;

  /* Initialise the timer.  Returns true on success. */
  virtual bool Init(std::chrono::nanoseconds interval) = 0;

  /* Block until the next tick.  Returns false if stopped. */
  virtual bool WaitForNextTick() = 0;

  /* Block until an absolute time point (drift-free).  Returns false if
   * stopped.  Timers implement via timerfd+TFD_TIMER_ABSTIME. */
  virtual bool WaitUntil(std::chrono::steady_clock::time_point deadline) = 0;

  /* Stop the timer (may interrupt a blocked WaitForNextTick). */
  virtual void Stop() = 0;

  /* Current tick count (monotonic). */
  virtual uint64_t TickCount() const = 0;

  /* Elapsed nanoseconds since Init(). */
  virtual std::chrono::nanoseconds Elapsed() const = 0;

  /* Factory: creates a TimerfdTickTimer (Linux-only platform). */
  static std::unique_ptr<TickTimer> Create(std::chrono::nanoseconds interval);
};

/* Portable fallback using std::this_thread::sleep_for/sleep_until.
 * Never selected by TickTimer::Create on Linux — retained so the
 * class hierarchy compiles on non-Linux platforms. */
class SleepTickTimer final : public TickTimer {
 public:
  bool Init(std::chrono::nanoseconds interval) override;
  bool WaitForNextTick() override;
  bool WaitUntil(std::chrono::steady_clock::time_point deadline) override;
  void Stop() override;
  uint64_t TickCount() const override { return tick_count_; }
  std::chrono::nanoseconds Elapsed() const override;

 private:
  std::chrono::nanoseconds  interval_{};
  uint64_t                  tick_count_{0};
  std::chrono::steady_clock::time_point start_;
  bool                      running_{false};
};

/* Sole backend on Linux: Linux timerfd with absolute-time scheduling.
 * Selected by TickTimer::Create for all intervals.  Drift-free. */
class TimerfdTickTimer final : public TickTimer {
 public:
  ~TimerfdTickTimer() override { Stop(); }

  bool Init(std::chrono::nanoseconds interval) override;
  bool WaitForNextTick() override;
  bool WaitUntil(std::chrono::steady_clock::time_point deadline) override;
  void Stop() override;
  uint64_t TickCount() const override { return tick_count_; }
  std::chrono::nanoseconds Elapsed() const override;

 private:
  int                       fd_{-1};
  std::chrono::nanoseconds  interval_{};
  uint64_t                  tick_count_{0};
  std::chrono::steady_clock::time_point start_;
};

}  // namespace boat::hil
