#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <variant>

#include <toml++/toml.hpp>

struct sqlite3;

namespace boat::store {

using ConfigValue = std::variant<std::string, std::int64_t, double, bool>;

class IConfigStore {
 public:
  virtual ~IConfigStore() = default;
  virtual std::optional<ConfigValue> Get(std::string_view key) = 0;
  virtual void Set(std::string_view key, ConfigValue value) = 0;
  virtual void LoadToml(std::string_view file_path) = 0;
};

class SqliteTomlConfigStore : public IConfigStore {
 public:
  explicit SqliteTomlConfigStore(std::string_view db_path);
  ~SqliteTomlConfigStore() override;

  std::optional<ConfigValue> Get(std::string_view key) override;
  void Set(std::string_view key, ConfigValue value) override;
  void LoadToml(std::string_view file_path) override;

 private:
  sqlite3* db_{nullptr};
  toml::table config_table_;
};

}  // namespace boat::store
