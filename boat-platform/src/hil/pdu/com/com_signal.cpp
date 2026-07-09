#include "pdu/com/com_signal.h"

#include <algorithm>
#include <cstring>
#include <stdexcept>

namespace boat::hil::com {

// ── Intel (little-endian) bit packing ────────────────────────────────────────
//
// Intel layout: the LSB of the signal is placed at start_bit.  Bits are
// numbered 0 (LSB of byte 0) ascending.  Multi-byte signals are stored in
// little-endian byte order within the CAN/Ethernet frame.
//
// Example (DBC convention): start_bit=12, length=16 → occupies bits 12-27,
// byte 1 bits 4-7, byte 2 bits 0-7, byte 3 bits 0-3.

void PackIntel(std::vector<uint8_t>& buffer, uint32_t start_bit,
               uint32_t bit_length, uint64_t raw_value) {
  for (uint32_t i = 0; i < bit_length; ++i) {
    const uint32_t dst_bit = start_bit + i;
    const uint32_t byte_idx = dst_bit / 8;
    const uint32_t bit_idx  = dst_bit % 8;
    if (byte_idx >= buffer.size()) buffer.resize(byte_idx + 1);
    const uint64_t src_bit_val = (raw_value >> i) & 1ULL;
    if (src_bit_val) {
      buffer[byte_idx] |= static_cast<uint8_t>(1 << bit_idx);
    } else {
      buffer[byte_idx] &= ~static_cast<uint8_t>(1 << bit_idx);
    }
  }
}

uint64_t UnpackIntel(const uint8_t* data, uint32_t start_bit,
                     uint32_t bit_length) {
  uint64_t result = 0;
  for (uint32_t i = 0; i < bit_length; ++i) {
    const uint32_t src_bit = start_bit + i;
    const uint32_t byte_idx = src_bit / 8;
    const uint32_t bit_idx  = src_bit % 8;
    if (data[byte_idx] & (1 << bit_idx)) {
      result |= (1ULL << i);
    }
  }
  return result;
}

// ── Motorola (big-endian) bit packing ────────────────────────────────────────
//
// Motorola layout: the MSB of the signal is placed at start_bit.  Multi-byte
// signals are stored in big-endian byte order.  The start_bit is the bit
// position of the MSB of the signal.
//
// Example (DBC convention): start_bit=12, length=16 → occupies bits 12-27,
// byte 1 bits 4-7 (MSB), byte 0 bits 0-7, byte 3 bits 0-3 (overflow).

void PackMotorola(std::vector<uint8_t>& buffer, uint32_t start_bit,
                  uint32_t bit_length, uint64_t raw_value) {
  for (uint32_t i = 0; i < bit_length; ++i) {
    // For Motorola, bit position within the signal counts from MSB.
    const uint32_t sig_bit_from_lsb = bit_length - 1 - i;
    const uint32_t dst_bit = start_bit - sig_bit_from_lsb;
    const uint32_t byte_idx = dst_bit / 8;
    const uint32_t bit_idx  = dst_bit % 8;
    if (byte_idx >= buffer.size()) buffer.resize(byte_idx + 1);
    const uint64_t src_bit_val = (raw_value >> i) & 1ULL;
    if (src_bit_val) {
      buffer[byte_idx] |= static_cast<uint8_t>(1 << bit_idx);
    } else {
      buffer[byte_idx] &= ~static_cast<uint8_t>(1 << bit_idx);
    }
  }
}

uint64_t UnpackMotorola(const uint8_t* data, uint32_t start_bit,
                        uint32_t bit_length) {
  uint64_t result = 0;
  for (uint32_t i = 0; i < bit_length; ++i) {
    const uint32_t sig_bit_from_lsb = bit_length - 1 - i;
    const uint32_t src_bit = start_bit - sig_bit_from_lsb;
    const uint32_t byte_idx = src_bit / 8;
    const uint32_t bit_idx  = src_bit % 8;
    if (data[byte_idx] & (1 << bit_idx)) {
      result |= (1ULL << i);
    }
  }
  return result;
}

// ── Signal-level pack (mux-aware) ─────────────────────────────────────────────
//
// If a multiplexor signal (is_muxor=true) exists, only signals whose
// mux_value matches the muxor's current value are packed, plus static
// signals (mux_value = nullopt) and the muxor itself.

std::vector<uint8_t> PackSignals(const MessageDef& msg,
                                 const std::map<std::string, double>& values) {
  std::vector<uint8_t> buffer(msg.length_bytes, 0);

  // Find the multiplexor signal (if any).
  const SignalDef* muxor = nullptr;
  for (const auto& sig : msg.signals) {
    if (sig.is_muxor) {
      muxor = &sig;
      break;
    }
  }

  // Determine the active mux value.
  int active_mux = 0;
  bool has_muxor = false;
  if (muxor) {
    has_muxor = true;
    auto it = values.find(muxor->name);
    double mux_physical = (it != values.end()) ? it->second : muxor->init_value;
    active_mux = static_cast<int>(std::round(mux_physical));
  }

  for (const auto& sig : msg.signals) {
    // Skip signals belonging to a non-active mux group.
    if (sig.mux_value.has_value() && sig.mux_value.value() != active_mux) {
      continue;
    }
    // The muxor itself is always packed (already included above).

    auto it = values.find(sig.name);
    double physical = (it != values.end()) ? it->second : sig.init_value;
    int64_t raw = PhysicalToRaw(physical, sig.factor, sig.offset);

    // Sign-extend for signed types
    if (sig.value_type == "Signed") {
      uint64_t mask = (1ULL << sig.bit_length) - 1;
      int64_t sign_bit = 1ULL << (sig.bit_length - 1);
      int64_t extended = raw & mask;
      if (extended & sign_bit) extended |= ~mask;
      raw = extended;
    }

    uint64_t raw_u = static_cast<uint64_t>(static_cast<int64_t>(raw));

    if (sig.is_motorola) {
      PackMotorola(buffer, sig.start_pos, sig.bit_length, raw_u);
    } else {
      PackIntel(buffer, sig.start_pos, sig.bit_length, raw_u);
    }
  }

  return buffer;
}

// ── Signal-level unpack (mux-aware) ───────────────────────────────────────────
//
// First pass: decode all static signals (no mux_value), including the muxor.
// If a multiplexor is active, read its value and decode the matching
// dynamic group in a second pass.

std::map<std::string, double> UnpackSignals(const MessageDef& msg,
                                             const uint8_t* data,
                                             uint32_t len) {
  std::map<std::string, double> result;
  const uint32_t actual_len = std::min(len, msg.length_bytes);

  // First pass: static signals + the muxor.
  int active_mux = 0;
  bool has_muxor = false;

  for (const auto& sig : msg.signals) {
    if (sig.mux_value.has_value()) {
      continue;  // skip dynamic signals for now
    }

    uint64_t raw_u;
    if (sig.is_motorola) {
      raw_u = UnpackMotorola(data, sig.start_pos, sig.bit_length);
    } else {
      raw_u = UnpackIntel(data, sig.start_pos, sig.bit_length);
    }

    int64_t raw_s = static_cast<int64_t>(raw_u);
    if (sig.value_type == "Signed") {
      uint64_t mask = (1ULL << sig.bit_length) - 1;
      int64_t sign_bit = 1ULL << (sig.bit_length - 1);
      raw_s = raw_u & mask;
      if (raw_s & sign_bit) raw_s |= ~mask;
    }

    double physical = RawToPhysical(raw_s, sig.factor, sig.offset);
    result[sig.name] = physical;

    if (sig.is_muxor) {
      has_muxor = true;
      active_mux = static_cast<int>(std::round(physical));
    }
  }

  // Second pass: dynamic signals matching the active mux group.
  if (has_muxor) {
    for (const auto& sig : msg.signals) {
      if (!sig.mux_value.has_value() || sig.mux_value.value() != active_mux) {
        continue;
      }

      uint64_t raw_u;
      if (sig.is_motorola) {
        raw_u = UnpackMotorola(data, sig.start_pos, sig.bit_length);
      } else {
        raw_u = UnpackIntel(data, sig.start_pos, sig.bit_length);
      }

      int64_t raw_s = static_cast<int64_t>(raw_u);
      if (sig.value_type == "Signed") {
        uint64_t mask = (1ULL << sig.bit_length) - 1;
        int64_t sign_bit = 1ULL << (sig.bit_length - 1);
        raw_s = raw_u & mask;
        if (raw_s & sign_bit) raw_s |= ~mask;
      }

      double physical = RawToPhysical(raw_s, sig.factor, sig.offset);
      result[sig.name] = physical;
    }
  }

  return result;
}

// ── E2E CRC profiles ─────────────────────────────────────────────────────────
//
// Simplified AUTOSAR E2E CRC implementations for profiles 2, 4, 5, 7.

uint8_t E2eCrc8(const uint8_t* data, uint32_t len) {
  uint8_t crc = 0xFF;
  for (uint32_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int j = 0; j < 8; ++j) {
      if (crc & 0x80) {
        crc = static_cast<uint8_t>((crc << 1) ^ 0x1D);
      } else {
        crc = static_cast<uint8_t>(crc << 1);
      }
    }
  }
  return crc ^ 0xFF;
}

uint16_t E2eCrc16(const uint8_t* data, uint32_t len) {
  uint16_t crc = 0xFFFF;
  for (uint32_t i = 0; i < len; ++i) {
    crc ^= static_cast<uint16_t>(data[i] << 8);
    for (int j = 0; j < 8; ++j) {
      if (crc & 0x8000) {
        crc = static_cast<uint16_t>((crc << 1) ^ 0x1021);
      } else {
        crc = static_cast<uint16_t>(crc << 1);
      }
    }
  }
  return crc;
}

uint32_t E2eCrc32(const uint8_t* data, uint32_t len) {
  uint32_t crc = 0xFFFFFFFF;
  for (uint32_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int j = 0; j < 8; ++j) {
      if (crc & 1) {
        crc = (crc >> 1) ^ 0xEDB88320;
      } else {
        crc >>= 1;
      }
    }
  }
  return ~crc;
}

}  // namespace boat::hil::com
