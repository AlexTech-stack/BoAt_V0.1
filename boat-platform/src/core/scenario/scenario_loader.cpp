#include "scenario/scenario_loader.h"

#include <cctype>
#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <unordered_map>
#include <variant>

namespace boat::core {
namespace {

struct JsonMap;
struct JsonValue {
  std::variant<std::nullptr_t, bool, double, std::string, std::vector<JsonValue>, std::unique_ptr<JsonMap> > value;
};
struct JsonMap {
  std::unordered_map<std::string, JsonValue> map;
};

using JsonObject = JsonMap;
using JsonArray = std::vector<JsonValue>;

class JsonParser {
 public:
  explicit JsonParser(std::string_view text) : text_(text) {}

  JsonValue Parse() {
    SkipWs();
    JsonValue v = ParseValue();
    SkipWs();
    return v;
  }

 private:
  JsonValue ParseValue() {
    SkipWs();
    if (Match('{')) return JsonValue{std::make_unique<JsonMap>(ParseObject()) };
    if (Match('[')) return JsonValue{ParseArray()};
    if (Match('"')) return JsonValue{ParseString()};
    if (std::isdigit(Peek()) || Peek() == '-') return JsonValue{ParseNumber()};
    if (ConsumeWord("true")) return JsonValue{true};
    if (ConsumeWord("false")) return JsonValue{false};
    if (ConsumeWord("null")) return JsonValue{nullptr};
    throw std::runtime_error("invalid JSON value");
  }

  JsonMap ParseObject() {
    JsonMap obj;
    SkipWs();
    if (Match('}')) return obj;
    for (;;) {
      SkipWs();
      Expect('"');
      const std::string key = ParseString();
      SkipWs();
      Expect(':');
      obj.map[key] = ParseValue();
      SkipWs();
      if (Match('}')) break;
      Expect(',');
    }
    return obj;
  }

  JsonArray ParseArray() {
    JsonArray arr;
    SkipWs();
    if (Match(']')) return arr;
    for (;;) {
      arr.push_back(ParseValue());
      SkipWs();
      if (Match(']')) break;
      Expect(',');
    }
    return arr;
  }

  std::string ParseString() {
    std::string out;
    while (pos_ < text_.size()) {
      char c = text_[pos_++];
      if (c == '"') break;
      if (c == '\\' && pos_ < text_.size()) {
        char n = text_[pos_++];
        if (n == '"' || n == '\\' || n == '/') out.push_back(n);
        else if (n == 'n') out.push_back('\n');
        else if (n == 't') out.push_back('\t');
        else out.push_back(n);
      } else {
        out.push_back(c);
      }
    }
    return out;
  }

  double ParseNumber() {
    const std::size_t start = pos_;
    if (Peek() == '-') ++pos_;
    while (std::isdigit(Peek())) ++pos_;
    if (Peek() == '.') {
      ++pos_;
      while (std::isdigit(Peek())) ++pos_;
    }
    return std::stod(std::string(text_.substr(start, pos_ - start)));
  }

  void SkipWs() {
    while (pos_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[pos_]))) ++pos_;
  }
  bool Match(char c) {
    if (Peek() == c) {
      ++pos_;
      return true;
    }
    return false;
  }
  void Expect(char c) {
    if (!Match(c)) throw std::runtime_error("unexpected token");
  }
  bool ConsumeWord(std::string_view word) {
    if (text_.substr(pos_, word.size()) == word) {
      pos_ += word.size();
      return true;
    }
    return false;
  }
  char Peek() const { return pos_ < text_.size() ? text_[pos_] : '\0'; }

  std::string_view text_;
  std::size_t pos_{0};
};

const std::unordered_map<std::string, JsonValue>& AsObject(const JsonValue& value) {
  return std::get<std::unique_ptr<JsonMap> >(value.value)->map;
}
const JsonArray& AsArray(const JsonValue& value) {
  return std::get<JsonArray>(value.value);
}
std::string AsString(const JsonValue& value) {
  return std::get<std::string>(value.value);
}
std::uint64_t AsU64(const JsonValue& value) {
  return static_cast<std::uint64_t>(std::get<double>(value.value));
}
double AsDouble(const JsonValue& value) {
  return std::get<double>(value.value);
}

ScenarioDef ParseScenarioFromObject(const std::unordered_map<std::string, JsonValue>& root) {
  ScenarioDef scenario;
  scenario.id = AsString(root.at("id"));
  scenario.name = AsString(root.at("name"));
  scenario.version = AsString(root.at("version"));
  scenario.duration_ticks = AsU64(root.at("duration_ticks"));
  scenario.seed = AsU64(root.at("seed"));

  for (const auto& plugin_v : AsArray(root.at("plugins"))) {
    const auto& obj = AsObject(plugin_v);
    scenario.plugins.push_back({AsString(obj.at("so_path")), AsString(obj.at("config_json"))});
  }
  for (const auto& signal_v : AsArray(root.at("signals"))) {
    const auto& obj = AsObject(signal_v);
    scenario.signals.push_back({AsString(obj.at("id")), AsString(obj.at("name")), AsString(obj.at("type")),
                                AsString(obj.at("unit"))});
  }
  for (const auto& fault_v : AsArray(root.at("faults"))) {
    const auto& obj = AsObject(fault_v);
    scenario.faults.push_back(
        {AsU64(obj.at("tick")), AsString(obj.at("signal_id")), AsString(obj.at("fault_type")), AsDouble(obj.at("magnitude"))});
  }
  return scenario;
}

std::string Trim(const std::string& s) {
  const auto a = s.find_first_not_of(" \t\r\n");
  if (a == std::string::npos) return {};
  const auto b = s.find_last_not_of(" \t\r\n");
  return s.substr(a, b - a + 1);
}

std::string Unquote(const std::string& s) {
  if (s.size() >= 2 && ((s.front() == '"' && s.back() == '"') || (s.front() == '\'' && s.back() == '\''))) {
    return s.substr(1, s.size() - 2);
  }
  return s;
}

}  // namespace

ScenarioDef ScenarioLoader::LoadFromFile(const std::string& path) {
  std::ifstream in(path);
  if (!in) {
    throw std::runtime_error("unable to open scenario file");
  }
  std::stringstream buffer;
  buffer << in.rdbuf();
  if (path.size() >= 5 && path.substr(path.size() - 5) == ".json") {
    return LoadFromJson(buffer.str());
  }
  return LoadFromYaml(buffer.str());
}

ScenarioDef ScenarioLoader::LoadFromJson(const std::string& json_text) {
  JsonParser parser(json_text);
  JsonValue parsed = parser.Parse();
  return ParseScenarioFromObject(AsObject(parsed));
}

ScenarioDef ScenarioLoader::LoadFromYaml(const std::string& yaml_text) {
  ScenarioDef scenario;
  std::istringstream stream(yaml_text);
  std::string line;
  std::string section;
  PluginRef plugin{};
  SignalDef signal{};
  FaultEventDef fault{};
  bool has_item = false;

  auto flush_item = [&]() {
    if (!has_item) return;
    if (section == "plugins") scenario.plugins.push_back(plugin);
    if (section == "signals") scenario.signals.push_back(signal);
    if (section == "faults") scenario.faults.push_back(fault);
    plugin = {};
    signal = {};
    fault = {};
    has_item = false;
  };

  while (std::getline(stream, line)) {
    const std::string trimmed = Trim(line);
    if (trimmed.empty() || trimmed[0] == '#') continue;

    if (trimmed == "plugins:" || trimmed == "signals:" || trimmed == "faults:") {
      flush_item();
      section = trimmed.substr(0, trimmed.size() - 1);
      continue;
    }

    if (trimmed.rfind("- ", 0) == 0) {
      flush_item();
      has_item = true;
      const std::string kv = Trim(trimmed.substr(2));
      const auto colon = kv.find(':');
      if (colon == std::string::npos) continue;
      const std::string key = Trim(kv.substr(0, colon));
      const std::string value = Unquote(Trim(kv.substr(colon + 1)));
      if (section == "plugins") {
        if (key == "so_path") plugin.so_path = value;
        if (key == "config_json") plugin.config_json = value;
      } else if (section == "signals") {
        if (key == "id") signal.id = value;
        if (key == "name") signal.name = value;
        if (key == "type") signal.type = value;
        if (key == "unit") signal.unit = value;
      } else if (section == "faults") {
        if (key == "tick") fault.tick = std::stoull(value);
        if (key == "signal_id") fault.signal_id = value;
        if (key == "fault_type") fault.fault_type = value;
        if (key == "magnitude") fault.magnitude = std::stod(value);
      }
      continue;
    }

    const auto colon = trimmed.find(':');
    if (colon == std::string::npos) continue;
    const std::string key = Trim(trimmed.substr(0, colon));
    const std::string value = Unquote(Trim(trimmed.substr(colon + 1)));

    if (section == "plugins") {
      if (key == "so_path") plugin.so_path = value;
      if (key == "config_json") plugin.config_json = value;
      has_item = true;
    } else if (section == "signals") {
      if (key == "id") signal.id = value;
      if (key == "name") signal.name = value;
      if (key == "type") signal.type = value;
      if (key == "unit") signal.unit = value;
      has_item = true;
    } else if (section == "faults") {
      if (key == "tick") fault.tick = std::stoull(value);
      if (key == "signal_id") fault.signal_id = value;
      if (key == "fault_type") fault.fault_type = value;
      if (key == "magnitude") fault.magnitude = std::stod(value);
      has_item = true;
    } else {
      if (key == "id") scenario.id = value;
      if (key == "name") scenario.name = value;
      if (key == "version") scenario.version = value;
      if (key == "duration_ticks") scenario.duration_ticks = std::stoull(value);
      if (key == "seed") scenario.seed = std::stoull(value);
    }
  }
  flush_item();
  return scenario;
}

}  // namespace boat::core
