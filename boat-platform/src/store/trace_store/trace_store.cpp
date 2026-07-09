#include "trace_store/trace_store.h"

#include <fcntl.h>
#include <sqlite3.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstring>
#include <stdexcept>
#include <string>

namespace boat::store {
namespace {

void ExecOrThrow(sqlite3* db, const char* sql) {
  char* err = nullptr;
  if (sqlite3_exec(db, sql, nullptr, nullptr, &err) != SQLITE_OK) {
    const std::string message = err != nullptr ? err : "sqlite error";
    sqlite3_free(err);
    throw std::runtime_error(message);
  }
}

}  // namespace

FlatFileTraceStore::FlatFileTraceStore(const std::string& index_db_path) {
  constexpr int kFlags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX;
  if (sqlite3_open_v2(index_db_path.c_str(), &db_, kFlags, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to open sqlite trace index");
  }

  ExecOrThrow(db_, "PRAGMA journal_mode=WAL;");
  ExecOrThrow(
      db_,
      "CREATE TABLE IF NOT EXISTS traces ("
      "id TEXT PRIMARY KEY,"
      "simulation_id TEXT NOT NULL,"
      "start_tick INTEGER NOT NULL,"
      "end_tick INTEGER NOT NULL,"
      "format INTEGER NOT NULL,"
      "storage_path TEXT NOT NULL,"
      "size_bytes INTEGER NOT NULL"
      ");");
}

FlatFileTraceStore::~FlatFileTraceStore() {
  for (auto it = mmap_regions_.begin(); it != mmap_regions_.end();) {
    UnmapTrace(it->first);
    it = mmap_regions_.begin();
  }
  if (db_ != nullptr) {
    sqlite3_close_v2(db_);
    db_ = nullptr;
  }
}

void FlatFileTraceStore::WriteTrace(const TraceRecord& meta, std::span<const std::uint8_t> data) {
  // Remove any existing file first so open() works even if the old file
  // was created by a different process/user with incompatible permissions.
  unlink(meta.storage_path.c_str());
  const int fd = open(meta.storage_path.c_str(), O_CREAT | O_TRUNC | O_WRONLY, 0644);
  if (fd < 0) {
    throw std::runtime_error("failed to open trace output file");
  }

  std::size_t written = 0;
  while (written < data.size()) {
    const ssize_t chunk = write(fd, data.data() + written, data.size() - written);
    if (chunk <= 0) {
      close(fd);
      throw std::runtime_error("failed to write trace payload");
    }
    written += static_cast<std::size_t>(chunk);
  }
  close(fd);

  constexpr const char* kInsertSql =
      "INSERT OR REPLACE INTO traces "
      "(id, simulation_id, start_tick, end_tick, format, storage_path, size_bytes) "
      "VALUES (?, ?, ?, ?, ?, ?, ?);";
  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kInsertSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare trace index insert");
  }
  sqlite3_bind_text(stmt, 1, meta.id.c_str(), -1, SQLITE_TRANSIENT);
  sqlite3_bind_text(stmt, 2, meta.simulation_id.c_str(), -1, SQLITE_TRANSIENT);
  sqlite3_bind_int64(stmt, 3, static_cast<sqlite3_int64>(meta.start_tick));
  sqlite3_bind_int64(stmt, 4, static_cast<sqlite3_int64>(meta.end_tick));
  sqlite3_bind_int(stmt, 5, static_cast<int>(meta.format));
  sqlite3_bind_text(stmt, 6, meta.storage_path.c_str(), -1, SQLITE_TRANSIENT);
  sqlite3_bind_int64(stmt, 7, static_cast<sqlite3_int64>(meta.size_bytes));
  if (sqlite3_step(stmt) != SQLITE_DONE) {
    sqlite3_finalize(stmt);
    throw std::runtime_error("failed to persist trace metadata");
  }
  sqlite3_finalize(stmt);
}

void FlatFileTraceStore::AppendTraceRecord(const TraceRecord& meta,
                                            std::span<const std::uint8_t> record_data) {
  const int fd = open(meta.storage_path.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
  if (fd < 0) {
    throw std::runtime_error("failed to open trace file for append");
  }

  std::size_t written = 0;
  while (written < record_data.size()) {
    const ssize_t chunk = write(fd, record_data.data() + written, record_data.size() - written);
    if (chunk <= 0) {
      close(fd);
      throw std::runtime_error("failed to append trace record");
    }
    written += static_cast<std::size_t>(chunk);
  }
  close(fd);

  struct stat file_stat{};
  if (stat(meta.storage_path.c_str(), &file_stat) != 0) {
    throw std::runtime_error("failed to stat appended trace file");
  }

  constexpr const char* kUpsertSql =
      "INSERT OR REPLACE INTO traces "
      "(id, simulation_id, start_tick, end_tick, format, storage_path, size_bytes) "
      "VALUES (?, ?, ?, ?, ?, ?, ?);";
  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kUpsertSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare trace index upsert");
  }
  sqlite3_bind_text(stmt, 1, meta.id.c_str(), -1, SQLITE_TRANSIENT);
  sqlite3_bind_text(stmt, 2, meta.simulation_id.c_str(), -1, SQLITE_TRANSIENT);
  sqlite3_bind_int64(stmt, 3, static_cast<sqlite3_int64>(meta.start_tick));
  sqlite3_bind_int64(stmt, 4, static_cast<sqlite3_int64>(meta.end_tick));
  sqlite3_bind_int(stmt, 5, static_cast<int>(meta.format));
  sqlite3_bind_text(stmt, 6, meta.storage_path.c_str(), -1, SQLITE_TRANSIENT);
  sqlite3_bind_int64(stmt, 7, static_cast<sqlite3_int64>(file_stat.st_size));
  if (sqlite3_step(stmt) != SQLITE_DONE) {
    sqlite3_finalize(stmt);
    throw std::runtime_error("failed to persist trace index after append");
  }
  sqlite3_finalize(stmt);
}

std::span<const std::uint8_t> FlatFileTraceStore::ReadTraceMmap(const std::string& trace_id) {
  constexpr const char* kSelectSql = "SELECT storage_path FROM traces WHERE id = ?;";
  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kSelectSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare trace path lookup");
  }

  sqlite3_bind_text(stmt, 1, trace_id.c_str(), -1, SQLITE_TRANSIENT);
  const int step = sqlite3_step(stmt);
  if (step != SQLITE_ROW) {
    sqlite3_finalize(stmt);
    throw std::runtime_error("trace id not found");
  }

  const char* path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
  const std::string storage_path = path != nullptr ? path : "";
  sqlite3_finalize(stmt);

  const int fd = open(storage_path.c_str(), O_RDONLY);
  if (fd < 0) {
    throw std::runtime_error("failed to open trace for mmap");
  }

  struct stat file_stat {};
  if (fstat(fd, &file_stat) != 0) {
    close(fd);
    throw std::runtime_error("failed to stat trace file");
  }
  const std::size_t length = static_cast<std::size_t>(file_stat.st_size);
  void* addr = mmap(nullptr, length, PROT_READ, MAP_SHARED | MAP_POPULATE, fd, 0);
  if (addr == MAP_FAILED) {
    close(fd);
    throw std::runtime_error("mmap failed");
  }

  mmap_regions_[trace_id] = MmapRegion{addr, length, fd};
  return {static_cast<const std::uint8_t*>(addr), length};
}

void FlatFileTraceStore::UnmapTrace(const std::string& trace_id) {
  const auto it = mmap_regions_.find(trace_id);
  if (it == mmap_regions_.end()) {
    return;
  }

  munmap(it->second.addr, it->second.length);
  close(it->second.fd);
  mmap_regions_.erase(it);
}

std::vector<TraceRecord> FlatFileTraceStore::ListTraces(const std::string& simulation_id) {
  constexpr const char* kSelectSql =
      "SELECT id, simulation_id, start_tick, end_tick, format, storage_path, size_bytes "
      "FROM traces WHERE simulation_id = ?;";
  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kSelectSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare trace list query");
  }
  sqlite3_bind_text(stmt, 1, simulation_id.c_str(), -1, SQLITE_TRANSIENT);

  std::vector<TraceRecord> traces;
  while (sqlite3_step(stmt) == SQLITE_ROW) {
    TraceRecord record;
    record.id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
    record.simulation_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
    record.start_tick = static_cast<std::uint64_t>(sqlite3_column_int64(stmt, 2));
    record.end_tick = static_cast<std::uint64_t>(sqlite3_column_int64(stmt, 3));
    record.format = static_cast<TraceRecord::Format>(sqlite3_column_int(stmt, 4));
    record.storage_path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5));
    record.size_bytes = static_cast<std::uint64_t>(sqlite3_column_int64(stmt, 6));
    traces.push_back(std::move(record));
  }

  sqlite3_finalize(stmt);
  return traces;
}

std::vector<TraceRecord> FlatFileTraceStore::ListAllTraces() {
  constexpr const char* kSelectSql =
      "SELECT id, simulation_id, start_tick, end_tick, format, storage_path, size_bytes "
      "FROM traces;";
  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kSelectSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare trace list-all query");
  }

  std::vector<TraceRecord> traces;
  while (sqlite3_step(stmt) == SQLITE_ROW) {
    TraceRecord record;
    record.id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
    record.simulation_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
    record.start_tick = static_cast<std::uint64_t>(sqlite3_column_int64(stmt, 2));
    record.end_tick = static_cast<std::uint64_t>(sqlite3_column_int64(stmt, 3));
    record.format = static_cast<TraceRecord::Format>(sqlite3_column_int(stmt, 4));
    record.storage_path = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 5));
    record.size_bytes = static_cast<std::uint64_t>(sqlite3_column_int64(stmt, 6));
    traces.push_back(std::move(record));
  }

  sqlite3_finalize(stmt);
  return traces;
}

}  // namespace boat::store
