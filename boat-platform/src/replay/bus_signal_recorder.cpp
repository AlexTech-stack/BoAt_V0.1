#include "bus_signal_recorder.h"

#include <array>
#include <cstring>
#include <span>
#include <variant>

namespace boat::replay {

namespace {

// Extract a numeric value from a bus signal variant. Returns false for
// string/bytes signals, which are not recorded.
bool AsDouble(const boat::core::BusSignalValue& v, double& out) {
  if (const auto* d = std::get_if<double>(&v)) {
    out = *d;
  } else if (const auto* i = std::get_if<std::int64_t>(&v)) {
    out = static_cast<double>(*i);
  } else if (const auto* b = std::get_if<bool>(&v)) {
    out = *b ? 1.0 : 0.0;
  } else {
    return false;
  }
  return true;
}

std::vector<std::uint8_t> EncodeDouble(double value) {
  std::vector<std::uint8_t> blob(sizeof(double));
  std::memcpy(blob.data(), &value, sizeof(double));
  return blob;
}

}  // namespace

BusSignalRecorder::BusSignalRecorder(boat::core::SignalBus& bus,
                                     boat::store::IEventStore& store,
                                     Config config)
    : bus_(bus), store_(store), config_(std::move(config)) {}

BusSignalRecorder::~BusSignalRecorder() { Stop(); }

bool BusSignalRecorder::Matches(const std::string& name) const {
  if (config_.prefixes.empty()) return true;
  for (const auto& p : config_.prefixes) {
    if (name.rfind(p, 0) == 0) return true;  // name starts with p
  }
  return false;
}

void BusSignalRecorder::Start() {
  if (running_.exchange(true)) return;
  epoch_ = std::chrono::steady_clock::now();
  writer_thread_ = std::thread(&BusSignalRecorder::WriterLoop, this);
  sub_id_ = bus_.Subscribe({}, [this](const boat::core::BusSignal& s) {
    OnSignal(s);
  });
}

void BusSignalRecorder::Stop() {
  if (!running_.exchange(false)) return;
  bus_.Unsubscribe(sub_id_);
  queue_cv_.notify_all();
  if (writer_thread_.joinable()) writer_thread_.join();
}

void BusSignalRecorder::OnSignal(const boat::core::BusSignal& signal) {
  double value = 0.0;
  if (!AsDouble(signal.value, value)) return;
  if (!Matches(signal.name)) return;

  const auto now = std::chrono::steady_clock::now();
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
                           now - epoch_)
                           .count();
  const auto tick_ns = config_.tick_duration.count() > 0
                           ? config_.tick_duration.count()
                           : 1;

  boat::store::EventRecord record;
  record.simulation_id = config_.simulation_id;
  record.tick = static_cast<std::uint64_t>(elapsed / tick_ns);
  record.wall_time_ns = static_cast<std::int64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count());
  record.signal_id = signal.name;
  record.value_type = 1;  // numeric
  record.value_blob = EncodeDouble(value);
  record.tags = "{}";

  {
    std::lock_guard<std::mutex> lock(queue_mutex_);
    record.id = config_.simulation_id + "_" + std::to_string(seq_++);
    queue_.push_back(std::move(record));
  }
  queue_cv_.notify_one();
}

void BusSignalRecorder::WriterLoop() {
  while (true) {
    std::deque<boat::store::EventRecord> batch;
    {
      std::unique_lock<std::mutex> lock(queue_mutex_);
      queue_cv_.wait(lock, [this] {
        return !running_.load() || !queue_.empty();
      });
      if (!running_.load() && queue_.empty()) break;
      batch.swap(queue_);
    }
    if (batch.empty()) continue;
    std::vector<boat::store::EventRecord> flat(
        std::make_move_iterator(batch.begin()),
        std::make_move_iterator(batch.end()));
    store_.InsertBatch(std::span<const boat::store::EventRecord>(flat));
    recorded_.fetch_add(flat.size());
  }
}

}  // namespace boat::replay
