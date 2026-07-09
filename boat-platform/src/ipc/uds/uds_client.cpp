#include "ipc/uds/uds_client.h"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdio>

#include "ipc/ipc_channel_selector.h"
#include "ipc/ipc_payload_dispatch.h"
#include "ipc/uds/uds_framing.h"

namespace boat::ipc {

namespace {

void PopulateCommand(boat::v1::UdsControlMessage* msg, UdsCommand cmd) {
  switch (cmd) {
    case UdsCommand::START:
      msg->mutable_start();
      break;
    case UdsCommand::PAUSE:
      msg->mutable_pause();
      break;
    case UdsCommand::STEP:
      msg->mutable_step();
      break;
    case UdsCommand::RESET:
      msg->mutable_reset();
      break;
    case UdsCommand::STOP:
      msg->mutable_stop();
      break;
    case UdsCommand::INJECT_FAULT:
      msg->mutable_inject_fault();
      break;
    case UdsCommand::QUERY_STATE:
      msg->mutable_query_state();
      break;
  }
}

}  // namespace

bool UdsClient::EnsureLargePayloadPublisher() {
  const std::string shm_topic = IpcChannelSelector::TopicName(
      "ipc", "uds_control_payload_" + IpcChannelSelector::ShmInstanceIdFromSocketPath(resolved_socket_path_));
  if (!large_payload_publisher_.has_value()) {
    large_payload_publisher_.emplace(shm_topic);
  }
  if (!large_payload_publisher_->IsOpen()) {
    return large_payload_publisher_->Open();
  }
  return true;
}

bool UdsClient::Connect(const std::string& socket_path) {
  Disconnect();
  fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd_ < 0) {
    return false;
  }

  const std::string resolved_socket_path = IpcChannelSelector::ResolveSocketPath(socket_path);
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", resolved_socket_path.c_str());
  if (::connect(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
    Disconnect();
    return false;
  }
  resolved_socket_path_ = resolved_socket_path;
  large_payload_shm_topic_ = LargeControlPayloadShmTopicForSocket(resolved_socket_path_);
  return true;
}

boat::v1::UdsControlResponse UdsClient::SendCommand(UdsCommand cmd, const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  PopulateCommand(&message, cmd);
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

boat::v1::UdsControlResponse UdsClient::SendMessage(const boat::v1::UdsControlMessage& message) {
  boat::v1::UdsControlResponse response;
  if (fd_ < 0) {
    response.set_ok(false);
    response.set_message("not connected");
    return response;
  }

  boat::v1::UdsControlMessage wire_message(message);
  ShmPublisher<ShmPayloadSample>* shm_publisher = nullptr;
  if (!wire_message.payload_bytes().empty() &&
      IpcChannelSelector::SelectChannel(wire_message.payload_bytes().size()) == IpcChannel::kSharedMemory) {
    if (!EnsureLargePayloadPublisher()) {
      response.set_ok(false);
      response.set_message("failed to open large payload shm publisher");
      return response;
    }
    shm_publisher = &large_payload_publisher_.value();
  }
  if (!PrepareOutboundUdsControlPayload(&wire_message, shm_publisher, large_payload_shm_topic_)) {
    response.set_ok(false);
    response.set_message("failed to prepare outbound ipc payload");
    return response;
  }

  std::string out;
  wire_message.SerializeToString(&out);
  if (!WriteFrame(fd_, out)) {
    response.set_ok(false);
    response.set_message("write failed");
    return response;
  }

  std::string in;
  if (!ReadFrame(fd_, in) || !response.ParseFromString(in)) {
    response.Clear();
    response.set_ok(false);
    response.set_message("read failed");
  }
  return response;
}

boat::v1::UdsControlResponse UdsClient::SendStepCommand(uint32_t ticks, const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  message.mutable_step()->set_ticks(ticks);
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

boat::v1::UdsControlResponse UdsClient::SendInjectFaultCommand(const std::string& fault_payload,
                                                               const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  message.mutable_inject_fault()->set_payload(fault_payload);
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

boat::v1::UdsControlResponse UdsClient::SendQueryStateCommand(const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  message.mutable_query_state();
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

void UdsClient::Disconnect() {
  if (large_payload_publisher_.has_value()) {
    large_payload_publisher_->Close();
    large_payload_publisher_.reset();
  }
  resolved_socket_path_.clear();
  large_payload_shm_topic_.clear();
  if (fd_ >= 0) {
    ::shutdown(fd_, SHUT_RDWR);
    ::close(fd_);
    fd_ = -1;
  }
}

}  // namespace boat::ipc
