#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace boat::ipc {

// Fixed-layout sample for iceoryx2 publish_subscribe of arbitrary control payloads (<= kCapacity).
struct ShmPayloadSample {
  static constexpr std::size_t kCapacity = 512 * 1024;

  std::uint64_t payload_token{0};
  std::uint32_t size_bytes{0};
  std::array<std::byte, kCapacity> bytes{};

  [[nodiscard]] bool SetFromString(const std::string& payload) {
    if (payload.size() > kCapacity) {
      return false;
    }
    size_bytes = static_cast<std::uint32_t>(payload.size());
    if (size_bytes > 0) {
      std::memcpy(bytes.data(), payload.data(), payload.size());
    }
    return true;
  }

  [[nodiscard]] std::string ToString() const {
    return std::string(reinterpret_cast<const char*>(bytes.data()), static_cast<std::size_t>(size_bytes));
  }
};

}  // namespace boat::ipc
