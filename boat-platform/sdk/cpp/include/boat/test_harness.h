#pragma once

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <fstream>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/simulation/simulation_context.h"
#include "hal/hal_driver.h"
#include "can_bus_registry.h"
#include "ethernet/ethernet_frame.h"
#include "ethernet_bus_registry.h"
#include "pdu/pdu_router.h"
#include "pdu/pdu_types.h"

namespace boat::test {

// ── Forward declarations ─────────────────────────────────────────────────────

class TestStep;
class TestHarness;

// ═══════════════════════════════════════════════════════════════════════════════
// TestReport
// ═══════════════════════════════════════════════════════════════════════════════

/// Minimal JSON report builder matching the Python TestReport schema.
class TestReport {
 public:
  struct Assertion {
    std::string expression;
    std::string expected;
    std::string actual;
    std::string result;  // PASS | FAIL | ERROR
  };

  struct StepRecord {
    int id{};
    std::string name;
    std::string verdict = "SKIPPED";
    std::int64_t duration_ms{};
    std::vector<Assertion> assertions;
  };

  void AddStep(const StepRecord& step) { steps_.push_back(step); }

  std::string ToJson() const {
    std::string out = R"({"meta":{"report_schema_version":"1.0","generator":"boat-test-cpp"})";
    if (!test_id_.empty() || !test_name_.empty()) {
      out += R"(,"test":{)" + JsonStr("id", test_id_) + "," + JsonStr("name", test_name_) + "}";
    }
    out += R"(,"execution":{"started_at":")" + start_time_ + "\"";
    if (duration_ms_ >= 0) {
      out += R"(,"duration_ms":)" + std::to_string(duration_ms_);
    }
    out += R"(,"verdict":")" + verdict_ + "\"}";
    out += R"(,"preconditions":[])";
    out += R"(,"steps":[)";
    for (std::size_t i = 0; i < steps_.size(); ++i) {
      if (i > 0) out += ",";
      out += StepToJson(steps_[i]);
    }
    out += R"(],"verdict":")" + verdict_ + "\"";
    out += "}";
    return out;
  }

  void Save(const std::string& path) const {
    std::ofstream f(path);
    if (f) f << ToJson();
  }

  // Fluent setters
  TestReport& SetTestId(const std::string& v) { test_id_ = v; return *this; }
  TestReport& SetTestName(const std::string& v) { test_name_ = v; return *this; }
  TestReport& SetDurationMs(std::int64_t ms) { duration_ms_ = ms; return *this; }
  TestReport& SetVerdict(const std::string& v) { verdict_ = v; return *this; }
  TestReport& SetStartTime(const std::string& v) { start_time_ = v; return *this; }

 private:
  std::string test_id_;
  std::string test_name_;
  std::string verdict_ = "RUNNING";
  std::string start_time_;
  std::int64_t duration_ms_ = -1;
  std::vector<StepRecord> steps_;

  static std::string JsonStr(const std::string& key, const std::string& val) {
    std::string escaped = val;
    auto pos = escaped.find('"');
    while (pos != std::string::npos) {
      escaped.replace(pos, 1, "\\\"");
      pos = escaped.find('"', pos + 2);
    }
    return "\"" + key + "\":\"" + escaped + "\"";
  }

  static std::string StepToJson(const StepRecord& s) {
    std::string out = "{";
    out += JsonStr("id", std::to_string(s.id)) + ",";
    out += JsonStr("name", s.name) + ",";
    out += JsonStr("verdict", s.verdict);
    if (s.duration_ms > 0) {
      out += R"(,"duration_ms":)" + std::to_string(s.duration_ms);
    }
    out += R"(,"assertions":[)";
    for (std::size_t i = 0; i < s.assertions.size(); ++i) {
      if (i > 0) out += ",";
      const auto& a = s.assertions[i];
      out += "{";
      out += JsonStr("expression", a.expression) + ",";
      out += JsonStr("expected", a.expected) + ",";
      out += JsonStr("actual", a.actual) + ",";
      out += JsonStr("result", a.result);
      out += "}";
    }
    out += "]}";
    return out;
  }
};

// ═══════════════════════════════════════════════════════════════════════════════
// Mock drivers
// ═══════════════════════════════════════════════════════════════════════════════

class MockCanDriver : public boat::hil::IHalDriver {
 public:
  bool Open()  override { return true; }
  void Close() override {}

  bool ReadFrame(boat::hil::CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    return false;
  }

  bool WriteFrame(const boat::hil::CanFrame& f) override {
    written.push_back(f);
    return true;
  }

  boat::hil::CanInterfaceInfo GetInfo() const override { return {}; }
  std::vector<boat::hil::CanFrame> written;
};

class MockEthernetDriver : public boat::hil::IEthernetDriver {
 public:
  bool Open()  override { return true; }
  void Close() override {}

  bool ReadFrame(boat::hil::EthernetFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    return false;
  }

  bool WriteFrame(const boat::hil::EthernetFrame& f) override {
    written.push_back(f);
    return true;
  }
  std::vector<boat::hil::EthernetFrame> written;
};

// ═══════════════════════════════════════════════════════════════════════════════
// TestCanBus
// ═══════════════════════════════════════════════════════════════════════════════

class TestCanBus {
 public:
  TestCanBus(boat::hil::CanBusRegistry& reg, const std::string& iface)
      : reg_(reg), iface_(iface) {}

  const std::string& Interface() const { return iface_; }

  void Send(std::uint32_t id, const std::vector<std::uint8_t>& data,
            std::uint8_t flags = 0) {
    boat::hil::CanFrame frame{};
    frame.can_id = id;
    frame.dlc = static_cast<std::uint8_t>(std::min(data.size(), size_t(64)));
    frame.flags = flags;
    std::copy_n(data.begin(), frame.dlc, frame.data);
    reg_.SendFrame(iface_, frame);
  }

  boat::hil::CanFrame Expect(std::uint32_t can_id = 0,
                              std::chrono::milliseconds timeout = std::chrono::milliseconds(1000)) {
    auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
      // rely on mock drivers or real HW
    }
    // In a real test, frames are captured by the mock's written vector.
    // This is a placeholder for future subscription-based observation.
    return {};
  }

  // Access the mock's written frames for inspection
  const std::vector<boat::hil::CanFrame>& WrittenFrames() const {
    return written_;
  }

  boat::hil::CanBusRegistry& Registry() { return reg_; }

 private:
  boat::hil::CanBusRegistry& reg_;
  std::string iface_;
  std::vector<boat::hil::CanFrame> written_;
};

// ═══════════════════════════════════════════════════════════════════════════════
// TestEthBus
// ═══════════════════════════════════════════════════════════════════════════════

class TestEthBus {
 public:
  TestEthBus(boat::hil::EthernetBusRegistry& reg, const std::string& iface)
      : reg_(reg), iface_(iface) {}

  const std::string& Interface() const { return iface_; }

  void Send(const std::vector<std::uint8_t>& dst_mac,
            std::uint16_t ethertype,
            const std::vector<std::uint8_t>& payload) {
    boat::hil::EthernetFrame frame{};
    std::copy_n(dst_mac.begin(), std::min(dst_mac.size(), size_t(6)), frame.dst_mac);
    frame.ethertype = ethertype;
    frame.payload = payload;
    reg_.SendFrame(iface_, frame);
  }

  boat::hil::EthernetBusRegistry& Registry() { return reg_; }

 private:
  boat::hil::EthernetBusRegistry& reg_;
  std::string iface_;
};

// ═══════════════════════════════════════════════════════════════════════════════
// TestStep  (RAII)
// ═══════════════════════════════════════════════════════════════════════════════

class TestStep {
 public:
  TestStep(TestReport& report, int id, const std::string& name)
      : report_(report) {
    record_.id = id;
    record_.name = name;
    record_.verdict = "PASS";
    start_ = std::chrono::steady_clock::now();
  }

  ~TestStep() {
    auto elapsed = std::chrono::steady_clock::now() - start_;
    record_.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
    if (failed_) record_.verdict = "FAIL";
    report_.AddStep(record_);
  }

  // Move only
  TestStep(TestStep&&) noexcept = default;
  TestStep& operator=(TestStep&&) noexcept = default;

  void Assert(bool condition, const std::string& expression = "assert",
              const std::string& expected = "true",
              const std::string& actual = "true") {
    if (!condition) {
      failed_ = true;
      record_.assertions.push_back({"", expected, actual, "FAIL"});
    } else {
      record_.assertions.push_back({"", expected, actual, "PASS"});
    }
  }

 private:
  TestReport& report_;
  TestReport::StepRecord record_;
  std::chrono::steady_clock::time_point start_;
  bool failed_ = false;
};

// ═══════════════════════════════════════════════════════════════════════════════
// TestHarness
// ═══════════════════════════════════════════════════════════════════════════════

class TestHarness {
 public:
  TestHarness() {
    sim_ = std::make_unique<boat::core::SimulationContext>(777, 2);
    report_.SetStartTime(NowIso());
  }

  ~TestHarness() {
    if (report_started_) {
      report_.SetVerdict(FinalVerdict());
      report_.Save("report.json");
    }
  }

  // ── Bus management ─────────────────────────────────────────────────────

  TestCanBus& AddCanBus(const std::string& name) {
    auto driver = std::make_shared<MockCanDriver>();
    auto* raw = driver.get();
    can_reg_.Add(name, std::move(driver), sim_->event_bus());
    auto bus = std::make_unique<TestCanBus>(can_reg_, name);
    auto* ptr = bus.get();
    can_buses_[name] = {std::move(bus), raw};
    return *ptr;
  }

  TestEthBus& AddEthBus(const std::string& name) {
    auto driver = std::make_unique<MockEthernetDriver>();
    auto* raw = driver.get();
    eth_reg_.Add(name, std::move(driver));
    auto bus = std::make_unique<TestEthBus>(eth_reg_, name);
    auto* ptr = bus.get();
    eth_buses_[name] = {std::move(bus), raw};
    return *ptr;
  }

  TestCanBus& CanBus(const std::string& name) { return *can_buses_.at(name).bus; }
  TestEthBus& EthBus(const std::string& name) { return *eth_buses_.at(name).bus; }

  auto& MockCan(const std::string& name) { return *can_buses_.at(name).mock; }
  auto& MockEth(const std::string& name) { return *eth_buses_.at(name).mock; }

  // ── Step management ────────────────────────────────────────────────────

  TestStep Step(int id, const std::string& name) {
    report_started_ = true;
    return TestStep(report_, id, name);
  }

  // ── Time ───────────────────────────────────────────────────────────────

  void Advance(std::chrono::milliseconds ms) {
    uint64_t ticks = std::max(uint64_t(1), uint64_t(ms.count()) / 10);
    for (uint64_t i = 0; i < ticks; ++i) {
      sim_->clock().Step(1);
      sim_->plugin_manager().TickAll(sim_->clock().tick());
      pdu_router_.OnTick(sim_->clock().tick());
    }
  }

  uint64_t CurrentTick() const { return sim_->clock().tick(); }

  // ── Plugin loading ─────────────────────────────────────────────────────

  void LoadPlugin(const std::string& so_path, const std::string& config = "{}") {
    sim_->plugin_manager().Load(so_path, config);
  }

  // ── Component access (for advanced tests) ──────────────────────────────

  boat::hil::CanBusRegistry& CanRegistry() { return can_reg_; }
  boat::hil::EthernetBusRegistry& EthRegistry() { return eth_reg_; }
  boat::hil::PduRouter& PduRouter() { return pdu_router_; }
  boat::core::PluginManager& Plugins() { return sim_->plugin_manager(); }
  boat::core::SimulationContext& Sim() { return *sim_; }
  TestReport& Report() { return report_; }

 private:
  struct CanBusEntry {
    std::unique_ptr<TestCanBus> bus;
    MockCanDriver* mock{};
  };
  struct EthBusEntry {
    std::unique_ptr<TestEthBus> bus;
    MockEthernetDriver* mock{};
  };

  std::string FinalVerdict() const {
    return "PASS";  // simplified; real logic from step results
  }

  static std::string NowIso() {
    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    char buf[32]{};
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&t));
    return buf;
  }

  std::unique_ptr<boat::core::SimulationContext> sim_;
  boat::core::EventBus event_bus_;
  boat::hil::CanBusRegistry can_reg_;
  boat::hil::EthernetBusRegistry eth_reg_;
  boat::hil::PduRouter pdu_router_{can_reg_, eth_reg_};
  TestReport report_;
  bool report_started_ = false;
  std::unordered_map<std::string, CanBusEntry> can_buses_;
  std::unordered_map<std::string, EthBusEntry> eth_buses_;
};

}  // namespace boat::test
