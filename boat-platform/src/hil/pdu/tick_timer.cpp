#include "pdu/tick_timer.h"

#include <cerrno>
#include <thread>

#ifdef __linux__
#include <sys/timerfd.h>
#include <unistd.h>
#endif

namespace boat::hil {

// ── Factory ───────────────────────────────────────────────────────────────────

std::unique_ptr<TickTimer> TickTimer::Create(std::chrono::nanoseconds interval) {
  auto t = std::make_unique<TimerfdTickTimer>();
  t->Init(interval);
  return t;
}

// ── SleepTickTimer ────────────────────────────────────────────────────────────

bool SleepTickTimer::Init(std::chrono::nanoseconds interval) {
  interval_ = interval;
  start_    = std::chrono::steady_clock::now();
  running_  = true;
  return true;
}

bool SleepTickTimer::WaitForNextTick() {
  if (!running_) return false;
  std::this_thread::sleep_for(interval_);
  tick_count_++;
  return running_;
}

bool SleepTickTimer::WaitUntil(std::chrono::steady_clock::time_point deadline) {
  if (!running_) return false;
  std::this_thread::sleep_until(deadline);
  tick_count_++;
  return running_;
}

void SleepTickTimer::Stop() {
  running_ = false;
}

std::chrono::nanoseconds SleepTickTimer::Elapsed() const {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - start_);
}

// ── TimerfdTickTimer ──────────────────────────────────────────────────────────

#ifdef __linux__

namespace {

timespec ToTimespec(std::chrono::nanoseconds ns) {
  auto secs = std::chrono::duration_cast<std::chrono::seconds>(ns);
  ns -= secs;
  return timespec{
      .tv_sec  = static_cast<time_t>(secs.count()),
      .tv_nsec = static_cast<long>(ns.count()),
  };
}

}  // anonymous namespace

bool TimerfdTickTimer::Init(std::chrono::nanoseconds interval) {
  if (fd_ >= 0) ::close(fd_);

  fd_ = timerfd_create(CLOCK_MONOTONIC, 0);  // blocking read
  if (fd_ < 0) return false;

  interval_ = interval;
  start_ = std::chrono::steady_clock::now();

  itimerspec spec{};
  spec.it_interval = ToTimespec(interval);   // repeating interval
  spec.it_value    = ToTimespec(interval);    // first expiry after interval

  struct itimerspec old{};
  return timerfd_settime(fd_, 0, &spec, &old) == 0;
}

bool TimerfdTickTimer::WaitForNextTick() {
  if (fd_ < 0) return false;

  uint64_t expirations = 0;
  ssize_t n;
  do {
    n = ::read(fd_, &expirations, sizeof(expirations));
  } while (n < 0 && errno == EINTR);

  // Blocking read on the timerfd waits until the timer fires.
  // Returns the number of expirations (>=1).  Repeat count is
  // consumed — next read will wait for the following interval.
  if (n <= 0) return false;

  tick_count_ += expirations;
  return true;
}

bool TimerfdTickTimer::WaitUntil(std::chrono::steady_clock::time_point deadline) {
  if (fd_ < 0) return false;

  auto deadline_ns = deadline.time_since_epoch();
  itimerspec spec{};
  spec.it_value = ToTimespec(deadline_ns);

  // One-shot set at the absolute deadline.  If deadline is in the past
  // the timer fires immediately — no drift, no lost time.
  if (timerfd_settime(fd_, TFD_TIMER_ABSTIME, &spec, nullptr) < 0) return false;

  uint64_t expirations = 0;
  ssize_t n;
  do {
    n = ::read(fd_, &expirations, sizeof(expirations));
  } while (n < 0 && errno == EINTR);

  if (n <= 0) return false;
  tick_count_ += expirations;
  return true;
}

void TimerfdTickTimer::Stop() {
  if (fd_ >= 0) {
    ::close(fd_);
    fd_ = -1;
  }
}

std::chrono::nanoseconds TimerfdTickTimer::Elapsed() const {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - start_);
}

#else   // not __linux__

bool TimerfdTickTimer::Init(std::chrono::nanoseconds) { return false; }
bool TimerfdTickTimer::WaitForNextTick() { return false; }
void TimerfdTickTimer::Stop() {}
std::chrono::nanoseconds TimerfdTickTimer::Elapsed() const { return {}; }

#endif  // __linux__

}  // namespace boat::hil
