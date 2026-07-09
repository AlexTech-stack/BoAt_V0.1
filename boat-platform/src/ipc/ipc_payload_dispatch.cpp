#include "ipc/ipc_payload_dispatch.h"

#include <limits>
#include <random>

#include "ipc/ipc_channel_selector.h"
#include "ipc/shm/shm_payload_sample.h"
#include "ipc/shm/shm_publisher.h"

namespace boat::ipc {
namespace {

[[nodiscard]] std::uint64_t GeneratePayloadToken() {
  thread_local std::mt19937_64 gen{std::random_device{}()};
  std::uniform_int_distribution<std::uint64_t> dist(1, std::numeric_limits<std::uint64_t>::max());
  return dist(gen);
}

}  // namespace

std::string LargeControlPayloadShmTopicForSocket(const std::string& resolved_socket_path) {
  const std::string instance_id = IpcChannelSelector::ShmInstanceIdFromSocketPath(resolved_socket_path);
  return IpcChannelSelector::ResolveTopicName(
      IpcChannelSelector::TopicName("ipc", "uds_control_payload_" + instance_id));
}

bool PrepareOutboundUdsControlPayload(boat::v1::UdsControlMessage* message, ShmPublisher<ShmPayloadSample>* publisher,
                                      const std::string& shm_payload_topic) {
  if (message == nullptr) {
    return false;
  }
  const std::string& payload = message->payload_bytes();
  if (payload.empty()) {
    return true;
  }
  if (IpcChannelSelector::SelectChannel(payload.size()) != IpcChannel::kSharedMemory) {
    return true;
  }
  if (publisher == nullptr || !publisher->IsOpen()) {
    return false;
  }
  const std::uint64_t token = GeneratePayloadToken();
  ShmPayloadSample sample{};
  sample.payload_token = token;
  if (!sample.SetFromString(payload)) {
    return false;
  }
  publisher->Publish(sample);
  message->set_shm_payload_topic(shm_payload_topic);
  message->set_shm_payload_token(token);
  message->clear_payload_bytes();
  return true;
}

bool ResolveInboundUdsControlPayload(boat::v1::UdsControlMessage* message, const std::string& expected_shm_payload_topic,
                                     const std::function<bool(std::uint64_t payload_token, std::string& out)>&
                                         receive_shm_payload) {
  if (message == nullptr) {
    return false;
  }
  if (message->shm_payload_topic().empty()) {
    return true;
  }
  if (message->shm_payload_topic() != expected_shm_payload_topic || message->shm_payload_token() == 0U) {
    return false;
  }
  std::string restored;
  if (!receive_shm_payload(message->shm_payload_token(), restored)) {
    return false;
  }
  message->set_payload_bytes(restored);
  message->clear_shm_payload_topic();
  message->clear_shm_payload_token();
  return true;
}

}  // namespace boat::ipc
