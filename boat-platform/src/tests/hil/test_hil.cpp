#include <catch2/catch_test_macros.hpp>

#include <cstdio>
#include <cstdlib>
#include <cstring>

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

TEST_CASE("Virtual CAN HIL: CAN FD payload over 8 bytes is rounded to a valid ISO 11898-1 length", "[hil]") {
  // A raw 9-byte payload is not a valid CAN FD wire length -- valid lengths
  // jump straight from 8 to 12. Sending dlc=9 verbatim would put an
  // unrepresentable length nibble on the wire; it must come back out at the
  // next valid length (12) with the extra bytes zero-padded, not as a
  // literal 9.
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
  frame.can_id = 0x200;
  frame.flags = boat::hil::kCanFdFlagFdf;
  frame.dlc = 9;
  const std::uint8_t payload[9] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x11, 0x22};
  std::memcpy(frame.data, payload, sizeof(payload));
  frame.timestamp_ns = 0;
  REQUIRE(tx_driver.WriteFrame(frame));

  boat::hil::CanFrame received {};
  REQUIRE(rx_driver.ReadFrame(received));
  REQUIRE(received.can_id == 0x200);
  REQUIRE(received.dlc == 12);  // next valid ISO 11898-1 CAN FD length above 9
  REQUIRE(std::memcmp(received.data, payload, sizeof(payload)) == 0);
  REQUIRE(received.data[9] == 0);
  REQUIRE(received.data[10] == 0);
  REQUIRE(received.data[11] == 0);

  tx_driver.Close();
  rx_driver.Close();
  SUCCEED("HIL CAN FD DLC rounding: PASS");
}
