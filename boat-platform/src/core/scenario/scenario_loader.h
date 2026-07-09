#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace boat::core {

struct SignalDef {
  std::string id;
  std::string name;
  std::string type;
  std::string unit;
};

struct FaultEventDef {
  std::uint64_t tick;
  std::string signal_id;
  std::string fault_type;
  double magnitude;
};

struct PluginRef {
  std::string so_path;
  std::string config_json;
};

struct ScenarioDef {
  std::string id;
  std::string name;
  std::string version;
  std::uint64_t duration_ticks{0};
  std::uint64_t seed{0};
  std::vector<PluginRef> plugins;
  std::vector<SignalDef> signals;
  std::vector<FaultEventDef> faults;
};

class ScenarioLoader {
 public:
  static ScenarioDef LoadFromFile(const std::string& path);
  static ScenarioDef LoadFromJson(const std::string& json_text);
  static ScenarioDef LoadFromYaml(const std::string& yaml_text);
};

}  // namespace boat::core
