#pragma once

#include <cstddef>
#include <string>

namespace boat::ipc {

enum class IpcChannel {
  kUds,
  kSharedMemory,
};

class IpcChannelSelector {
 public:
  static constexpr std::size_t kShmThresholdBytes = 4 * 1024;

  [[nodiscard]] static IpcChannel SelectChannel(std::size_t payload_size_bytes);
  [[nodiscard]] static std::string TopicName(const std::string& scenario_id, const std::string& signal_name);
  [[nodiscard]] static std::string ResolveTopicName(const std::string& raw_topic_name);
  [[nodiscard]] static std::string ResolveSocketPath(const std::string& instance_or_socket);
  // Stable identifier derived from a bound UDS socket path for SHM topic scoping (one topic per server instance).
  [[nodiscard]] static std::string ShmInstanceIdFromSocketPath(const std::string& resolved_socket_path);
};

}  // namespace boat::ipc
