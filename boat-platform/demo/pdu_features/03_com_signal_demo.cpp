// Example: COM Signal Library Usage
// Build: g++ -std=c++20 -I src/hil 03_com_signal_demo.cpp src/hil/pdu/com/com_signal.cpp -o com_demo
// Run:   ./com_demo

#include <cstdio>
#include <iostream>
#include "pdu/com/com_signal.h"

using namespace boat::hil::com;

int main() {
  // ── Define a CAN message with two signals ────────────────────────────────
  SignalDef speed;
  speed.name        = "MotorSpeed";
  speed.bit_length  = 16;
  speed.start_pos   = 0;
  speed.is_motorola = false;  // Intel (little-endian)
  speed.factor      = 0.5;
  speed.offset      = 0.0;
  speed.value_type  = "Unsigned";
  speed.unit        = "rpm";

  SignalDef temp;
  temp.name         = "CoolantTemp";
  temp.bit_length   = 8;
  temp.start_pos    = 16;     // immediately after MotorSpeed
  temp.is_motorola  = false;
  temp.factor       = 1.0;
  temp.offset       = -40.0;  // physical = raw - 40
  temp.value_type   = "Unsigned";
  temp.unit         = "degC";

  MessageDef msg;
  msg.name         = "EngineData";
  msg.length_bytes = 4;
  msg.signals      = {speed, temp};

  // ── Pack signals → raw bytes ─────────────────────────────────────────────
  auto packed = PackSignals(msg, {
    {"MotorSpeed", 3000.0},    // raw = 3000/0.5 = 6000 = 0x1770 (Intel)
    {"CoolantTemp", 85.0},     // raw = 85+40 = 125 = 0x7D
  });

  std::printf("Packed %zu bytes:\n", packed.size());
  for (auto b : packed) std::printf(" %02X", b);
  std::printf("\n");

  // ── Unpack bytes → physical values ──────────────────────────────────────
  auto unpacked = UnpackSignals(msg, packed.data(), packed.size());
  std::printf("Unpacked:\n");
  std::printf("  MotorSpeed  = %.1f rpm\n", unpacked["MotorSpeed"]);
  std::printf("  CoolantTemp = %.1f degC\n", unpacked["CoolantTemp"]);

  // ── E2E CRC ──────────────────────────────────────────────────────────────
  uint8_t crc8 = E2eCrc8(packed.data(), packed.size());
  uint16_t crc16 = E2eCrc16(packed.data(), packed.size());
  std::printf("CRC-8 = 0x%02X, CRC-16 = 0x%04X\n", crc8, crc16);

  // ── Manual bit packing (Intel) ──────────────────────────────────────────
  std::vector<uint8_t> buf(4, 0);
  PackIntel(buf, 0, 16, 0x1770);     // MotorSpeed
  PackIntel(buf, 16, 8, 125);         // CoolantTemp
  std::printf("Manually packed:\n");
  for (auto b : buf) std::printf(" %02X", b);
  std::printf("\n");

  return 0;
}
