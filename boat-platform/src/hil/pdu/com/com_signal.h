#pragma once

#include <cmath>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace boat::hil::com {

/* Signal definition — mirrors the JSON PDU DB schema. */
struct SignalDef {
  uint32_t    id{0};
  std::string name;
  uint32_t    bit_length{1};
  uint32_t    start_pos{0};     // LSB position, 0-indexed
  bool        is_motorola{false}; // false = Intel (little-endian)
  std::string value_type{"Unsigned"}; // Unsigned, Signed, Float, Bool
  double      factor{1.0};
  double      offset{0.0};
  double      init_value{0.0};
  double      min_val{0.0};
  double      max_val{0.0};
  std::string unit;

  // Multiplexing support
  bool               is_muxor{false};
  std::optional<int> mux_value;

  // Metadata
  std::string comment;
};

struct MessageDef {
  uint32_t                    db_id{0};
  std::string                 name;
  uint32_t                    length_bytes{8};
  std::vector<SignalDef>      signals;

  // Metadata
  std::string comment;
  std::string node;
};

// ── Bit-level pack/unpack (Intel / Motorola) ─────────────────────────────────

void PackIntel(std::vector<uint8_t>& buffer, uint32_t start_bit,
               uint32_t bit_length, uint64_t raw_value);

uint64_t UnpackIntel(const uint8_t* data, uint32_t start_bit,
                     uint32_t bit_length);

void PackMotorola(std::vector<uint8_t>& buffer, uint32_t start_bit,
                  uint32_t bit_length, uint64_t raw_value);

uint64_t UnpackMotorola(const uint8_t* data, uint32_t start_bit,
                        uint32_t bit_length);

// ── Physical / raw conversion ───────────────────────────────────────────────

inline int64_t PhysicalToRaw(double physical, double factor, double offset) {
  return static_cast<int64_t>(std::round((physical - offset) / factor));
}

inline double RawToPhysical(int64_t raw, double factor, double offset) {
  return static_cast<double>(raw) * factor + offset;
}

// ── Signal-level pack/unpack ────────────────────────────────────────────────

std::vector<uint8_t> PackSignals(const MessageDef& msg,
                                 const std::map<std::string, double>& values);

std::map<std::string, double> UnpackSignals(const MessageDef& msg,
                                             const uint8_t* data,
                                             uint32_t len);

// ── AUTOSAR E2E CRC profiles ───────────────────────────────────────────────

uint8_t  E2eCrc8(const uint8_t* data, uint32_t len);
uint16_t E2eCrc16(const uint8_t* data, uint32_t len);
uint32_t E2eCrc32(const uint8_t* data, uint32_t len);

}  // namespace boat::hil::com
