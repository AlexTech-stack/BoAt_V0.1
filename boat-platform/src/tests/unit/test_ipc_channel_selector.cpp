#include <catch2/catch_test_macros.hpp>

#include "ipc/ipc_channel_selector.h"

TEST_CASE("IPC channel selection respects payload threshold", "[unit][ipc]") {
  using boat::ipc::IpcChannel;
  using boat::ipc::IpcChannelSelector;

  REQUIRE(IpcChannelSelector::SelectChannel(IpcChannelSelector::kShmThresholdBytes - 1) == IpcChannel::kUds);
  REQUIRE(IpcChannelSelector::SelectChannel(IpcChannelSelector::kShmThresholdBytes) == IpcChannel::kSharedMemory);
}

TEST_CASE("IPC naming rules normalize topic and socket paths", "[unit][ipc]") {
  using boat::ipc::IpcChannelSelector;

  REQUIRE(IpcChannelSelector::TopicName("scenario-1", "speed") == "boat/scenario-1/speed");
  REQUIRE(IpcChannelSelector::ResolveTopicName("boat/scenario-1/speed") == "boat/scenario-1/speed");
  REQUIRE(IpcChannelSelector::ResolveTopicName("scenario 1/speed") == "boat/scenario_1_speed");

  REQUIRE(IpcChannelSelector::ResolveSocketPath("sim-1") == "/run/boat/sim-1.sock");
  REQUIRE(IpcChannelSelector::ResolveSocketPath("/run/boat/sim-2.sock") == "/run/boat/sim-2.sock");

  REQUIRE(IpcChannelSelector::ShmInstanceIdFromSocketPath("/run/boat/sim-2.sock") == "sim-2");
  REQUIRE(IpcChannelSelector::ShmInstanceIdFromSocketPath("/tmp/foo_bar.sock") == "foo_bar");
}
