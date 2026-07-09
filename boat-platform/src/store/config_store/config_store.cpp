#include "config_store/config_store.h"

#include <sqlite3.h>

#include <charconv>
#include <cctype>
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

std::string EscapeJsonString(const std::string& input) {
  std::string out;
  out.reserve(input.size());
  for (char ch : input) {
    if (ch == '\\' || ch == '"') {
      out.push_back('\\');
    }
    out.push_back(ch);
  }
  return out;
}

std::string SerializeConfigValue(const ConfigValue& value) {
  return std::visit(
      [](const auto& v) -> std::string {
        using T = std::decay_t<decltype(v)>;
        if constexpr (std::is_same_v<T, std::string>) {
          return "{\"type\":\"string\",\"value\":\"" + EscapeJsonString(v) + "\"}";
        } else if constexpr (std::is_same_v<T, std::int64_t>) {
          return "{\"type\":\"int\",\"value\":" + std::to_string(v) + "}";
        } else if constexpr (std::is_same_v<T, double>) {
          return "{\"type\":\"double\",\"value\":" + std::to_string(v) + "}";
        } else {
          return std::string{"{\"type\":\"bool\",\"value\":"} + (v ? "true" : "false") + "}";
        }
      },
      value);
}

std::optional<std::string> ExtractStringField(const std::string& json, const std::string& key) {
  const std::string token = "\"" + key + "\":";
  const auto pos = json.find(token);
  if (pos == std::string::npos) {
    return std::nullopt;
  }
  auto value_start = pos + token.size();
  while (value_start < json.size() && std::isspace(static_cast<unsigned char>(json[value_start]))) {
    ++value_start;
  }
  if (value_start >= json.size()) {
    return std::nullopt;
  }
  if (json[value_start] == '"') {
    ++value_start;
    auto value_end = value_start;
    while (value_end < json.size() && json[value_end] != '"') {
      if (json[value_end] == '\\' && (value_end + 1) < json.size()) {
        value_end += 2;
        continue;
      }
      ++value_end;
    }
    if (value_end >= json.size()) {
      return std::nullopt;
    }
    // Unescape the extracted string (reverse of EscapeJsonString: \\ -> \ and \" -> ")
    std::string result;
    result.reserve(value_end - value_start);
    for (auto i = value_start; i < value_end; ) {
      if (json[i] == '\\' && (i + 1) < value_end) {
        result.push_back(json[i + 1]);
        i += 2;
      } else {
        result.push_back(json[i]);
        ++i;
      }
    }
    return result;
  }
  auto value_end = value_start;
  while (value_end < json.size() && json[value_end] != ',' && json[value_end] != '}') {
    ++value_end;
  }
  return json.substr(value_start, value_end - value_start);
}

std::optional<ConfigValue> DeserializeConfigValue(const std::string& json) {
  const auto type = ExtractStringField(json, "type");
  const auto value = ExtractStringField(json, "value");
  if (!type.has_value() || !value.has_value()) {
    return std::nullopt;
  }

  if (*type == "string") {
    return ConfigValue{*value};
  }
  if (*type == "int") {
    std::int64_t parsed = 0;
    const auto* begin = value->data();
    const auto* end = value->data() + value->size();
    if (std::from_chars(begin, end, parsed).ec == std::errc{}) {
      return ConfigValue{parsed};
    }
    return std::nullopt;
  }
  if (*type == "double") {
    return ConfigValue{std::stod(*value)};
  }
  if (*type == "bool") {
    return ConfigValue{*value == "true"};
  }
  return std::nullopt;
}

std::optional<ConfigValue> TomlNodeToConfigValue(const toml::node& node) {
  if (const auto v = node.value<std::string>(); v.has_value()) {
    return ConfigValue{*v};
  }
  if (const auto v = node.value<std::int64_t>(); v.has_value()) {
    return ConfigValue{*v};
  }
  if (const auto v = node.value<double>(); v.has_value()) {
    return ConfigValue{*v};
  }
  if (const auto v = node.value<bool>(); v.has_value()) {
    return ConfigValue{*v};
  }
  return std::nullopt;
}

}  // namespace

SqliteTomlConfigStore::SqliteTomlConfigStore(std::string_view db_path) {
  constexpr int kFlags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX;
  if (sqlite3_open_v2(std::string(db_path).c_str(), &db_, kFlags, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to open sqlite config store");
  }
  ExecOrThrow(db_, "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);");
}

SqliteTomlConfigStore::~SqliteTomlConfigStore() {
  if (db_ != nullptr) {
    sqlite3_close_v2(db_);
    db_ = nullptr;
  }
}

void SqliteTomlConfigStore::LoadToml(std::string_view file_path) {
  config_table_ = toml::parse_file(std::string(file_path));

  for (const auto& [key, value] : config_table_) {
    if (const auto config_value = TomlNodeToConfigValue(value)) {
      Set(key.str(), *config_value);
    }
  }
}

std::optional<ConfigValue> SqliteTomlConfigStore::Get(std::string_view key) {
  constexpr const char* kSelectSql = "SELECT value_json FROM config WHERE key = ?;";
  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kSelectSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare config select statement");
  }
  sqlite3_bind_text(stmt, 1, std::string(key).c_str(), -1, SQLITE_TRANSIENT);
  if (sqlite3_step(stmt) == SQLITE_ROW) {
    const char* value_json = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
    const std::string payload = value_json != nullptr ? value_json : "";
    sqlite3_finalize(stmt);
    return DeserializeConfigValue(payload);
  }
  sqlite3_finalize(stmt);

  if (const toml::node* node = config_table_.get(key)) {
    if (const auto value = TomlNodeToConfigValue(*node)) {
      return value;
    }
  }
  return std::nullopt;
}

void SqliteTomlConfigStore::Set(std::string_view key, ConfigValue value) {
  constexpr const char* kUpsertSql = "INSERT OR REPLACE INTO config (key, value_json) VALUES (?, ?);";
  sqlite3_stmt* stmt = nullptr;
  if (sqlite3_prepare_v2(db_, kUpsertSql, -1, &stmt, nullptr) != SQLITE_OK) {
    throw std::runtime_error("failed to prepare config upsert statement");
  }

  const std::string value_json = SerializeConfigValue(value);
  sqlite3_bind_text(stmt, 1, std::string(key).c_str(), -1, SQLITE_TRANSIENT);
  sqlite3_bind_text(stmt, 2, value_json.c_str(), -1, SQLITE_TRANSIENT);
  if (sqlite3_step(stmt) != SQLITE_DONE) {
    sqlite3_finalize(stmt);
    throw std::runtime_error("failed to persist config value");
  }
  sqlite3_finalize(stmt);

  // Keep TOML cache coherent with DB-backed updates.
  config_table_.erase(std::string(key));
}

}  // namespace boat::store
