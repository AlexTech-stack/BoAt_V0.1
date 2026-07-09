#include <catch2/catch_test_macros.hpp>
#include <vector>

#include "boat/test_harness.h"

using namespace boat::test;

TEST_CASE("TestHarness basic CAN send/receive", "[unit][test_harness]") {
  TestHarness harness;
  auto& can1 = harness.AddCanBus("can1");
  auto& can2 = harness.AddCanBus("can2");

  // Send a CAN frame on can1
  can1.Send(0x100, {0x01, 0xF4});

  // The mock driver captures it
  auto& mock_can1 = harness.MockCan("can1");
  REQUIRE(mock_can1.written.size() == 1);
  REQUIRE(mock_can1.written[0].can_id == 0x100);
  REQUIRE(mock_can1.written[0].data[0] == 0x01);
  REQUIRE(mock_can1.written[0].data[1] == 0xF4);
}

TEST_CASE("TestHarness step lifecycle", "[unit][test_harness]") {
  TestHarness harness;
  harness.AddCanBus("can1");
  harness.AddCanBus("can2");

  {
    auto step = harness.Step(1, "Send RPM");

    auto& can1 = harness.CanBus("can1");
    auto& mock_can1 = harness.MockCan("can1");

    can1.Send(0x100, {0x01, 0xF4});
    harness.Advance(std::chrono::milliseconds(100));

    // Verify the frame was written
    step.Assert(mock_can1.written.size() == 1, "frame sent");
    step.Assert(mock_can1.written[0].can_id == 0x100, "correct ID");

    REQUIRE(mock_can1.written.size() == 1);
  }

  // Report should have the step
  harness.Report().Save("test_step_report.json");
}

TEST_CASE("TestHarness advances ticks", "[unit][test_harness]") {
  TestHarness harness;
  REQUIRE(harness.CurrentTick() == 0);

  harness.Advance(std::chrono::milliseconds(50));
  REQUIRE(harness.CurrentTick() >= 5);  // 50ms / 10ms per tick = 5 ticks

  auto tick1 = harness.CurrentTick();
  harness.Advance(std::chrono::milliseconds(100));
  REQUIRE(harness.CurrentTick() > tick1);
}

TEST_CASE("TestHarness Ethernet send", "[unit][test_harness]") {
  TestHarness harness;
  auto& eth = harness.AddEthBus("eth0");

  eth.Send({0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF}, 0x88B5, {0x01, 0x02});

  auto& mock_eth = harness.MockEth("eth0");
  REQUIRE(mock_eth.written.size() == 1);
  REQUIRE(mock_eth.written[0].ethertype == 0x88B5);
}

TEST_CASE("TestHarness multiple CAN buses are isolated", "[unit][test_harness]") {
  TestHarness harness;
  auto& can1 = harness.AddCanBus("engine_can");
  auto& can2 = harness.AddCanBus("body_can");

  can1.Send(0x100, {0x01});
  can2.Send(0x200, {0x02});

  auto& m1 = harness.MockCan("engine_can");
  auto& m2 = harness.MockCan("body_can");

  REQUIRE(m1.written.size() == 1);
  REQUIRE(m1.written[0].can_id == 0x100);
  REQUIRE(m2.written.size() == 1);
  REQUIRE(m2.written[0].can_id == 0x200);
}

TEST_CASE("TestReport JSON output", "[unit][test_harness]") {
  TestReport report;
  report.SetTestId("TC-001")
         .SetTestName("RPM Test")
         .SetVerdict("PASS")
         .SetDurationMs(1234);

  TestReport::StepRecord step;
  step.id = 1;
  step.name = "Send request";
  step.verdict = "PASS";
  step.duration_ms = 100;
  step.assertions.push_back({"frame.id == 0x300", "true", "true", "PASS"});
  report.AddStep(step);

  std::string json = report.ToJson();
  REQUIRE(json.find("TC-001") != std::string::npos);
  REQUIRE(json.find("RPM Test") != std::string::npos);
  REQUIRE(json.find("PASS") != std::string::npos);
  REQUIRE(json.find("frame.id") != std::string::npos);
}
