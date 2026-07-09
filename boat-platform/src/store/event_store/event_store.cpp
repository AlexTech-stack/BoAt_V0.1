#include "event_store/event_store.h"

#include <sqlite3.h>

#include <stdexcept>
#include <string>
#include <vector>

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

SqliteEventStore::SqliteEventStore(std::string_view db_path) {
  constexpr int kFlags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX;
  if (sqlite3_open_v2(std::string(db_path).c_str(), &db_, kFlags, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to open sqlite event store");
  }

  ExecOrThrow(db_, "PRAGMA journal_mode=WAL;");
  ExecOrThrow(db_, "PRAGMA synchronous=NORMAL;");
  ExecOrThrow(
      db_,
      "CREATE TABLE IF NOT EXISTS events ("
      "id TEXT PRIMARY KEY,"
      "simulation_id TEXT NOT NULL,"
      "tick INTEGER NOT NULL,"
      "wall_time_ns INTEGER NOT NULL,"
      "signal_id TEXT NOT NULL,"
      "value_type INTEGER NOT NULL,"
      "value_blob BLOB NOT NULL,"
      "tags TEXT"
      ");");
  ExecOrThrow(db_, "CREATE INDEX IF NOT EXISTS idx_events_sim_tick ON events(simulation_id, tick);");
  ExecOrThrow(db_, "CREATE INDEX IF NOT EXISTS idx_events_signal_tick ON events(signal_id, tick);");
}

SqliteEventStore::~SqliteEventStore() {
  if (db_ != nullptr) {
    sqlite3_close_v2(db_);
    db_ = nullptr;
  }
}

void SqliteEventStore::InsertBatch(std::span<const EventRecord> events) {
  if (events.empty()) {
    return;
  }

  constexpr const char* kInsertSql =
      "INSERT OR REPLACE INTO events "
      "(id, simulation_id, tick, wall_time_ns, signal_id, value_type, value_blob, tags) "
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?);";

  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kInsertSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare event insert statement");
  }

  ExecOrThrow(db_, "BEGIN IMMEDIATE;");
  std::size_t in_tx_count = 0;

  for (const auto& event : events) {
    sqlite3_bind_text(stmt, 1, event.id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, event.simulation_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(stmt, 3, static_cast<sqlite3_int64>(event.tick));
    sqlite3_bind_int64(stmt, 4, static_cast<sqlite3_int64>(event.wall_time_ns));
    sqlite3_bind_text(stmt, 5, event.signal_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 6, event.value_type);
    sqlite3_bind_blob(stmt,
                      7,
                      event.value_blob.empty() ? nullptr : event.value_blob.data(),
                      static_cast<int>(event.value_blob.size()),
                      SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 8, event.tags.c_str(), -1, SQLITE_TRANSIENT);

    if (sqlite3_step(stmt) != SQLITE_DONE) {
      sqlite3_finalize(stmt);
      ExecOrThrow(db_, "ROLLBACK;");
      throw std::runtime_error("failed to insert event");
    }
    sqlite3_reset(stmt);
    sqlite3_clear_bindings(stmt);

    ++in_tx_count;
    if (in_tx_count == 1000U) {
      ExecOrThrow(db_, "COMMIT;");
      ExecOrThrow(db_, "BEGIN IMMEDIATE;");
      in_tx_count = 0;
    }
  }

  ExecOrThrow(db_, "COMMIT;");
  sqlite3_finalize(stmt);
}

std::vector<EventRecord> SqliteEventStore::Query(const EventFilter& filter) {
  std::string sql =
      "SELECT id, simulation_id, tick, wall_time_ns, signal_id, value_type, value_blob, tags "
      "FROM events WHERE 1=1";
  if (filter.simulation_id.has_value()) {
    sql += " AND simulation_id = ?";
  }
  if (filter.signal_id.has_value()) {
    sql += " AND signal_id = ?";
  }
  if (filter.tick_min.has_value()) {
    sql += " AND tick >= ?";
  }
  if (filter.tick_max.has_value()) {
    sql += " AND tick <= ?";
  }
  sql += " ORDER BY tick ASC;";

  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare event query statement");
  }

  int bind_idx = 1;
  if (filter.simulation_id.has_value()) {
    sqlite3_bind_text(stmt, bind_idx++, filter.simulation_id->c_str(), -1, SQLITE_TRANSIENT);
  }
  if (filter.signal_id.has_value()) {
    sqlite3_bind_text(stmt, bind_idx++, filter.signal_id->c_str(), -1, SQLITE_TRANSIENT);
  }
  if (filter.tick_min.has_value()) {
    sqlite3_bind_int64(stmt, bind_idx++, static_cast<sqlite3_int64>(*filter.tick_min));
  }
  if (filter.tick_max.has_value()) {
    sqlite3_bind_int64(stmt, bind_idx++, static_cast<sqlite3_int64>(*filter.tick_max));
  }

  std::vector<EventRecord> out;
  while (sqlite3_step(stmt) == SQLITE_ROW) {
    EventRecord row;
    row.id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
    row.simulation_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
    row.tick = static_cast<std::uint64_t>(sqlite3_column_int64(stmt, 2));
    row.wall_time_ns = static_cast<std::int64_t>(sqlite3_column_int64(stmt, 3));
    row.signal_id = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
    row.value_type = sqlite3_column_int(stmt, 5);
    const auto* blob = static_cast<const std::uint8_t*>(sqlite3_column_blob(stmt, 6));
    const auto blob_size = static_cast<std::size_t>(sqlite3_column_bytes(stmt, 6));
    if (blob == nullptr || blob_size == 0U) {
      row.value_blob.clear();
    } else {
      row.value_blob.assign(blob, blob + blob_size);
    }
    const auto* tags_text = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 7));
    row.tags = tags_text != nullptr ? tags_text : "";
    out.push_back(std::move(row));
  }

  sqlite3_finalize(stmt);
  return out;
}

}  // namespace boat::store
