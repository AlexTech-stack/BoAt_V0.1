#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include "boat/v1/control.pb.h"
#include "ipc/shm/shm_payload_sample.h"
#include "ipc/shm/shm_publisher.h"
#include "ipc/uds/uds_types.h"

namespace boat::ipc {

class UdsClient {
 public:
  bool Connect(const std::string& socket_path);
  boat::v1::UdsControlResponse SendMessage(const boat::v1::UdsControlMessage& message);
  boat::v1::UdsControlResponse SendCommand(UdsCommand cmd, const std::string& payload_bytes);
  boat::v1::UdsControlResponse SendStepCommand(uint32_t ticks, const std::string& payload_bytes = {});
  boat::v1::UdsControlResponse SendInjectFaultCommand(const std::string& fault_payload,
                                                      const std::string& payload_bytes = {});
  boat::v1::UdsControlResponse SendQueryStateCommand(const std::string& payload_bytes = {});
  void Disconnect();

 private:
  [[nodiscard]] bool EnsureLargePayloadPublisher();

  int fd_{-1};
  std::string resolved_socket_path_;
  std::string large_payload_shm_topic_;
  std::optional<ShmPublisher<ShmPayloadSample>> large_payload_publisher_;
};

}  // namespace boat::ipc
