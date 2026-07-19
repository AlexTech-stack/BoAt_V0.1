#include "replay_engine/replay_engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <vector>

#include <arpa/inet.h>

#include "boat/v1/frame.pb.h"
#include "core/frame.h"

namespace boat::replay {
namespace {

std::uint64_t FrameTimestampToMs(std::uint64_t timestamp_ns) {
  return timestamp_ns / 1'000'000ULL;
}

// Decode a numeric event value_blob (a raw little-endian double, as written by
// the metrics/signal path) back to a double. Matches DecodeNumeric in
// metrics_service_impl.cpp — the codebase's convention for numeric event blobs.
double DecodeNumericBlob(const std::vector<std::uint8_t>& blob) {
  if (blob.size() >= sizeof(double)) {
    double value = 0.0;
    std::memcpy(&value, blob.data(), sizeof(double));
    return value;
  }
  return 0.0;
}

// Resolve a 1-based trace channel to a target CAN interface using the
// replay's --buses mapping (ch1->buses[0], ch2->buses[1], ...); falls back
// to the last bus if there are more channels than buses, and to "vcan0" if
// no buses were configured at all -- this is what makes an imported trace
// replayable on different hardware without re-importing it.
std::string ResolveCanIface(const ReplayConfig& config, std::uint32_t channel) {
  if (config.buses.empty()) {
    return "vcan0";
  }
  const std::size_t idx = channel == 0 ? 0 : static_cast<std::size_t>(channel - 1);
  return config.buses[std::min(idx, config.buses.size() - 1)];
}

// Render a raw 4- or 16-byte IP address back to its canonical string form
// (matching Python's `str(ipaddress.ip_address(...))`, RFC 5952) so it can
// be looked up in a --mac-map keyed by that string.
std::string IpBytesToString(const std::string& ip_bytes, std::uint32_t ip_version) {
  if (ip_bytes.empty()) {
    return {};
  }
  char buf[INET6_ADDRSTRLEN]{};
  const int family = (ip_version == 6) ? AF_INET6 : AF_INET;
  if (inet_ntop(family, ip_bytes.data(), buf, sizeof(buf)) == nullptr) {
    return {};
  }
  return std::string(buf);
}

bool ParseMacInto(const std::string& mac_str, std::uint8_t out[6]) {
  unsigned int bytes[6];
  if (std::sscanf(mac_str.c_str(), "%x:%x:%x:%x:%x:%x", &bytes[0], &bytes[1],
                   &bytes[2], &bytes[3], &bytes[4], &bytes[5]) != 6) {
    return false;
  }
  for (int i = 0; i < 6; ++i) {
    out[i] = static_cast<std::uint8_t>(bytes[i]);
  }
  return true;
}

boat::core::Frame ProtoToCoreFrame(const boat::v1::Frame& pf, const ReplayConfig& config) {
  std::vector<std::uint8_t> payload(pf.payload().begin(), pf.payload().end());
  std::uint64_t ts = pf.timestamp_ns();

  boat::core::Frame f;
  switch (pf.bus_type()) {
    case boat::v1::Frame::CAN:
    case boat::v1::Frame::CANFD: {
      const auto& cm = pf.can();
      std::string iface = ResolveCanIface(config, cm.channel());
      f = boat::core::Frame::FromCan(
          std::move(iface), cm.can_id(), static_cast<std::uint8_t>(cm.dlc()),
          static_cast<std::uint8_t>(cm.flags()), std::move(payload),
          pf.bus_type() == boat::v1::Frame::CANFD);
      break;
    }
    case boat::v1::Frame::ETHERNET: {
      const auto& em = pf.eth();
      uint8_t dm[6]{}, sm[6]{};
      std::memcpy(dm, em.dst_mac().data(), std::min(em.dst_mac().size(), 6UL));
      std::memcpy(sm, em.src_mac().data(), std::min(em.src_mac().size(), 6UL));

      if (!config.mac_map.empty()) {
        const std::string dst_ip_str = IpBytesToString(em.dst_ip(), em.ip_version());
        const std::string src_ip_str = IpBytesToString(em.src_ip(), em.ip_version());
        if (auto it = config.mac_map.find(dst_ip_str); it != config.mac_map.end()) {
          ParseMacInto(it->second, dm);
        }
        if (auto it = config.mac_map.find(src_ip_str); it != config.mac_map.end()) {
          ParseMacInto(it->second, sm);
        }
      }

      const uint8_t* sip = em.src_ip().empty()
                               ? nullptr
                               : reinterpret_cast<const uint8_t*>(em.src_ip().data());
      const uint8_t* dip = em.dst_ip().empty()
                               ? nullptr
                               : reinterpret_cast<const uint8_t*>(em.dst_ip().data());
      std::string iface = config.eth_iface.empty() ? pf.iface() : config.eth_iface;
      f = boat::core::Frame::FromEthernet(
          std::move(iface), dm, sm, static_cast<std::uint16_t>(em.ethertype()),
          static_cast<std::uint16_t>(em.vlan_id()), sip,
          static_cast<std::uint8_t>(em.ip_version()), dip,
          std::move(payload));
      break;
    }
    case boat::v1::Frame::PDU: {
      const auto& pm = pf.pdu();
      f = boat::core::Frame::FromPdu(pf.iface(), pm.pdu_id(), std::move(payload));
      break;
    }
    default:
      break;
  }
  f.set_timestamp_ns(ts);
  return f;
}

}  // namespace

ReplayController::ReplayController(boat::store::ITraceStore& trace_store,
                                   boat::store::IEventStore& event_store,
                                   boat::core::EventBus& event_bus)
    : trace_store_(trace_store), event_store_(event_store), event_bus_(event_bus) {}

ReplayController::~ReplayController() { Stop(); }

void ReplayController::Start(const ReplayConfig& config) {
  Stop();

  active_config_ = config;
  mapped_trace_ = trace_store_.ReadTraceMmap(config.trace_id);
  current_tick_.store(config.start_tick);
  requested_seek_tick_.store(config.start_tick);
  seek_pending_.store(true);
  paused_.store(false);
  running_.store(true);
  {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_.clear();
  }

  ParseTickDurationFromEnv();
  tick_timer_ = boat::hil::TickTimer::Create(tick_duration_);
  replay_base_time_ = std::chrono::steady_clock::now();
  replay_base_tick_ = config.start_tick;

  replay_thread_ = std::thread(&ReplayController::ReplayLoop, this);
}

void ReplayController::StartFromEvents(const boat::store::EventFilter& filter,
                                        const ReplayConfig& replay_cfg) {
  // Event-store replay is signal-domain, not frame-domain: each recorded event
  // is replayed as its original named signal on the always-on signal bus (via
  // signal_forwarder_), preserving name, value and tick ordering. This replaces
  // the old approach of synthesizing throwaway CAN frames — a recorded voltage
  // curve now replays as psu.<id>.voltage.meas, not as fake wire traffic.
  auto events = event_store_.Query(filter);
  if (events.empty()) {
    return;
  }
  // Query is not guaranteed tick-ordered across all backends; sort defensively
  // so replay timing and ordering are deterministic.
  std::sort(events.begin(), events.end(),
            [](const boat::store::EventRecord& a, const boat::store::EventRecord& b) {
              return a.tick < b.tick;
            });

  Stop();  // tear down any in-flight replay before starting a new one

  active_config_ = replay_cfg;
  active_config_.trace_id.clear();  // no trace file backs a signal replay
  active_config_.start_tick = events.front().tick;
  {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_.clear();
  }
  ParseTickDurationFromEnv();
  tick_timer_ = boat::hil::TickTimer::Create(tick_duration_);
  paused_.store(false);
  running_.store(true);

  replay_thread_ = std::thread(&ReplayController::ReplaySignalLoop, this,
                               std::move(events));
}

void ReplayController::ReplaySignalLoop(
    std::vector<boat::store::EventRecord> events) {
  try {
    replay_base_time_ = std::chrono::steady_clock::now();
    replay_base_tick_ = events.front().tick;
    current_tick_.store(events.front().tick);

    double speed_multiplier = active_config_.speed_multiplier;
    if (speed_multiplier <= 0.0) speed_multiplier = 1.0;

    for (const auto& event : events) {
      // Honour pause / stop.
      {
        std::unique_lock<std::mutex> lock(pause_mutex_);
        pause_cv_.wait(lock, [this] {
          return !running_.load() || !paused_.load();
        });
      }
      if (!running_.load()) break;

      // Absolute-time scheduling, mirroring ReplayLoop's frame path.
      if (active_config_.speed == ReplaySpeed::REAL_TIME ||
          active_config_.speed == ReplaySpeed::ACCELERATED) {
        const std::uint64_t tick_delta =
            (event.tick > replay_base_tick_) ? (event.tick - replay_base_tick_) : 0;
        const auto tick_offset_ns =
            static_cast<double>(tick_delta * tick_duration_.count());
        const auto deadline_offset = std::chrono::nanoseconds(
            static_cast<std::uint64_t>(tick_offset_ns / speed_multiplier));
        tick_timer_->WaitUntil(replay_base_time_ + deadline_offset);
      }

      // Replay the event as a named signal onto the signal bus.
      const double value = DecodeNumericBlob(event.value_blob);
      {
        std::lock_guard<std::mutex> lock(forwarder_mutex_);
        if (signal_forwarder_) {
          signal_forwarder_(event.signal_id, value);
        }
      }

      // gRPC streaming parity (StreamReplay / event-bus consumers).
      std::string blob(event.value_blob.begin(), event.value_blob.end());
      boat::core::BusEvent replay_event;
      replay_event.type = kReplayBusEventType;
      replay_event.tick = event.tick;
      replay_event.payload = blob;
      event_bus_.Publish(std::move(replay_event));
      {
        std::lock_guard<std::mutex> lock(event_queue_mutex_);
        event_queue_.push_back({event.tick, std::move(blob)});
      }

      current_tick_.store(event.tick);

      if (active_config_.speed == ReplaySpeed::STEP_BY_STEP) {
        paused_.store(true);
        std::unique_lock<std::mutex> lock(pause_mutex_);
        pause_cv_.wait(lock, [this] {
          return !running_.load() || !paused_.load();
        });
        if (!running_.load()) break;
      }
    }
  } catch (const std::exception& ex) {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = ex.what();
  } catch (...) {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = "unknown signal-replay error";
  }
  paused_.store(false);
  running_.store(false);
  pause_cv_.notify_all();
}

void ReplayController::Seek(std::uint64_t tick) {
  requested_seek_tick_.store(tick);
  seek_pending_.store(true);
  pause_cv_.notify_all();
}

void ReplayController::Pause() { paused_.store(true); }

void ReplayController::Resume() {
  paused_.store(false);
  pause_cv_.notify_all();
}

void ReplayController::Stop() {
  const bool was_running = running_.exchange(false);
  pause_cv_.notify_all();
  if (replay_thread_.joinable()) {
    replay_thread_.join();
  }
  if (tick_timer_) {
    tick_timer_->Stop();
  }
  if (was_running || !active_config_.trace_id.empty()) {
    trace_store_.UnmapTrace(active_config_.trace_id);
  }
}

bool ReplayController::HasError() const {
  std::lock_guard<std::mutex> lock(error_mutex_);
  return !last_error_.empty();
}

std::string ReplayController::LastError() const {
  std::lock_guard<std::mutex> lock(error_mutex_);
  return last_error_;
}

void ReplayController::SetEventForwarder(EventForwarder forwarder) {
  std::lock_guard<std::mutex> lock(forwarder_mutex_);
  event_forwarder_ = std::move(forwarder);
}

void ReplayController::SetSignalForwarder(SignalForwarder forwarder) {
  std::lock_guard<std::mutex> lock(forwarder_mutex_);
  signal_forwarder_ = std::move(forwarder);
}

const ReplayConfig& ReplayController::GetActiveConfig() const {
  return active_config_;
}

void ReplayController::ParseTickDurationFromEnv() {
  const char* us_env = std::getenv("BOAT_NODE_TICK_US");
  if (us_env != nullptr) {
    char* end = nullptr;
    auto val = std::strtoul(us_env, &end, 10);
    if (end != us_env && val > 0) {
      tick_duration_ = std::chrono::microseconds(val);
      return;
    }
  }
  const char* ms_env = std::getenv("BOAT_NODE_TICK_MS");
  if (ms_env != nullptr) {
    char* end = nullptr;
    auto val = std::strtoul(ms_env, &end, 10);
    if (end != ms_env && val > 0) {
      tick_duration_ = std::chrono::milliseconds(val);
      return;
    }
  }
  tick_duration_ = std::chrono::milliseconds(1);
}

bool ReplayController::SeekToTick(std::uint64_t target_tick, std::size_t& offset,
                                   std::uint64_t& landed_tick) const {
  offset = 0;
  while (offset + sizeof(std::uint32_t) <= mapped_trace_.size()) {
    std::uint32_t record_len;
    std::memcpy(&record_len, mapped_trace_.data() + offset, sizeof(record_len));
    offset += sizeof(record_len);
    if (record_len == 0 || offset + record_len > mapped_trace_.size()) {
      throw std::runtime_error("invalid trace record length");
    }
    boat::v1::Frame pf;
    if (!pf.ParseFromArray(mapped_trace_.data() + offset, record_len)) {
      throw std::runtime_error("invalid protobuf frame record");
    }
    offset += record_len;
    const std::uint64_t record_tick = FrameTimestampToMs(pf.timestamp_ns());
    if (record_tick >= target_tick) {
      offset -= (sizeof(record_len) + record_len);
      landed_tick = record_tick;
      return true;
    }
  }
  offset = mapped_trace_.size();
  return false;
}

void ReplayController::ReplayLoop() {
  try {
    if (mapped_trace_.empty()) {
      running_.store(false);
      return;
    }

    const bool looping = active_config_.loop_delay_ms > 0;

    do {
      std::size_t offset = 0;
      std::chrono::steady_clock::time_point last_record_time;
      bool has_records = false;

      while (running_.load() && offset + sizeof(std::uint32_t) <= mapped_trace_.size()) {
        {
          std::unique_lock<std::mutex> lock(pause_mutex_);
          pause_cv_.wait(lock, [this] {
            return !running_.load() || !paused_.load() || seek_pending_.load();
          });
          if (!running_.load()) {
            break;
          }
        }

        if (seek_pending_.exchange(false)) {
          const auto target_tick = requested_seek_tick_.load();
          // Anchor to the actual tick of the record we land on, not the
          // raw requested target -- trace timestamps are absolute (epoch
          // milliseconds), so a target of 0 would otherwise leave the
          // schedule anchored decades before the first real record.
          std::uint64_t landed_tick = target_tick;
          SeekToTick(target_tick, offset, landed_tick);
          current_tick_.store(landed_tick);
          replay_base_time_ = std::chrono::steady_clock::now();
          replay_base_tick_ = landed_tick;
          continue;
        }

        // ── Read length-delimited protobuf record ───────────────────────
        std::uint32_t record_len;
        std::memcpy(&record_len, mapped_trace_.data() + offset, sizeof(record_len));
        offset += sizeof(record_len);
        if (record_len == 0 || offset + record_len > mapped_trace_.size()) {
          throw std::runtime_error("invalid trace record length");
        }

        boat::v1::Frame pf;
        if (!pf.ParseFromArray(mapped_trace_.data() + offset, record_len)) {
          throw std::runtime_error("invalid protobuf frame record");
        }
        offset += record_len;

        std::uint64_t tick = FrameTimestampToMs(pf.timestamp_ns());

        // ── Absolute-time scheduling ────────────────────────────────────
        double speed_multiplier = active_config_.speed_multiplier;
        if (speed_multiplier <= 0.0) {
          speed_multiplier = 1.0;
        }
        if (active_config_.speed == ReplaySpeed::REAL_TIME ||
            active_config_.speed == ReplaySpeed::ACCELERATED) {
          // Traces are expected to be timestamp-ordered, but a record
          // earlier than the replay's base tick (e.g. from a hand-edited
          // trace) must not be allowed to underflow this unsigned delta --
          // that previously produced a deadline hundreds of millions of
          // years out, which stalls this frame and every RPC that touches
          // the replay controller afterward. Clamp to "play immediately"
          // instead.
          const std::uint64_t tick_delta =
              (tick > replay_base_tick_) ? (tick - replay_base_tick_) : 0;
          const auto tick_offset_ns = static_cast<double>(
              tick_delta * tick_duration_.count());
          const auto deadline_offset = std::chrono::nanoseconds(
              static_cast<std::uint64_t>(tick_offset_ns / speed_multiplier));
          tick_timer_->WaitUntil(replay_base_time_ + deadline_offset);
        }

        last_record_time = std::chrono::steady_clock::now();
        has_records = true;

        // ── Dispatch via core::Frame ────────────────────────────────────
        auto core_frame = ProtoToCoreFrame(pf, active_config_);

        {
          std::lock_guard<std::mutex> lock(forwarder_mutex_);
          if (event_forwarder_) {
            event_forwarder_(core_frame);
          }
        }

        // ── Publish replay event for gRPC streaming ─────────────────────
        std::string proto_bytes(reinterpret_cast<const char*>(mapped_trace_.data() + offset - record_len), record_len);

        boat::core::BusEvent replay_event;
        replay_event.type = kReplayBusEventType;
        replay_event.tick = tick;
        replay_event.payload = proto_bytes;
        event_bus_.Publish(std::move(replay_event));

        // ── Push to internal queue (StreamReplay) ───────────────────────
        {
          std::lock_guard<std::mutex> lock(event_queue_mutex_);
          event_queue_.push_back({tick, proto_bytes});
        }

        // ── Store in event store ────────────────────────────────────────
        {
          std::vector<std::uint8_t> payload_copy(proto_bytes.begin(), proto_bytes.end());
          boat::store::EventRecord record;
          record.id = std::to_string(tick) + "_" + std::to_string(pf.can().can_id());
          record.simulation_id = active_config_.trace_id;
          record.tick = tick;
          record.wall_time_ns = static_cast<std::int64_t>(pf.timestamp_ns());
          record.signal_id = std::to_string(pf.can().can_id());
          record.value_type = 0;
          record.value_blob = std::move(payload_copy);
          record.tags = "{}";
          std::array<boat::store::EventRecord, 1> batch{record};
          event_store_.InsertBatch(std::span<const boat::store::EventRecord>(batch));
        }

        current_tick_.store(tick);

        if (active_config_.speed == ReplaySpeed::STEP_BY_STEP) {
          paused_.store(true);
          std::unique_lock<std::mutex> lock(pause_mutex_);
          pause_cv_.wait(lock, [this] {
            return !running_.load() || !paused_.load() || seek_pending_.load();
          });
          if (!running_.load()) {
            break;
          }
        }
      }

      if (!running_.load()) {
        break;
      }

      if (looping && has_records) {
        auto target = last_record_time + std::chrono::milliseconds(active_config_.loop_delay_ms);
        auto now = std::chrono::steady_clock::now();
        if (target > now) {
          std::this_thread::sleep_for(target - now);
        }
        replay_base_time_ = target;
        // Same absolute-tick anchoring as the seek path above -- re-derive
        // the actual tick of the record the next pass will start on rather
        // than reusing the raw configured start_tick.
        std::size_t restart_offset = 0;
        std::uint64_t landed_tick = active_config_.start_tick;
        SeekToTick(active_config_.start_tick, restart_offset, landed_tick);
        replay_base_tick_ = landed_tick;
      }
    } while (running_.load() && looping);
  } catch (const std::exception& ex) {
    {
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = ex.what();
    }
    paused_.store(false);
    running_.store(false);
    pause_cv_.notify_all();
    return;
  } catch (...) {
    {
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = "unknown replay error";
    }
    paused_.store(false);
    running_.store(false);
    pause_cv_.notify_all();
    return;
  }

  paused_.store(false);
  running_.store(false);
  pause_cv_.notify_all();
}

void ReplayController::PushEvent(std::uint64_t tick, std::string payload) {
  std::lock_guard<std::mutex> lock(event_queue_mutex_);
  event_queue_.push_back({tick, std::move(payload)});
}

std::vector<ReplayController::ReplayEventEntry> ReplayController::ConsumeEvents() {
  std::lock_guard<std::mutex> lock(event_queue_mutex_);
  std::vector<ReplayEventEntry> result;
  result.reserve(event_queue_.size());
  while (!event_queue_.empty()) {
    result.push_back(std::move(event_queue_.front()));
    event_queue_.pop_front();
  }
  return result;
}

}  // namespace boat::replay