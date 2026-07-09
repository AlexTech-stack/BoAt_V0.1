#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <unordered_map>
#include <vector>

struct sqlite3;

namespace boat::store {

struct __attribute__((packed)) TraceRecordHeader {
  std::uint32_t magic;
  std::uint32_t event_type;
  std::uint64_t tick;
  std::int64_t wall_time_ns;
  std::uint32_t payload_size;
};

struct TraceRecord {
  enum class Format : int {
    BINARY = 0,
    MF4 = 1,
    CSV = 2,
  };

  std::string id;
  std::string simulation_id;
  std::uint64_t start_tick;
  std::uint64_t end_tick;
  Format format;
  std::string storage_path;
  std::uint64_t size_bytes;
};

class ITraceStore {
 public:
  virtual ~ITraceStore() = default;
  virtual void WriteTrace(const TraceRecord& meta, std::span<const std::uint8_t> data) = 0;
  virtual std::span<const std::uint8_t> ReadTraceMmap(const std::string& trace_id) = 0;
  virtual std::vector<TraceRecord> ListTraces(const std::string& simulation_id) = 0;
  virtual std::vector<TraceRecord> ListAllTraces() = 0;
  virtual void UnmapTrace(const std::string& trace_id) = 0;
};

class FlatFileTraceStore : public ITraceStore {
 public:
  explicit FlatFileTraceStore(const std::string& index_db_path);
  ~FlatFileTraceStore() override;

  void WriteTrace(const TraceRecord& meta, std::span<const std::uint8_t> data) override;
  void AppendTraceRecord(const TraceRecord& meta, std::span<const std::uint8_t> record_data);
  std::span<const std::uint8_t> ReadTraceMmap(const std::string& trace_id) override;
  std::vector<TraceRecord> ListTraces(const std::string& simulation_id) override;
  std::vector<TraceRecord> ListAllTraces() override;
  void UnmapTrace(const std::string& trace_id) override;

 private:
  struct MmapRegion {
    void* addr{nullptr};
    std::size_t length{0};
    int fd{-1};
  };

  sqlite3* db_{nullptr};
  std::unordered_map<std::string, MmapRegion> mmap_regions_;
};

}  // namespace boat::store
