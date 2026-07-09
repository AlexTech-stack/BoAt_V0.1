#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

struct sqlite3;

namespace boat::store {

struct EventRecord {
  std::string id;
  std::string simulation_id;
  std::uint64_t tick;
  std::int64_t wall_time_ns;
  std::string signal_id;
  int value_type;
  std::vector<std::uint8_t> value_blob;
  std::string tags;
};

struct EventFilter {
  std::optional<std::string> simulation_id;
  std::optional<std::string> signal_id;
  std::optional<std::uint64_t> tick_min;
  std::optional<std::uint64_t> tick_max;
};

class IEventStore {
 public:
  virtual ~IEventStore() = default;
  virtual void InsertBatch(std::span<const EventRecord> events) = 0;
  virtual std::vector<EventRecord> Query(const EventFilter& filter) = 0;
};

class SqliteEventStore : public IEventStore {
 public:
  explicit SqliteEventStore(std::string_view db_path);
  ~SqliteEventStore() override;

  void InsertBatch(std::span<const EventRecord> events) override;
  std::vector<EventRecord> Query(const EventFilter& filter) override;

 private:
  sqlite3* db_{nullptr};
};

}  // namespace boat::store
