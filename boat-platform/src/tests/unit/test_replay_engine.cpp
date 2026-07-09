#include <catch2/catch_test_macros.hpp>

#include <array>
#include <atomic>
#include <cstdint>
#include <chrono>
#include <cstring>
#include <mutex>
#include <span>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "boat/v1/frame.pb.h"
#include "event/event_bus.h"
#include "event_store/event_store.h"
#include "replay_engine/replay_engine.h"
#include "trace_store/trace_store.h"

using namespace boat::replay;
using namespace boat::store;

namespace {

std::vector<std::uint8_t> MakeTraceRecord(std::uint32_t can_id, std::uint64_t tick_ms,
                                          const std::vector<std::uint8_t>& payload) {
  boat::v1::Frame proto;
  proto.set_bus_type(boat::v1::Frame::CAN);
  proto.set_timestamp_ns(tick_ms * 1'000'000ULL);
  proto.set_payload(payload.data(), payload.size());
  proto.mutable_can()->set_can_id(can_id);
  proto.mutable_can()->set_dlc(static_cast<std::uint32_t>(payload.size()));
  proto.mutable_can()->set_flags(0);

  std::string raw = proto.SerializeAsString();
  std::uint32_t len = static_cast<std::uint32_t>(raw.size());

  std::vector<std::uint8_t> record(sizeof(len) + raw.size());
  std::memcpy(record.data(), &len, sizeof(len));
  std::memcpy(record.data() + sizeof(len), raw.data(), raw.size());
  return record;
}

std::vector<std::uint8_t> BuildSequentialTrace(std::uint64_t start_tick, std::uint64_t count) {
  std::vector<std::uint8_t> trace_data;
  for (std::uint64_t i = 0; i < count; ++i) {
    std::uint8_t val = static_cast<std::uint8_t>(i & 0xFF);
    auto record = MakeTraceRecord(100, start_tick + i * 10, {val});
    trace_data.insert(trace_data.end(), record.begin(), record.end());
  }
  return trace_data;
}

struct MockTraceStore : ITraceStore {
  std::unordered_map<std::string, std::vector<std::uint8_t>> traces;
  std::vector<std::string> unmapped;

  void WriteTrace(const TraceRecord& meta, std::span<const std::uint8_t> data) override {
    traces[meta.id] = std::vector<std::uint8_t>(data.begin(), data.end());
  }
  std::span<const std::uint8_t> ReadTraceMmap(const std::string& trace_id) override {
    const auto it = traces.find(trace_id);
    if (it == traces.end()) {
      throw std::runtime_error("trace id not found");
    }
    return it->second;
  }
  std::vector<TraceRecord> ListTraces(const std::string&) override { return {}; }
  std::vector<TraceRecord> ListAllTraces() override { return {}; }
  void UnmapTrace(const std::string& trace_id) override {
    unmapped.push_back(trace_id);
  }
};

struct MockEventStore : IEventStore {
  std::vector<EventRecord> inserted;
  std::vector<EventRecord> QueryResult;

  void InsertBatch(std::span<const EventRecord> events) override {
    for (const auto& e : events) {
      inserted.push_back(e);
    }
  }
  std::vector<EventRecord> Query(const EventFilter&) override { return QueryResult; }
};

}  // namespace

TEST_CASE("ReplayController Start/Stop lifecycle", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["lifecycle"] = trace_data;

  ReplayConfig config;
  config.trace_id = "lifecycle";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 100.0;

  controller.Start(config);
  REQUIRE_FALSE(controller.HasError());

  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 3);
  REQUIRE(trace_store.unmapped.size() == 1);
  REQUIRE(trace_store.unmapped[0] == "lifecycle");
}

TEST_CASE("ReplayController replays records in tick order", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 5);
  trace_store.traces["ordered"] = trace_data;

  ReplayConfig config;
  config.trace_id = "ordered";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 5);
  REQUIRE(event_store.inserted[0].tick == 100);
  REQUIRE(event_store.inserted[1].tick == 110);
  REQUIRE(event_store.inserted[2].tick == 120);
  REQUIRE(event_store.inserted[3].tick == 130);
  REQUIRE(event_store.inserted[4].tick == 140);
}

TEST_CASE("ReplayController publishes events on EventBus", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(200, 2);
  trace_store.traces["bus_test"] = trace_data;

  std::atomic<int> replay_events{0};

  event_bus.Subscribe(kReplayBusEventType, [&](const boat::core::BusEvent&) { ++replay_events; });

  ReplayConfig config;
  config.trace_id = "bus_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  event_bus.Dispatch();
  controller.Stop();

  REQUIRE(replay_events.load() >= 2);
}

TEST_CASE("ReplayController accelerated speed finishes faster than real-time", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 10);
  trace_store.traces["fast"] = trace_data;

  ReplayConfig config;
  config.trace_id = "fast";
  config.speed = ReplaySpeed::ACCELERATED;
  config.speed_multiplier = 1000.0;

  auto start = std::chrono::steady_clock::now();
  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  controller.Stop();
  auto elapsed = std::chrono::steady_clock::now() - start;

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 10);
  REQUIRE(elapsed < std::chrono::milliseconds(500));
}

TEST_CASE("ReplayController step-by-step pauses after each record", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["step"] = trace_data;

  ReplayConfig config;
  config.trace_id = "step";
  config.speed = ReplaySpeed::STEP_BY_STEP;
  config.speed_multiplier = 1.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  REQUIRE(event_store.inserted.size() == 1);

  controller.Resume();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  REQUIRE(event_store.inserted.size() == 2);

  controller.Resume();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  REQUIRE(event_store.inserted.size() == 3);

  controller.Stop();
  REQUIRE_FALSE(controller.HasError());
}

TEST_CASE("ReplayController Pause/Resume suspends and continues replay", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 200);
  trace_store.traces["pause_test"] = trace_data;

  ReplayConfig config;
  config.trace_id = "pause_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 2.0;

  controller.Start(config);

  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  controller.Pause();

  std::this_thread::sleep_for(std::chrono::milliseconds(20));

  auto count_after_pause = event_store.inserted.size();
  REQUIRE(count_after_pause > 0);

  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  auto count_while_paused = event_store.inserted.size();
  REQUIRE(count_while_paused <= count_after_pause + 2);

  controller.Resume();

  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  auto count_after_resume = event_store.inserted.size();
  REQUIRE(count_after_resume > count_after_pause);

  controller.Stop();
  REQUIRE_FALSE(controller.HasError());
}

TEST_CASE("ReplayController Seek jumps to requested tick", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 10);
  trace_store.traces["seek_test"] = trace_data;

  ReplayConfig config;
  config.trace_id = "seek_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Seek(150);
  controller.Resume();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  bool found_150 = false;
  for (const auto& e : event_store.inserted) {
    if (e.tick == 150) found_150 = true;
  }
  REQUIRE(found_150);
}

TEST_CASE("ReplayController error on missing trace", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  ReplayConfig config;
  config.trace_id = "nonexistent";

  REQUIRE_THROWS_AS(controller.Start(config), std::runtime_error);
}

TEST_CASE("ReplayController empty trace finishes immediately", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  trace_store.traces["empty"] = {};

  ReplayConfig config;
  config.trace_id = "empty";
  config.speed = ReplaySpeed::REAL_TIME;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.empty());
}

TEST_CASE("ReplayController Stop unmaps trace", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["unmap_test"] = trace_data;

  ReplayConfig config;
  config.trace_id = "unmap_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  REQUIRE(trace_store.unmapped.empty());
  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE(trace_store.unmapped.size() == 1);
  REQUIRE(trace_store.unmapped[0] == "unmap_test");
}

TEST_CASE("ReplayController multiple Start calls stop previous replay", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["first"] = trace_data;
  trace_store.traces["second"] = trace_data;

  ReplayConfig config1{.trace_id = "first", .speed = ReplaySpeed::REAL_TIME, .speed_multiplier = 1000.0};
  ReplayConfig config2{.trace_id = "second", .speed = ReplaySpeed::REAL_TIME, .speed_multiplier = 1000.0};

  controller.Start(config1);
  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  controller.Start(config2);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE(trace_store.unmapped.size() == 2);
}

TEST_CASE("ReplayController StartFromEvents replays events from event store", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  std::vector<EventRecord> events = {
      {.id = "e1", .simulation_id = "sim1", .tick = 100, .wall_time_ns = 100000000, .signal_id = "sig1",
       .value_type = 1, .value_blob = {0x01, 0x02}, .tags = "{}"},
      {.id = "e2", .simulation_id = "sim1", .tick = 110, .wall_time_ns = 110000000, .signal_id = "sig1",
       .value_type = 1, .value_blob = {0x03, 0x04}, .tags = "{}"},
      {.id = "e3", .simulation_id = "sim1", .tick = 120, .wall_time_ns = 120000000, .signal_id = "sig1",
       .value_type = 1, .value_blob = {0x05, 0x06}, .tags = "{}"},
  };
  event_store.QueryResult = events;

  EventFilter filter;
  filter.simulation_id = "sim1";

  ReplayConfig cfg;
  cfg.speed = ReplaySpeed::ACCELERATED;
  cfg.speed_multiplier = 100.0;
  cfg.start_tick = 100;
  controller.StartFromEvents(filter, cfg);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() >= 3);
}

TEST_CASE("ReplayController StartFromEvents handles empty result", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  EventFilter filter;
  filter.simulation_id = "nonexistent";

  controller.StartFromEvents(filter);

  REQUIRE_FALSE(controller.HasError());
}

TEST_CASE("ReplayController defaults speed_multiplier to 1.0 when zero", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["zero_mult"] = trace_data;

  ReplayConfig config;
  config.trace_id = "zero_mult";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 0.0;
  config.start_tick = 100;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 3);
}

TEST_CASE("ReplayController anchors to the actual first-record tick for absolute-epoch traces",
          "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  // Real imported traces store absolute epoch-millisecond timestamps, not
  // small relative tick counts starting near 0 -- StartReplay never sets
  // ReplayConfig::start_tick, so it defaults to 0. Before the fix, the
  // schedule was anchored to that raw 0 instead of the trace's actual first
  // tick, producing a multi-decade wait deadline on record one and hanging
  // forever (zero events, ever).
  constexpr std::uint64_t kEpochBaseMs = 1'775'052'551'000ULL;
  auto trace_data = BuildSequentialTrace(kEpochBaseMs, 5);
  trace_store.traces["epoch_trace"] = trace_data;

  ReplayConfig config;
  config.trace_id = "epoch_trace";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  auto start = std::chrono::steady_clock::now();
  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  controller.Stop();
  auto elapsed = std::chrono::steady_clock::now() - start;

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 5);
  REQUIRE(elapsed < std::chrono::milliseconds(500));
}

TEST_CASE("ReplayController re-anchors to the first-record tick on each loop pass",
          "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  constexpr std::uint64_t kEpochBaseMs = 1'775'052'551'000ULL;
  auto trace_data = BuildSequentialTrace(kEpochBaseMs, 3);
  trace_store.traces["epoch_loop"] = trace_data;

  ReplayConfig config;
  config.trace_id = "epoch_loop";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;
  config.loop_delay_ms = 5;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(150));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  // Without the loop-restart anchor fix, the second pass re-anchors to
  // start_tick (0) and hangs the same way the first pass would without the
  // seek fix -- so more than one pass' worth of records confirms looping
  // actually progresses past the first cycle.
  REQUIRE(event_store.inserted.size() > 3);
}

TEST_CASE("ReplayController maps CAN channel to interface via ReplayConfig.buses",
          "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  boat::v1::Frame f1;
  f1.set_bus_type(boat::v1::Frame::CAN);
  f1.set_timestamp_ns(100 * 1'000'000ULL);
  f1.set_payload("A", 1);
  f1.mutable_can()->set_can_id(0x100);
  f1.mutable_can()->set_dlc(1);
  f1.mutable_can()->set_channel(1);

  boat::v1::Frame f2;
  f2.set_bus_type(boat::v1::Frame::CAN);
  f2.set_timestamp_ns(110 * 1'000'000ULL);
  f2.set_payload("B", 1);
  f2.mutable_can()->set_can_id(0x200);
  f2.mutable_can()->set_dlc(1);
  f2.mutable_can()->set_channel(2);

  std::vector<std::uint8_t> trace_data;
  for (const auto* f : {&f1, &f2}) {
    std::string raw = f->SerializeAsString();
    std::uint32_t len = static_cast<std::uint32_t>(raw.size());
    trace_data.insert(trace_data.end(), reinterpret_cast<const std::uint8_t*>(&len),
                       reinterpret_cast<const std::uint8_t*>(&len) + sizeof(len));
    trace_data.insert(trace_data.end(), raw.begin(), raw.end());
  }
  trace_store.traces["multi_channel"] = trace_data;

  std::vector<std::string> ifaces_seen;
  std::mutex mtx;
  controller.SetEventForwarder([&](const boat::core::Frame& frame) {
    std::lock_guard<std::mutex> lock(mtx);
    ifaces_seen.push_back(frame.iface());
  });

  ReplayConfig config;
  config.trace_id = "multi_channel";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;
  config.buses = {"can0", "can1"};

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  std::lock_guard<std::mutex> lock(mtx);
  REQUIRE(ifaces_seen.size() == 2);
  REQUIRE(ifaces_seen[0] == "can0");
  REQUIRE(ifaces_seen[1] == "can1");
}

TEST_CASE("ReplayController falls back to vcan0 when no buses are configured",
          "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  boat::v1::Frame f1;
  f1.set_bus_type(boat::v1::Frame::CAN);
  f1.set_timestamp_ns(100 * 1'000'000ULL);
  f1.set_payload("A", 1);
  f1.mutable_can()->set_can_id(0x100);
  f1.mutable_can()->set_dlc(1);
  f1.mutable_can()->set_channel(3);  // arbitrary channel; no buses configured

  std::string raw = f1.SerializeAsString();
  std::uint32_t len = static_cast<std::uint32_t>(raw.size());
  std::vector<std::uint8_t> trace_data(sizeof(len) + raw.size());
  std::memcpy(trace_data.data(), &len, sizeof(len));
  std::memcpy(trace_data.data() + sizeof(len), raw.data(), raw.size());
  trace_store.traces["no_buses"] = trace_data;

  std::string seen_iface;
  std::mutex mtx;
  controller.SetEventForwarder([&](const boat::core::Frame& frame) {
    std::lock_guard<std::mutex> lock(mtx);
    seen_iface = frame.iface();
  });

  ReplayConfig config;
  config.trace_id = "no_buses";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;
  // config.buses left empty (default).

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  std::lock_guard<std::mutex> lock(mtx);
  REQUIRE(seen_iface == "vcan0");
}

TEST_CASE("ReplayController overrides Ethernet iface and MAC via ReplayConfig",
          "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  boat::v1::Frame f1;
  f1.set_bus_type(boat::v1::Frame::ETHERNET);
  f1.set_timestamp_ns(100 * 1'000'000ULL);
  f1.set_payload("hello", 5);
  auto* em = f1.mutable_eth();
  em->set_ethertype(0x0800);
  em->set_ip_version(4);
  const std::uint8_t dst_ip_bytes[4] = {192, 168, 0, 100};
  const std::uint8_t src_ip_bytes[4] = {192, 168, 0, 1};
  em->set_dst_ip(dst_ip_bytes, 4);
  em->set_src_ip(src_ip_bytes, 4);
  const std::uint8_t zero_mac[6] = {0, 0, 0, 0, 0, 0};
  em->set_dst_mac(zero_mac, 6);
  em->set_src_mac(zero_mac, 6);

  std::string raw = f1.SerializeAsString();
  std::uint32_t len = static_cast<std::uint32_t>(raw.size());
  std::vector<std::uint8_t> trace_data(sizeof(len) + raw.size());
  std::memcpy(trace_data.data(), &len, sizeof(len));
  std::memcpy(trace_data.data() + sizeof(len), raw.data(), raw.size());
  trace_store.traces["eth_test"] = trace_data;

  std::string captured_iface;
  std::array<std::uint8_t, 6> captured_dst_mac{};
  std::array<std::uint8_t, 6> captured_src_mac{};
  bool got = false;
  std::mutex mtx;
  controller.SetEventForwarder([&](const boat::core::Frame& frame) {
    std::lock_guard<std::mutex> lock(mtx);
    captured_iface = frame.iface();
    std::memcpy(captured_dst_mac.data(), frame.eth_meta().dst_mac, 6);
    std::memcpy(captured_src_mac.data(), frame.eth_meta().src_mac, 6);
    got = true;
  });

  ReplayConfig config;
  config.trace_id = "eth_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;
  config.eth_iface = "veth99";
  config.mac_map["192.168.0.100"] = "02:de:ad:be:ef:01";
  config.mac_map["192.168.0.1"] = "02:de:ad:be:ef:02";

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  std::lock_guard<std::mutex> lock(mtx);
  REQUIRE(got);
  REQUIRE(captured_iface == "veth99");
  REQUIRE(captured_dst_mac[0] == 0x02);
  REQUIRE(captured_dst_mac[5] == 0x01);
  REQUIRE(captured_src_mac[5] == 0x02);
}
