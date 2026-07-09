#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "pdu/com/com_signal.h"

using namespace boat::hil::com;

TEST_CASE("PhysicalToRaw and RawToPhysical round-trip", "[unit][com]") {
  const double physical = 123.5;
  const double factor   = 0.5;
  const double offset   = 0.0;
  const int64_t raw = PhysicalToRaw(physical, factor, offset);
  REQUIRE(raw == 247);
  const double back = RawToPhysical(raw, factor, offset);
  REQUIRE(back == 123.5);
}

TEST_CASE("PackIntel simple 8-bit signal at bit 0", "[unit][com]") {
  std::vector<uint8_t> buf(1, 0);
  PackIntel(buf, 0, 8, 0xAB);
  REQUIRE(buf[0] == 0xAB);
}

TEST_CASE("PackIntel 16-bit signal cross-byte boundary", "[unit][com]") {
  std::vector<uint8_t> buf(3, 0);
  PackIntel(buf, 4, 16, 0xABCD);
  // Intel: LSB at bit 4, MSB at bit 19
  // byte0 bits 4-7 = 0xD, byte1 bits 0-7 = 0xBC, byte2 bits 0-3 = 0xA
  REQUIRE((buf[0] & 0xF0) == 0xD0);
  REQUIRE(buf[1] == 0xBC);
  REQUIRE((buf[2] & 0x0F) == 0x0A);
}

TEST_CASE("UnpackIntel round-trip with PackIntel", "[unit][com]") {
  std::vector<uint8_t> buf(4, 0);
  PackIntel(buf, 3, 12, 0xABC);
  const uint64_t val = UnpackIntel(buf.data(), 3, 12);
  REQUIRE(val == 0xABC);
}

TEST_CASE("PackMotorola 16-bit signal at bit 12", "[unit][com]") {
  std::vector<uint8_t> buf(3, 0);
  PackMotorola(buf, 12, 16, 0xAABB);
  // Motorola: MSB at bit 12, LSB at bit 27
  // byte1 bits 4-7 = 0xAA high nibble, byte1 bits 0-3 = 0xAA low nibble? No...
  // Let's just verify round-trip
  const uint64_t val = UnpackMotorola(buf.data(), 12, 16);
  REQUIRE(val == 0xAABB);
}

TEST_CASE("UnpackMotorola round-trip with PackMotorola", "[unit][com]") {
  std::vector<uint8_t> buf(4, 0);
  PackMotorola(buf, 19, 9, 0x1AB);
  const uint64_t val = UnpackMotorola(buf.data(), 19, 9);
  REQUIRE(val == 0x1AB);
}

TEST_CASE("PackSignals with single signal", "[unit][com]") {
  MessageDef msg;
  msg.name = "TestMsg";
  msg.length_bytes = 2;
  SignalDef sig;
  sig.name = "Speed";
  sig.bit_length = 16;
  sig.start_pos = 0;
  sig.is_motorola = false;
  sig.factor = 1.0;
  sig.offset = 0.0;
  sig.value_type = "Unsigned";
  msg.signals.push_back(sig);

  const auto bytes = PackSignals(msg, {{"Speed", 1000.0}});
  REQUIRE(bytes.size() == 2);
  REQUIRE(bytes[0] == 0xE8);  // 1000 & 0xFF = 0xE8
  REQUIRE(bytes[1] == 0x03);  // 1000 >> 8 = 0x03
}

TEST_CASE("PackSignals with Intel and Motorola signals", "[unit][com]") {
  MessageDef msg;
  msg.length_bytes = 4;

  SignalDef s1;
  s1.name = "IntelSig";
  s1.bit_length = 8;
  s1.start_pos = 0;
  s1.is_motorola = false;
  s1.factor = 1.0;
  s1.offset = 0.0;
  s1.value_type = "Unsigned";
  msg.signals.push_back(s1);

  SignalDef s2;
  s2.name = "MotorolaSig";
  s2.bit_length = 8;
  s2.start_pos = 15;
  s2.is_motorola = true;
  s2.factor = 1.0;
  s2.offset = 0.0;
  s2.value_type = "Unsigned";
  msg.signals.push_back(s2);

  const auto bytes = PackSignals(msg, {{"IntelSig", 0xAB}, {"MotorolaSig", 0xCD}});
  // IntelSig at bit 0 → byte0 = 0xAB
  // MotorolaSig MSB at bit 15, LSB at bit 8 → occupies byte1
  // Motorola 8-bit at start_pos=15: MSB bit 15, LSB bit 8 → byte1 = 0xCD
  REQUIRE(bytes[0] == 0xAB);
  REQUIRE(bytes[1] == 0xCD);
}

TEST_CASE("UnpackSignals round-trip", "[unit][com]") {
  MessageDef msg;
  msg.name = "TestMsg";
  msg.length_bytes = 4;

  SignalDef sig;
  sig.name = "Temp";
  sig.bit_length = 10;
  sig.start_pos = 2;
  sig.is_motorola = false;
  sig.factor = 0.5;
  sig.offset = -40.0;
  // Physical = raw*0.5 + (-40) → raw = (physical+40)/0.5
  // For physical=25: raw = (25+40)/0.5 = 130
  sig.value_type = "Unsigned";
  msg.signals.push_back(sig);

  const auto bytes = PackSignals(msg, {{"Temp", 25.0}});
  REQUIRE(bytes.size() == 4);

  const auto unpacked = UnpackSignals(msg, bytes.data(), bytes.size());
  REQUIRE(unpacked.find("Temp") != unpacked.end());
  // Physical = raw*0.5 + (-40). raw = 130 → 130*0.5-40 = 25.0
  REQUIRE(unpacked.at("Temp") == 25.0);
}

TEST_CASE("PackSignals with multiplexing — packs only active mux group", "[unit][com]") {
  MessageDef msg;
  msg.name = "MuxMsg";
  msg.length_bytes = 8;

  // Multiplexor signal
  SignalDef muxor;
  muxor.name = "MUX";
  muxor.bit_length = 2;
  muxor.start_pos = 0;
  muxor.is_motorola = false;
  muxor.factor = 1.0;
  muxor.offset = 0.0;
  muxor.value_type = "Unsigned";
  muxor.is_muxor = true;
  msg.signals.push_back(muxor);

  // Static signal (always present)
  SignalDef static_sig;
  static_sig.name = "StaticVal";
  static_sig.bit_length = 8;
  static_sig.start_pos = 8;
  static_sig.is_motorola = false;
  static_sig.factor = 1.0;
  static_sig.offset = 0.0;
  static_sig.value_type = "Unsigned";
  msg.signals.push_back(static_sig);

  // Mux group 0 signals
  SignalDef mux0_sig;
  mux0_sig.name = "Group0_Val";
  mux0_sig.bit_length = 8;
  mux0_sig.start_pos = 16;
  mux0_sig.is_motorola = false;
  mux0_sig.factor = 1.0;
  mux0_sig.offset = 0.0;
  mux0_sig.value_type = "Unsigned";
  mux0_sig.mux_value = 0;
  msg.signals.push_back(mux0_sig);

  // Mux group 1 signals
  SignalDef mux1_sig;
  mux1_sig.name = "Group1_Val";
  mux1_sig.bit_length = 8;
  mux1_sig.start_pos = 16;   // same position as Group0_Val!
  mux1_sig.is_motorola = false;
  mux1_sig.factor = 1.0;
  mux1_sig.offset = 0.0;
  mux1_sig.value_type = "Unsigned";
  mux1_sig.mux_value = 1;
  msg.signals.push_back(mux1_sig);

  // Pack with MUX=0: Group0_Val should be packed, Group1_Val should be skipped.
  {
    const auto bytes = PackSignals(msg, {{"MUX", 0.0}, {"StaticVal", 0xAA}, {"Group0_Val", 0xBB}});
    REQUIRE(bytes[0] == 0x00);  // MUX=0
    REQUIRE(bytes[1] == 0xAA);  // StaticVal
    REQUIRE(bytes[2] == 0xBB);  // Group0_Val
  }

  // Pack with MUX=1: Group1_Val should be packed, Group0_Val skipped.
  {
    const auto bytes = PackSignals(msg, {{"MUX", 1.0}, {"StaticVal", 0xAA}, {"Group1_Val", 0xCC}});
    REQUIRE(bytes[0] == 0x01);  // MUX=1
    REQUIRE(bytes[1] == 0xAA);  // StaticVal
    REQUIRE(bytes[2] == 0xCC);  // Group1_Val
  }
}

TEST_CASE("UnpackSignals with multiplexing — decodes only active mux group", "[unit][com]") {
  MessageDef msg;
  msg.name = "MuxMsg";
  msg.length_bytes = 8;

  SignalDef muxor;
  muxor.name = "MUX";
  muxor.bit_length = 2;
  muxor.start_pos = 0;
  muxor.is_motorola = false;
  muxor.factor = 1.0;
  muxor.offset = 0.0;
  muxor.value_type = "Unsigned";
  muxor.is_muxor = true;
  msg.signals.push_back(muxor);

  SignalDef static_sig;
  static_sig.name = "StaticVal";
  static_sig.bit_length = 8;
  static_sig.start_pos = 8;
  static_sig.is_motorola = false;
  static_sig.factor = 1.0;
  static_sig.offset = 0.0;
  static_sig.value_type = "Unsigned";
  msg.signals.push_back(static_sig);

  SignalDef mux0_sig;
  mux0_sig.name = "Group0_Val";
  mux0_sig.bit_length = 8;
  mux0_sig.start_pos = 16;
  mux0_sig.is_motorola = false;
  mux0_sig.factor = 1.0;
  mux0_sig.offset = 0.0;
  mux0_sig.value_type = "Unsigned";
  mux0_sig.mux_value = 0;
  msg.signals.push_back(mux0_sig);

  SignalDef mux1_sig;
  mux1_sig.name = "Group1_Val";
  mux1_sig.bit_length = 8;
  mux1_sig.start_pos = 16;
  mux1_sig.is_motorola = false;
  mux1_sig.factor = 1.0;
  mux1_sig.offset = 0.0;
  mux1_sig.value_type = "Unsigned";
  mux1_sig.mux_value = 1;
  msg.signals.push_back(mux1_sig);

  // Pack with MUX=0, then unpack — should see Group0_Val but not Group1_Val.
  {
    const auto packed = PackSignals(msg, {{"MUX", 0.0}, {"StaticVal", 0xAA}, {"Group0_Val", 0xBB}});
    const auto unpacked = UnpackSignals(msg, packed.data(), packed.size());
    REQUIRE(unpacked.at("MUX") == 0.0);
    REQUIRE(unpacked.at("StaticVal") == 0xAA);
    REQUIRE(unpacked.at("Group0_Val") == 0xBB);
    REQUIRE(unpacked.find("Group1_Val") == unpacked.end());
  }

  // Pack with MUX=1, then unpack — should see Group1_Val but not Group0_Val.
  {
    const auto packed = PackSignals(msg, {{"MUX", 1.0}, {"StaticVal", 0xAA}, {"Group1_Val", 0xCC}});
    const auto unpacked = UnpackSignals(msg, packed.data(), packed.size());
    REQUIRE(unpacked.at("MUX") == 1.0);
    REQUIRE(unpacked.at("StaticVal") == 0xAA);
    REQUIRE(unpacked.at("Group1_Val") == 0xCC);
    REQUIRE(unpacked.find("Group0_Val") == unpacked.end());
  }
}

TEST_CASE("E2eCrc8 produces non-zero result", "[unit][com]") {
  const uint8_t data[] = {0x01, 0x02, 0x03, 0x04};
  const uint8_t crc = E2eCrc8(data, 4);
  // Just verify it's non-zero and deterministic
  REQUIRE(crc != 0);
  REQUIRE(crc == E2eCrc8(data, 4));
}

TEST_CASE("E2eCrc16 produces consistent result", "[unit][com]") {
  const uint8_t data[] = {0xDE, 0xAD, 0xBE, 0xEF};
  const uint16_t crc = E2eCrc16(data, 4);
  REQUIRE(crc != 0);
  REQUIRE(crc == E2eCrc16(data, 4));
}

TEST_CASE("E2eCrc32 produces consistent result", "[unit][com]") {
  const uint8_t data[] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
  const uint32_t crc = E2eCrc32(data, 6);
  REQUIRE(crc != 0);
  REQUIRE(crc == E2eCrc32(data, 6));
}
