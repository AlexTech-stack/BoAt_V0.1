#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstring>
#include <vector>

#include "tcp_plugin.h"

using namespace boat::tcp;

TEST_CASE("ParseMssOption - MSS option present", "[unit][tcp]") {
  // MSS option: kind=2, len=4, value=1400
  uint8_t options[] = {0x02, 0x04, 0x05, 0x78};  // 0x0578 = 1400
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 1400);
}

TEST_CASE("ParseMssOption - no MSS option returns default 536", "[unit][tcp]") {
  // End-of-options immediately
  uint8_t options[] = {0x00};
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 536);
}

TEST_CASE("ParseMssOption - empty options returns default 536", "[unit][tcp]") {
  uint8_t options[] = {};
  uint16_t mss = ParseMssOption(options, 0);
  REQUIRE(mss == 536);
}

TEST_CASE("ParseMssOption - NOP followed by MSS", "[unit][tcp]") {
  // NOP(1), MSS(2,len=4,val=800)
  uint8_t options[] = {0x01, 0x02, 0x04, 0x03, 0x20};  // 0x0320 = 800
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 800);
}

TEST_CASE("ParseMssOption - multiple options, MSS extracted", "[unit][tcp]") {
  // NOP(1), MSS(2,len=4,val=1200), NOP(1), unknown(8,len=10,data)
  uint8_t options[] = {
    0x01,
    0x02, 0x04, 0x04, 0xB0,  // 0x04B0 = 1200
    0x01,
    0x08, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00  // timestamps dummy
  };
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 1200);
}
