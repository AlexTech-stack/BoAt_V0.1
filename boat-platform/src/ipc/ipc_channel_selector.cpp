#include "ipc/ipc_channel_selector.h"

namespace boat::ipc {
namespace {

std::string SanitizeId(const std::string& value) {
  std::string out;
  out.reserve(value.size());
  for (const char ch : value) {
    const bool is_alpha_num = (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9');
    if (is_alpha_num || ch == '-' || ch == '_') {
      out.push_back(ch);
      continue;
    }
    out.push_back('_');
  }
  return out;
}

}  // namespace

IpcChannel IpcChannelSelector::SelectChannel(std::size_t payload_size_bytes) {
  return payload_size_bytes >= kShmThresholdBytes ? IpcChannel::kSharedMemory : IpcChannel::kUds;
}

std::string IpcChannelSelector::TopicName(const std::string& scenario_id, const std::string& signal_name) {
  return "boat/" + SanitizeId(scenario_id) + "/" + SanitizeId(signal_name);
}

std::string IpcChannelSelector::ResolveTopicName(const std::string& raw_topic_name) {
  if (raw_topic_name.rfind("boat/", 0) == 0) {
    return raw_topic_name;
  }
  return "boat/" + SanitizeId(raw_topic_name);
}

std::string IpcChannelSelector::ResolveSocketPath(const std::string& instance_or_socket) {
  if (instance_or_socket.rfind("/run/boat/", 0) == 0 && instance_or_socket.size() > std::string("/run/boat/").size()) {
    return instance_or_socket;
  }
  if (!instance_or_socket.empty() && instance_or_socket.front() == '/') {
    return instance_or_socket;
  }
  const std::string instance_id = instance_or_socket.empty() ? "default" : SanitizeId(instance_or_socket);
  return "/run/boat/" + instance_id + ".sock";
}

std::string IpcChannelSelector::ShmInstanceIdFromSocketPath(const std::string& resolved_socket_path) {
  const auto slash = resolved_socket_path.find_last_of("/\\");
  std::string base =
      (slash == std::string::npos) ? resolved_socket_path : resolved_socket_path.substr(slash + 1);
  constexpr const char kSuffix[] = ".sock";
  if (base.size() >= sizeof(kSuffix) - 1 &&
      base.compare(base.size() - (sizeof(kSuffix) - 1), sizeof(kSuffix) - 1, kSuffix) == 0) {
    base = base.substr(0, base.size() - (sizeof(kSuffix) - 1));
  }
  if (base.empty()) {
    return "default";
  }
  return SanitizeId(base);
}

}  // namespace boat::ipc
