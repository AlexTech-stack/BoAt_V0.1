#include <catch2/catch_test_macros.hpp>

#include <cstdio>
#include <cstdlib>

#include "virtual/virtual_can_driver.h"

TEST_CASE("Virtual CAN HIL smoke flow", "[hil]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  const char* iface_env = std::getenv("BOAT_VCAN_IFACE");
  const char* iface = (iface_env != nullptr && *iface_env != '\0') ? iface_env : "vcan0";
  boat::hil::VirtualCanDriver tx_driver(iface);
  boat::hil::VirtualCanDriver rx_driver(iface);

  REQUIRE(tx_driver.Open());
  REQUIRE(rx_driver.Open());

  boat::hil::CanFrame frame {};
  frame.can_id = 0x123;
  frame.dlc = 4;
  frame.data[0] = 0xDE;
  frame.data[1] = 0xAD;
  frame.data[2] = 0xBE;
  frame.data[3] = 0xEF;
  frame.timestamp_ns = 0;
  REQUIRE(tx_driver.WriteFrame(frame));

  boat::hil::CanFrame received {};
  REQUIRE(rx_driver.ReadFrame(received));
  REQUIRE(received.can_id == 0x123);

  tx_driver.Close();
  rx_driver.Close();
  SUCCEED("HIL smoke: PASS");
}
